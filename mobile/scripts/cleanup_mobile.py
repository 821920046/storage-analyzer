#!/usr/bin/env python3
"""Guarded, capability-aware cleanup executor for mobile (Android root / ADB).

Companion to scan_mobile.py. It will NEVER act on iOS (sandbox) — it only
prints guidance there. On Android it operates strictly within an allowlist of
cache paths supplied by an analysis JSON, and degrades to what the detected
privilege actually allows.

STRICTLY scoped:
- root  : may delete only the exact per-app `cache` paths listed as green items.
- adb   : may run `pm trim-caches <bytes>` (system-wide, safe) ONLY. It will not
          run `pm clear <pkg>` (that wipes user data) unless --allow-clear is
          given AND the package was explicitly listed as approved.
- ios   : no destructive action at all; prints manual steps to stdout.

Usage:
    # 1) Always preview first (default = dry-run, prints what WOULD happen)
    python3 cleanup_mobile.py analysis.json

    # 2) Execute after the user has confirmed, scoped to a tier
    python3 cleanup_mobile.py analysis.json --apply

    # 3) Optional: system-wide cache trim via ADB (no root needed)
    python3 cleanup_mobile.py analysis.json --trim-caches 2G --apply

Safety:
- Requires an explicit analysis JSON whose system.platform is android.
- Every path must be realpath-resolved and under system.safe_roots.
- Only green-tier items (pure cache) are eligible for deletion.
- --apply is mandatory to perform any action; without it, nothing is written.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HOME = os.path.realpath(os.path.expanduser("~"))


def real(p):
    return os.path.realpath(os.path.expanduser(p))


def human(nbytes):
    kb = nbytes / 1024
    return human_kb(kb)


def human_kb(kb):
    n = float(kb) * 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{int(n)} {unit}"
        n /= 1024


def parse_size(text):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?B?)\s*", text or "", re.I)
    if not m:
        raise ValueError(f"无法解析大小: {text!r}")
    num = float(m.group(1))
    unit = m.group(2).upper().rstrip("B") or "B"
    mult = {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}[unit[0]]
    return int(num * mult)


def is_under(path, roots):
    for base in roots:
        if path == base or path.startswith(base + os.sep):
            return True
    return False


def load_analysis(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # platform 可能在顶层或 system 内，两者都接受
    plat = data.get("platform") or data.get("system", {}).get("platform")
    return data, plat


def eligible_targets(data):
    """Yield (label, abspath) for green-tier cache paths only."""
    roots = [real(r) for r in data.get("system", {}).get("safe_roots", [])] or [HOME]
    seen = set()
    for item in data.get("green", []):
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
                    help="允许 pm clear 的包名（会清用户数据，需显式逐个授权）")
    args = ap.parse_args()

    data, plat = load_analysis(args.analysis)

    if plat == "ios":
        ios_guidance()
        return
    if plat != "android":
        print("未知平台，不做任何操作。")
        return

    print("=== 移动端清理计划（仅处理绿灯纯缓存）===")
    if not args.apply:
        print("[预览模式] 加 --apply 才会真正执行。\n")

    targets = list(eligible_targets(data))
    if not targets:
        print("  没有可清理的绿灯项（analysis JSON 的 green[].trash_paths 为空）。")
    done = skipped = 0
    for label, target, roots in targets:
        if not is_under(target, roots):
            print(f"  跳过（越界）: {label} -> {target}")
            skipped += 1
            continue
        if not os.path.exists(target):
            print(f"  跳过（不存在）: {label} -> {target}")
            skipped += 1
            continue
        if args.apply:
            ok, msg = delete_path(target)
            print(f"  {'✓' if ok else '✗'} {label}: {msg} -> {target}")
            done += 1 if ok else 0
        else:
            print(f"  将删除: {label} -> {target}")

    if args.trim_caches:
        if not adb_available():
            print("  ✗ 未找到 adb，跳过 trim-caches。")
        else:
            nbytes = parse_size(args.trim_caches)
            if args.apply:
                rc, out = adb_trim(nbytes)
                print(f"  {'✓' if rc == 0 else '✗'} pm trim-caches {args.trim_caches}: {out}")
            else:
                print(f"  将执行: adb shell pm trim-caches {nbytes} ({args.trim_caches})")

    for pkg in args.allow_clear:
        if not adb_available():
            print(f"  ✗ 未找到 adb，跳过 pm clear {pkg}。")
        elif args.apply:
            rc, out = adb_clear(pkg)
            print(f"  {'✓' if rc == 0 else '✗'} pm clear {pkg}: {out}")
        else:
            print(f"  将执行（危险，清用户数据）: adb shell pm clear {pkg}")

    print(f"\n完成：删除 {done} 项，跳过 {skipped} 项"
          + ("" if args.apply else "（预览，未做任何更改）"))


if __name__ == "__main__":
    main()
