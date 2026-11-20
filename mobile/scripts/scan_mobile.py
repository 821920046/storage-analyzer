#!/usr/bin/env python3
"""Read-only mobile storage scanner (Android + iOS).

Auto-detects platform and privilege level, then scans only what is actually
accessible from the current process and emits ONE JSON blob to stdout for the
agent to interpret. Capability is reported explicitly so the agent can degrade
gracefully instead of promising cleanup it cannot perform.

STRICTLY READ-ONLY: never creates, moves, modifies or deletes anything.

Capability tiers
----------------
full     : Android root (uid 0). Can enumerate and clean per-app cache dirs.
limited  : Android non-root with ADB authorization (adb shell available), or
           public-storage-only access. Can use `pm trim-caches` (system-wide)
           and inspect public Download/DCIM; cannot touch other apps' private
           /data/data/<pkg>/cache without root.
guide    : iOS sandbox (or no usable privilege). Third-party code cannot read
           or delete another app's container. Output is analysis + guidance
           only; Safari cache and per-app cache need user action in Settings.

Output shape:
{
  "generated_at", "scan_seconds", "platform", "platform_detail",
  "capabilities": {root, adb, adb_device, tier, notes:[...]},
  "system": {os, build, device, user, home, disk_total, disk_used, disk_free,
             disks:[...], safe_roots:[...]},
  "groups": { "<group>": [{name, path, size_kb, size_h, ...}] },
  "limitations": [ ... ]
}
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")

TOP_N = 40
MIN_KB = 25 * 1024          # ignore entries below 25 MB
APP_CACHE_MIN_KB = 10 * 1024  # per-app cache floor (root scan)


def human(kb):
    n = float(kb) * 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{int(n)} {unit}"
        n /= 1024


def run(cmd, timeout=20):
    """Run a read-only command, return stripped stdout or '' on any failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def dir_size_bytes(path, _depth=0):
    """Recursive size in bytes via os.scandir. Skips symlinks / unreadable.

    Guarded by a depth cap to avoid pathological recursion on huge trees.
    """
    if _depth > 32:
        return 0
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                    elif e.is_dir(follow_symlinks=False):
                        total += dir_size_bytes(e.path, _depth + 1)
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    return total


def size_children(path, min_kb=MIN_KB, top_n=TOP_N, extra=None):
    """Size every immediate child of `path`; return sorted, capped results."""
    if not path or not os.path.isdir(path):
        return []
    results = []
    try:
        entries = sorted(os.listdir(path))
    except (PermissionError, OSError):
        return [{"name": "(permission denied)", "path": path,
                 "size_kb": 0, "size_h": "?", "denied": True}]
    for name in entries:
        child = os.path.join(path, name)
        if os.path.islink(child):
            continue
        try:
            kb = (dir_size_bytes(child) if os.path.isdir(child)
                  else os.path.getsize(child)) // 1024
        except (PermissionError, OSError):
            continue
        if kb < min_kb:
            continue
        item = {"name": name, "path": child, "size_kb": kb, "size_h": human(kb)}
        if extra:
            item.update(extra(name, child))
        results.append(item)
    results.sort(key=lambda r: r["size_kb"], reverse=True)
    if len(results) > top_n:
        tail = results[top_n:]
        tail_kb = sum(r["size_kb"] for r in tail)
        results = results[:top_n] + [{
            "name": f"其他 {len(tail)} 项", "path": path,
            "size_kb": tail_kb, "size_h": human(tail_kb),
            "summary": True, "count": len(tail),
        }]
    return results


# ----------------------------------------------------------------------
# Platform + capability detection
# ----------------------------------------------------------------------
def detect_platform():
    """Return (platform, detail). platform in {android, ios, unsupported}."""
    # Android: Termux / root shell exposes these markers.
    if (os.environ.get("ANDROID_ROOT") or os.environ.get("TERMUX_VERSION")
            or os.path.exists("/system/build.prop")):
        prop = run(["getprop", "ro.build.version.release"]) or ""
        detail = f"Android {prop}".strip()
        return "android", detail
    # iOS: Pythonista / a-Shell / iSH. darwin kernel but no /Applications,
    # home under /private/var/mobile, or iOS-only dirs present.
    if sys.platform == "darwin":
        ios_markers = [
            os.path.exists("/var/mobile/Containers"),
            "/private/var/mobile" in HOME,
            os.environ.get("PYTHONISTA_BUNDLE_ID"),
        ]
        if any(ios_markers) or not os.path.isdir("/Applications"):
            return "ios", "iOS (sandboxed Python)"
    # iSH on iOS reports linux; detect iOS filesystem without Android markers.
    if sys.platform == "linux" and os.path.exists("/var/mobile/Containers"):
        return "ios", "iOS (iSH)"
    return "unsupported", sys.platform


