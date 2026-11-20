#!/usr/bin/env python3
"""Guarded, capability-aware cleanup executor for mobile (Android root / ADB).

Companion to scan_mobile.py. It will NEVER act on iOS (sandbox) — it only
prints guidance there. On Android it operates strictly within an allowlist of
cache paths supplied by an analysis JSON, and degrades to what the detected
privilege actually allows.

STRICTLY scoped (hardened after adversarial review):
- tier gate   : destructive actions are ONLY possible when the analysis JSON
                declares capabilities.tier == "full" (Android root).
- cache gate  : a path is deletable only if its basename is a KNOWN CACHE name
                AND its parent looks like an app package dir. Hard-denied names
                (databases/files/shared_prefs/MicroMsg/...) are always rejected,
                even if an agent lists them in green[].trash_paths.
- path gate   : realpath must stay under system.safe_roots AND under an
                Android app-data prefix. Symlinks are resolved before checks.
- clear gate  : `pm clear <pkg>` requires the package to be in the analysis
                JSON's `approved_clear_packages` AND match a strict package
                regex; CLI-only names are rejected.
- ios         : no destructive action at all; prints manual steps to stdout.

Usage:
    # 1) Always preview first (default = dry-run, prints what WOULD happen)
    python3 cleanup_mobile.py analysis.json

    # 2) Execute after the user has confirmed, scoped to a tier
    python3 cleanup_mobile.py analysis.json --apply

    # 3) Optional: system-wide cache trim via ADB (no root needed)
    python3 cleanup_mobile.py analysis.json --trim-caches 2G --apply

Safety invariants (do not weaken without adversarial re-review):
- --apply is mandatory; without it, NOTHING is written.
- An analysis JSON that omits / mislabels capabilities.tier cannot delete.
- Listing a non-cache path in green[] is never sufficient to delete it.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HOME = os.path.realpath(os.path.expanduser("~"))

# 只接受这些"缓存"目录名；其他一概拒绝
CACHE_NAMES = {
    "cache", "caches", "code_cache", "code_cache_secondary",
    "http-cache", "image_cache", "disk_cache", "gpu_cache",
    "ShaderCache", "GrShaderCache", "Crashpad", "npm-cache", "pip-cache",
}
# 这些目录名一旦出现在路径任一层，立即拒绝（含用户数据/核心配置）
HARD_DENY_PARTS = {
    "databases", "files", "shared_prefs", "MicroMsg", "msg", "message",
    "documents", "Documents", "DCIM", "Pictures", "Movies", "Download",
    "accounts", "auth", "tokens", "wallet", "sp", "blob", "datastore",
}
# Android 应用数据前缀：只有在这些前缀下的 cache 才允许删
APP_DATA_PREFIXES = ("/data/data/", "/data/user/0/", "/sdcard/Android/data/")
# 允许的缓存根（工具自身/系统临时），仍需 basename 属于缓存名
EXTRA_CACHE_ROOTS = ("/data/local/tmp", "/cache")
PKG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")


def real(p):
    return os.path.realpath(os.path.expanduser(p))


def human_kb(kb):
    n = float(kb) * 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{int(n)} {unit}"
        n /= 1024


def parse_size(text):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?B?)\s*", text or "", re.I)
    if not m:
        raise ValueError(f"无法解析大小: {text!r}（示例: 2G / 500M）")
    num = float(m.group(1))
    unit = m.group(2).upper().rstrip("B") or "B"
    mult = {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}[unit[0]]
    return int(num * mult)


def is_under(path, roots):
    # 统一用 / 比较，避免 Windows(\) 与 POSIX(/) 分隔符不一致导致误判
    p = path.replace("\\", "/").rstrip("/")
    for base in roots:
        b = base.replace("\\", "/").rstrip("/")
        if p == b or p.startswith(b + "/"):
            return True
    return False


def load_analysis(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("analysis JSON 顶层必须是对象")
    if not isinstance(data.get("system"), dict):
        raise ValueError("analysis JSON 缺少 system 对象")
    plat = data.get("platform") or data.get("system", {}).get("platform")
    return data, plat


def analysis_tier(data):
    caps = data.get("capabilities")
    tier = None
    if isinstance(caps, dict):
        tier = caps.get("tier")
    if not tier and isinstance(data.get("system"), dict):
        tier = data["system"].get("tier")
    return tier


def approved_clear_packages(data):
    vals = data.get("approved_clear_packages") or []
    if isinstance(vals, dict):
        vals = vals.get("packages") or []
    return set(v for v in vals if isinstance(v, str)) if isinstance(vals, list) else set()


def is_deletable_cache(ap, roots):
    """Return (ok, reason). HARD gates — no amount of agent labelling bypasses."""
    if not is_under(ap, roots):
        return False, "不在 safe_roots 内"
    # 硬拒名单：路径任一层含用户数据目录名
    parts = [p for p in ap.replace("\\", "/").split("/") if p]
    if any(part in HARD_DENY_PARTS for part in parts):
        return False, "路径含用户数据目录（硬拒）"
    # 末段必须是已知缓存名
    if parts and parts[-1] not in CACHE_NAMES:
        return False, f"末段 {parts[-1]!r} 不是已知缓存目录"
    # 必须位于 app-data 前缀或额外缓存根之下
    norm = ap.replace("\\", "/")
    in_app_data = any(norm.startswith(prefix) for prefix in APP_DATA_PREFIXES)
    in_extra = any(is_under(ap, [real(r)]) for r in EXTRA_CACHE_ROOTS if os.path.exists(r))
    if not (in_app_data or in_extra):
        return False, "不在 Android app-data / 已知缓存根下"
    # app-data 下要求形如 /data/data/<pkg>/cache（倒数第二段是包名）
    if in_app_data and len(parts) >= 4:
        pkg = parts[-2]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+", pkg):
            return False, f"父段 {pkg!r} 不像包名"
    return True, "OK"


def eligible_targets(data):
    """Yield (label, abspath, roots) for deletable green cache paths only."""
    roots = [real(r) for r in data.get("system", {}).get("safe_roots", [])] or [HOME]
    seen = set()
    for item in data.get("green", []) if isinstance(data.get("green"), list) else []:
        if not isinstance(item, dict):
            continue
        for p in item.get("trash_paths") or []:
            ap = real(p)
            if ap in seen:
                continue
            seen.add(ap)
            yield item.get("name", p), ap, roots


def delete_path(ap):
    if not os.path.exists(ap):
        return True, "已不存在"
    try:
        if os.path.isdir(ap) and not os.path.islink(ap):
            shutil.rmtree(ap)
        else:
            os.remove(ap)
        return True, "已删除"
    except Exception as e:
        return False, str(e)


def adb_available():
    return shutil.which("adb") is not None


def adb_trim(bytes_count):
    r = subprocess.run(["adb", "shell", "pm", "trim-caches", str(bytes_count)],
                       capture_output=True, text=True, timeout=300)
    return r.returncode, (r.stdout or r.stderr or "").strip()


def adb_clear(pkg):
    r = subprocess.run(["adb", "shell", "pm", "clear", pkg],
                       capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout or r.stderr or "").strip()


def ios_guidance():
    print("iOS 沙盒下无可用的程序化清理方式，请手动执行：")
    steps = [
        "设置 → 通用 → iPhone 储存空间：查看各 App 占用，系统在此提供「卸载 App」"
        "（保留文稿与数据）与「删除 App」选项。",
        "微信 / QQ：App 内 → 我 → 设置 → 通用 → 存储空间 → 清理缓存（勿直接删聊天数据库）。",
        "Safari：设置 → Safari 浏览器 → 清除历史记录与网站数据。",
        "照片：删除「最近删除」相簿以真正释放空间。",
        "视频/音乐类 App：在 App 内删除已下载内容，而非从文件系统删除。",
    ]
    for i, s in enumerate(steps, 1):
        print(f"  {i}. {s}")
    print("\n提示：定期备份后「卸载重装」是 iOS 上释放单个 App 缓存最彻底的方式。")


def main():
    ap = argparse.ArgumentParser(description="移动端受保护清理执行器")
    ap.add_argument("analysis", help="analysis JSON 路径")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认仅预览）")
    ap.add_argument("--trim-caches", metavar="SIZE", help="通过 ADB 系统级清理缓存，如 2G")
    ap.add_argument("--allow-clear", metavar="PKG", action="append", default=[],
                    help="允许 pm clear 的包名（须同时在 analysis 的 approved_clear_packages 中）")
    args = ap.parse_args()

    try:
        data, plat = load_analysis(args.analysis)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"✗ analysis JSON 无效: {e}")
        return

    if plat == "ios":
        ios_guidance()
        return
    if plat != "android":
        print("未知平台，不做任何操作。")
        return

    tier = analysis_tier(data)
    print(f"=== 移动端清理计划（tier={tier or '未声明'}，仅绿灯纯缓存）===")
    if not args.apply:
        print("[预览模式] 加 --apply 才会真正执行。\n")

    # tier 硬门槛：非 full 一律不执行任何删除
    if tier != "full":
        print("✗ 权限档位非 full（Android root），拒绝执行任何删除。")
        print("  当前档位只允许分析与手动引导；请勿在未声明 tier=full 的 analysis 上尝试清理。")
        if args.apply:
            return

    done = skipped = 0
    for label, target, roots in eligible_targets(data):
        ok, reason = is_deletable_cache(target, roots)
        if not ok:
            print(f"  拒绝（{reason}）: {label} -> {target}")
            skipped += 1
            continue
        if not os.path.exists(target):
            print(f"  跳过（不存在）: {label} -> {target}")
            skipped += 1
            continue
        if args.apply and tier == "full":
            ok2, msg = delete_path(target)
            print(f"  {'✓' if ok2 else '✗'} {label}: {msg} -> {target}")
            done += 1 if ok2 else 0
        else:
            print(f"  将删除: {label} -> {target}")

    # ADB trim-caches 是系统级安全 API，独立于 tier，但仍需显式 --apply
    if args.trim_caches:
        if not adb_available():
            print("  ✗ 未找到 adb，跳过 trim-caches。")
        else:
            try:
                nbytes = parse_size(args.trim_caches)
            except ValueError as e:
                print(f"  ✗ {e}")
            else:
                if args.apply:
                    rc, out = adb_trim(nbytes)
                    print(f"  {'✓' if rc == 0 else '✗'} pm trim-caches {args.trim_caches}: {out}")
                else:
                    print(f"  将执行: adb shell pm trim-caches {nbytes} ({args.trim_caches})")

    approved = approved_clear_packages(data)
    for pkg in args.allow_clear:
        if not PKG_RE.fullmatch(pkg) or pkg not in approved:
            print(f"  ✗ 拒绝 pm clear {pkg!r}：包名非法或不在 analysis 的 approved_clear_packages 中")
        elif not adb_available():
            print(f"  ✗ 未找到 adb，跳过 pm clear {pkg}。")
        elif args.apply:
            rc, out = adb_clear(pkg)
            print(f"  {'✓' if rc == 0 else '✗'} pm clear {pkg}: {out}")
        else:
            print(f"  将执行（危险，清用户数据）: adb shell pm clear {pkg}")

    print(f"\n完成：删除 {done} 项，拒绝/跳过 {skipped} 项"
          + ("" if args.apply else "（预览，未做任何更改）"))


if __name__ == "__main__":
    main()