def detect_capabilities(platform):
    caps = {"root": False, "adb": False, "adb_device": False,
            "tier": "guide", "notes": []}
    try:
        caps["root"] = (os.geteuid() == 0)
    except AttributeError:
        pass

    adb = shutil.which("adb")
    caps["adb"] = bool(adb)
    if adb:
        devices = run([adb, "devices"], timeout=15)
        # 放行尾随空格/CR，并容忍 "device product:xxx model:yyy" 这类带信息的行
        caps["adb_device"] = bool(re.search(r"^\S+\s+device\b", devices, re.M))

    if platform == "android":
        if caps["root"]:
            caps["tier"] = "full"
            caps["notes"].append("已获取 root：可枚举并清理各 App 的 cache 子目录。")
        elif caps["adb_device"]:
            caps["tier"] = "limited"
            caps["notes"].append("ADB 已授权：可用 pm trim-caches 做系统级缓存清理；"
                                 "逐 App 的 /data/data/<pkg>/cache 仍需 root。")
        else:
            caps["tier"] = "limited"
            caps["notes"].append("无 root / 未授权 ADB：只能读取公共存储"
                                 "（Download/DCIM 等）与 Termux 自身缓存；"
                                 "其他 App 私有缓存不可访问。")
    elif platform == "ios":
        caps["tier"] = "guide"
        caps["notes"].append("iOS 沙盒：第三方代码无法读取或删除其他 App 的容器，"
                             "也不存在系统级缓存清理 API。")
        caps["notes"].append("仅能分析自身沙盒 + Safari 缓存引导，"
                             "其余需在「设置 → 通用 → iPhone 储存空间」或各 App 内手动清理。")
    else:
        caps["notes"].append("未识别的移动端环境，仅做最基本的目录统计。")
    return caps


# ----------------------------------------------------------------------
# System info
# ----------------------------------------------------------------------
def disk_usage_of(path):
    try:
        t, u, f = shutil.disk_usage(path)
        return human(t // 1024), human(u // 1024), human(f // 1024)
    except Exception:
        return "?", "?", "?"


def system_info_android(caps):
    info = {
        "os": "Android " + (run(["getprop", "ro.build.version.release"]) or "?"),
        "build": run(["getprop", "ro.build.display.id"]),
        "device": run(["getprop", "ro.product.model"]),
        "user": os.environ.get("USER", ""),
        "home": HOME,
    }
    data_root = "/data" if os.path.isdir("/data") else (os.environ.get("EXTERNAL_STORAGE") or "/sdcard")
    total, used, free = disk_usage_of(data_root)
    info["disk_total"], info["disk_used"], info["disk_free"] = total, used, free
    info["disk_name"] = data_root
    info["filesystem"] = "ext4/f2fs"
    info["purgeable"] = ""
    disks = []
    for root in ("/data", "/sdcard", "/storage/emulated/0"):
        if os.path.exists(root):
            t, u, f = disk_usage_of(root)
            disks.append({"name": root, "total": t, "used": u, "free": f})
    info["disks"] = disks
    # safe roots = prefixes the cleanup tool is willing to touch
    roots = [HOME]
    for p in ("/data/data", "/sdcard/Android/data", "/data/local/tmp",
              "/sdcard/Download", "/cache"):
        if os.path.isdir(p):
            roots.append(p)
    info["safe_roots"] = sorted(set(os.path.realpath(r) for r in roots))
    return info


def system_info_ios():
    info = {
        "os": "iOS",
        "build": "",
        "device": os.environ.get("SIMULATOR_MODEL_IDENTIFIER", ""),
        "user": os.environ.get("USER", ""),
        "home": HOME,
        "filesystem": "APFS",
        "purgeable": "",
    }
    total, used, free = disk_usage_of(HOME)
    info["disk_total"], info["disk_used"], info["disk_free"] = total, used, free
    info["disk_name"] = "App 沙盒"
    info["disks"] = [{"name": "App 沙盒", "total": total, "used": used, "free": free}]
    roots = [HOME]
    for p in (os.path.join(HOME, "Library", "Caches"), os.path.join(HOME, "Documents")):
        if os.path.isdir(p):
            roots.append(p)
    info["safe_roots"] = sorted(set(os.path.realpath(r) for r in roots))
    return info


# ----------------------------------------------------------------------
# Per-platform scans (all read-only)
# ----------------------------------------------------------------------
def scan_android(caps):
    groups = {}
    # 1) Per-app cache (root only)
    if caps["tier"] == "full":
        app_caches = []
        for base in ("/data/data", "/data/user/0"):
            if not os.path.isdir(base):
                continue
            try:
                pkgs = sorted(os.listdir(base))
            except (PermissionError, OSError):
                continue
            for pkg in pkgs:
                cache = os.path.join(base, pkg, "cache")
                if not os.path.isdir(cache):
                    continue
                kb = dir_size_bytes(cache) // 1024
                if kb < APP_CACHE_MIN_KB:
                    continue
                app_caches.append({"name": pkg, "path": cache,
                                   "size_kb": kb, "size_h": human(kb),
                                   "package": pkg, "cache_path": cache})
        app_caches.sort(key=lambda r: r["size_kb"], reverse=True)
        groups["app_caches"] = app_caches[:TOP_N]

        # External app caches reachable with root
        ext = []
        base = "/sdcard/Android/data"
        if os.path.isdir(base):
            try:
                for pkg in sorted(os.listdir(base)):
                    for sub in ("cache", "files"):
                        p = os.path.join(base, pkg, sub)
                        if not os.path.isdir(p):
                            continue
                        kb = dir_size_bytes(p) // 1024
                        if kb < APP_CACHE_MIN_KB:
                            continue
                        ext.append({"name": f"{pkg}/{sub}", "path": p,
                                    "size_kb": kb, "size_h": human(kb),
                                    "package": pkg, "cache_path": p})
            except (PermissionError, OSError):
                pass
        ext.sort(key=lambda r: r["size_kb"], reverse=True)
        groups["external_app_data"] = ext[:TOP_N]

    # 2) Public storage (readable without root)
    sdcard = None
    for cand in ("/sdcard", "/storage/emulated/0", os.environ.get("EXTERNAL_STORAGE", "")):
        if cand and os.path.isdir(cand):
            sdcard = cand
            break
    if sdcard:
        groups["downloads"] = size_children(os.path.join(sdcard, "Download"))
        groups["media"] = []
        for d in ("DCIM", "Pictures", "Movies", "Music"):
            groups["media"] += size_children(os.path.join(sdcard, d),
                                             min_kb=100 * 1024, top_n=12)
        groups["media"].sort(key=lambda r: r["size_kb"], reverse=True)
        groups["media"] = groups["media"][:TOP_N]

    # 3) Runtime / developer caches owned by this shell (Termux)
    local_caches = []
    candidates = [
        os.path.join(HOME, ".cache"), os.path.join(HOME, ".npm"),
        os.path.join(HOME, ".gradle"), os.path.join(HOME, ".m2"),
        os.path.join(HOME, ".local", "share", "pip"),
        os.environ.get("PREFIX") and os.path.join(os.environ["PREFIX"], "cache"),
        "/data/local/tmp", "/cache",
    ]
    for p in candidates:
        if p and os.path.isdir(p):
            kb = dir_size_bytes(p) // 1024
            if kb >= MIN_KB:
                local_caches.append({"name": os.path.basename(p.rstrip("/")) or p,
                                     "path": p, "size_kb": kb, "size_h": human(kb)})
    local_caches.sort(key=lambda r: r["size_kb"], reverse=True)
    groups["runtime_caches"] = local_caches
    return groups


def scan_ios():
    groups = {}
    groups["app_caches"] = size_children(os.path.join(HOME, "Library", "Caches"))
    groups["documents"] = size_children(os.path.join(HOME, "Documents"))
    groups["app_support"] = size_children(os.path.join(HOME, "Library", "Application Support"))
    return groups


def build_limitations(caps, platform):
    out = list(caps["notes"])
    if platform == "android" and caps["tier"] != "full":
        out.append("未能枚举其他 App 的私有缓存目录——扫描结果只覆盖公共存储与当前运行环境，"
                   "不代表整机全貌，报告中的可释放量为下限。")
    if platform == "ios":
        out.append("iOS 无官方的跨 App 缓存清理接口；任何声称能清理其他 App 缓存的工具"
                   "都依赖企业证书/越狱，存在安全与合规风险，不建议使用。")
    return out


def main():
    started = time.time()
    platform, detail = detect_platform()
    caps = detect_capabilities(platform)
    if platform == "android":
        system = system_info_android(caps)
        groups = scan_android(caps)
    elif platform == "ios":
        system = system_info_ios()
        groups = scan_ios()
    else:
        print(json.dumps({
            "error": "unsupported_platform", "platform": detail,
            "message": "scan_mobile.py 仅供 Android / iOS 的移动端 Python 环境使用。",
        }, ensure_ascii=False, indent=2))
        return

    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scan_seconds": round(time.time() - started, 1),
        "platform": platform,
        "platform_detail": detail,
        "capabilities": caps,
        "system": system,
        "groups": {k: v for k, v in groups.items() if v},
        "limitations": build_limitations(caps, platform),
        "scan_config": {"top_n": TOP_N, "min_kb": MIN_KB},
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
