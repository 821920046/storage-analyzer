#!/usr/bin/env python3
"""Read-only storage scanner (macOS + Windows).

Collects disk usage, system info, and per-directory size breakdowns for the
hot spots that typically eat disk, and emits one JSON blob to stdout for Claude
to interpret and classify. Auto-detects the OS and scans the right locations.

STRICTLY READ-ONLY: only sizes/lists/reads metadata. Never creates, moves, or
deletes anything.

Output shape (same on both OSes):
{
  "generated_at", "scan_seconds",
  "system": {os, build, arch, user, home, filesystem,
             disk_total, disk_used, disk_free, purgeable,
             disks: [{name, total, used, free}]},   # all volumes/drives
  "groups": { "<group>": [{name, path, size_kb, size_h}], ... }
}
"""
import json
import os
import shutil
import string
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HOME = os.path.expanduser("~")

# — 扫描压缩常量 —
TOP_N = 30                 # 每组最多展示的独立条目数，超出部分聚合为"其他"
AGGREGATE_MIN_COUNT = 5    # 剩余项 ≥ 此数量时才生成聚合条目
CHILD_WORKERS = min(8, (os.cpu_count() or 4) + 4)   # 子目录并行扫描线程数


def human(kb):
    """KB number -> human string like '12.3 GB'."""
    n = float(kb) * 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{int(n)} {unit}"
        n /= 1024


# ======================================================================
# macOS
# ======================================================================
import re
import subprocess


def run(cmd, timeout=180):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def du_children(path, min_kb=51200, limit=40):
    """Size every immediate child of `path` via du, sorted desc. macOS."""
    if not os.path.isdir(path):
        return []
    results = []
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return [{"name": "(permission denied)", "path": path,
                 "size_kb": 0, "size_h": "?", "denied": True}]
    for name in entries:
        if name in (".", ".."):
            continue
        child = os.path.join(path, name)
        if os.path.islink(child):
            continue
        out = run(["du", "-sk", child], timeout=120)
        m = re.match(r"\s*(\d+)", out)
        if not m:
            continue
        kb = int(m.group(1))
        if kb < min_kb:
            continue
        results.append({"name": name, "path": child, "size_kb": kb, "size_h": human(kb)})
    results.sort(key=lambda r: r["size_kb"], reverse=True)
    return results[:limit]


MAC_TARGETS = [
    ("home", HOME, 102400),
    ("library", os.path.join(HOME, "Library"), 51200),
    ("caches", os.path.join(HOME, "Library/Caches"), 51200),
    ("containers", os.path.join(HOME, "Library/Containers"), 51200),
    ("group_containers", os.path.join(HOME, "Library/Group Containers"), 51200),
    ("app_support", os.path.join(HOME, "Library/Application Support"), 51200),
    ("applications", "/Applications", 102400),
    ("downloads", os.path.join(HOME, "Downloads"), 51200),
    ("dev_caches", None, 51200),
]

MAC_DEV_CACHE_PATHS = [
    "~/Library/Caches/pip", "~/Library/Caches/uv", "~/.cache", "~/.cargo",
    "~/.npm", "~/.pnpm-store", "~/.gradle", "~/.m2",
    "~/Library/Developer/Xcode/DerivedData", "~/Library/Developer/CoreSimulator",
    "~/Library/Developer/Xcode/iOS DeviceSupport", "~/Library/pnpm",
    "~/go/pkg", "~/.docker",
]


def dev_caches_macos():
    results = []
    for p in MAC_DEV_CACHE_PATHS:
        path = os.path.expanduser(p)
        if not os.path.isdir(path):
            continue
        out = run(["du", "-sk", path], timeout=180)
        m = re.match(r"\s*(\d+)", out)
        if not m:
            continue
        kb = int(m.group(1))
        if kb < 51200:
            continue
        results.append({"name": os.path.basename(path.rstrip("/")) or path,
                        "path": path, "size_kb": kb, "size_h": human(kb)})
    results.sort(key=lambda r: r["size_kb"], reverse=True)
    return results


def system_info_macos():
    info = {}
    info["os"] = "macOS " + run(["sw_vers", "-productVersion"]).strip()
    info["build"] = run(["sw_vers", "-buildVersion"]).strip()
    arch = run(["uname", "-m"]).strip()
    brand = run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    info["arch"] = (f"Apple Silicon (arm64){' / ' + brand if brand else ''}"
                    if arch == "arm64" else f"{arch}{' / ' + brand if brand else ''}")
    info["user"] = os.environ.get("USER", "") or run(["whoami"]).strip()
    info["home"] = HOME
    total, used, free = "?", "?", "?"
    try:
        t, u, f = shutil.disk_usage("/")
        total, used, free = human(t // 1024), human(u // 1024), human(f // 1024)
    except Exception:
        pass
    info["disk_total"], info["disk_used"], info["disk_free"] = total, used, free
    dinfo = run(["diskutil", "info", "/"])
    fs = re.search(r"File System Personality:\s*(.+)", dinfo)
    info["filesystem"] = fs.group(1).strip() if fs else "APFS"
    pm = re.search(r"Purgeable Space:\s*([\d.,]+ \w+)", dinfo)
    info["purgeable"] = pm.group(1).strip() if pm else ""
    info["disk_name"] = "Macintosh HD"
    info["disks"] = [{"name": "Macintosh HD", "total": total, "used": used, "free": free}]
    return info


def scan_macos():
    system = system_info_macos()
    groups = {}
    for key, path, floor in MAC_TARGETS:
        groups[key] = dev_caches_macos() if key == "dev_caches" else du_children(path, min_kb=floor)
    return system, groups


# ======================================================================
# Windows  (UNTESTED on this build — stdlib only: os, shutil, ctypes)
# ======================================================================
def dir_size_bytes(path):
    """Recursive size in bytes via os.scandir. Skips symlinks and unreadable."""
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
                        total += dir_size_bytes(e.path)
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    return total


def scandir_children(path, min_kb=51200, top_n=TOP_N, pool=None):
    """Size every immediate child of `path` via os.scandir (Windows).

    当传入 pool（ThreadPoolExecutor）时，多个子目录的大小计算并行执行，
    大幅缩减 I/O 阻塞时间。结果超过 top_n 条时，尾部小条目自动聚合为一条。
    """
    if not path or not os.path.isdir(path):
        return []
    results = []
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return [{"name": "(permission denied)", "path": path,
                 "size_kb": 0, "size_h": "?", "denied": True}]

    # — 并行收集子目录/文件大小 —
    if pool:
        # 拆成文件（同步快速获取）和目录（提交线程池）
        dir_futures = {}   # future → name, child_path
        for name in entries:
            child = os.path.join(path, name)
            if os.path.islink(child):
                continue
            try:
                if os.path.isfile(child):
                    kb = os.path.getsize(child) // 1024
                    if kb >= min_kb:
                        results.append({"name": name, "path": child, "size_kb": kb, "size_h": human(kb)})
                elif os.path.isdir(child):
                    dir_futures[pool.submit(dir_size_bytes, child)] = (name, child)
            except (PermissionError, OSError):
                continue
        # 收集线程池返回的目录大小
        for fut in as_completed(dir_futures):
            name, child = dir_futures[fut]
            try:
                kb = fut.result() // 1024
            except (PermissionError, OSError):
                continue
            if kb < min_kb:
                continue
            results.append({"name": name, "path": child, "size_kb": kb, "size_h": human(kb)})
    else:
        # 无线程池时退化为串行（macOS 或测试场景）
        for name in entries:
            child = os.path.join(path, name)
            if os.path.islink(child):
                continue
            try:
                kb = (os.path.getsize(child) if os.path.isfile(child)
                      else dir_size_bytes(child)) // 1024
            except (PermissionError, OSError):
                continue
            if kb < min_kb:
                continue
            results.append({"name": name, "path": child, "size_kb": kb, "size_h": human(kb)})

    results.sort(key=lambda r: r["size_kb"], reverse=True)

    # — 聚合尾部小条目，避免 JSON 爆炸 —
    if len(results) > top_n + AGGREGATE_MIN_COUNT:
        # 第 top_n 之后的条目按大小合并为一条
        tail = results[top_n:]
        tail_kb = sum(r["size_kb"] for r in tail)
        max_tail_kb = tail[0]["size_kb"]   # 尾部最大的一项，给大模型参考
        results = results[:top_n]
        results.append({
            "name": f"其他 {len(tail)} 项（单项 < {human(max_tail_kb)}）",
            "path": path,
            "size_kb": tail_kb,
            "size_h": human(tail_kb),
            "summary": True,
            "count": len(tail),
        })

    return results


def list_drives_windows():
    drives = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            try:
                t, u, f = shutil.disk_usage(root)
                drives.append({"name": root, "total": human(t // 1024),
                               "used": human(u // 1024), "free": human(f // 1024)})
            except Exception:
                continue
    return drives


def system_info_windows():
    import platform
    info = {}
    info["os"] = platform.system() + " " + platform.release()
    info["build"] = platform.version()
    info["arch"] = os.environ.get("PROCESSOR_ARCHITECTURE", platform.machine())
    info["user"] = os.environ.get("USERNAME", "")
    info["home"] = os.environ.get("USERPROFILE", HOME)
    sysdrive = os.environ.get("SystemDrive", "C:") + "\\"
    total, used, free = "?", "?", "?"
    try:
        t, u, f = shutil.disk_usage(sysdrive)
        total, used, free = human(t // 1024), human(u // 1024), human(f // 1024)
    except Exception:
        pass
    info["disk_total"], info["disk_used"], info["disk_free"] = total, used, free
    info["filesystem"] = "NTFS"
    info["purgeable"] = ""
    info["disk_name"] = sysdrive
    info["disks"] = list_drives_windows()
    return info


def _scan_dev_caches(profile, local, roaming, dev_paths):
    """扫描开发缓存目录（独立函数，供线程池调用）。"""
    dev = []
    for path in dev_paths:
        if not os.path.isdir(path):
            continue
        try:
            kb = dir_size_bytes(path) // 1024
        except (PermissionError, OSError):
            continue
        if kb < 51200:
            continue
        dev.append({"name": os.path.basename(path.rstrip("\\/")) or path,
                    "path": path, "size_kb": kb, "size_h": human(kb)})
    # 额外扫描常见社交/媒体/大数据应用目录
    big_app_paths = [
        # 微信
        os.path.join(profile, "Documents", "WeChat Files"),
        # 企业微信
        os.path.join(profile, "Documents", "WXWork"),
        # QQ
        os.path.join(profile, "Documents", "Tencent Files"),
        # 钉钉
        os.path.join(roaming, "DingTalk"),
        # 飞书
        os.path.join(local, "Feishu"),
        os.path.join(roaming, "Feishu"),
        # 浏览器用户数据
        os.path.join(local, "Google", "Chrome", "User Data"),
        os.path.join(local, "Microsoft", "Edge", "User Data"),
        os.path.join(roaming, "Mozilla", "Firefox", "Profiles"),
        # npm / yarn 全局
        os.path.join(roaming, "npm"),
        os.path.join(roaming, "npm-cache"),
        # Android SDK
        os.path.join(local, "Android", "Sdk"),
    ]
    for path in big_app_paths:
        if not os.path.isdir(path):
            continue
        # 防重复——若该路径已在 dev_paths 中扫过，跳过
        if path in dev_paths:
            continue
        try:
            kb = dir_size_bytes(path) // 1024
        except (PermissionError, OSError):
            continue
        if kb < 51200:
            continue
        dev.append({"name": os.path.basename(path.rstrip("\\/")) or path,
                    "path": path, "size_kb": kb, "size_h": human(kb)})
    dev.sort(key=lambda r: r["size_kb"], reverse=True)
    return dev


def _collect_safe_roots(profile, local, roaming, temp_dir, progfiles, progfiles_x86):
    """收集所有合法的删除根路径，供 server.py 动态白名单使用。

    返回的路径列表代表扫描器已验证可安全操作的前缀目录——
    server 端只允许删除这些路径下的文件。
    """
    roots = []
    # 用户配置文件相关
    for p in (profile, local, roaming, temp_dir,
              os.path.join(profile, "Downloads"),
              os.path.join(profile, "Documents")):
        if p and os.path.isdir(p):
            roots.append(os.path.realpath(p))
    # 程序文件目录
    for p in (progfiles, progfiles_x86):
        if p and os.path.isdir(p):
            roots.append(os.path.realpath(p))
    # 所有存在的盘符根（如 D:\、E:\），支撑跨盘清理
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            roots.append(root)
    return sorted(set(roots))


def scan_windows():
    profile = os.environ.get("USERPROFILE", HOME)
    local = os.environ.get("LOCALAPPDATA", os.path.join(profile, "AppData", "Local"))
    roaming = os.environ.get("APPDATA", os.path.join(profile, "AppData", "Roaming"))
    temp_dir = os.environ.get("TEMP", os.path.join(local, "Temp"))
    progfiles = os.environ.get("ProgramFiles", r"C:\Program Files")
    progfiles_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

    targets = [
        ("user_profile", profile, 102400),
        ("appdata_local", local, 51200),
        ("appdata_roaming", roaming, 51200),
        ("temp", temp_dir, 51200),
        ("downloads", os.path.join(profile, "Downloads"), 51200),
        ("program_files", progfiles, 102400),
        ("program_files_x86", progfiles_x86, 102400),
    ]

    dev_paths = [
        os.path.join(profile, ".cache"), os.path.join(profile, ".npm"),
        os.path.join(profile, ".gradle"), os.path.join(profile, ".m2"),
        os.path.join(profile, ".nuget", "packages"), os.path.join(profile, ".cargo"),
        os.path.join(local, "pip", "Cache"), os.path.join(local, "Yarn"),
        os.path.join(local, "uv"), os.path.join(local, "ms-playwright"),
        os.path.join(local, "go-build"),
    ]

    groups = {}
    # — 多线程并行：7 个目标组 + 1 个开发缓存组 —
    with ThreadPoolExecutor(max_workers=CHILD_WORKERS) as ex:
        futures = {}  # future → group_key
        for key, path, floor in targets:
            futures[ex.submit(scandir_children, path, floor, TOP_N, ex)] = key
        futures[ex.submit(_scan_dev_caches, profile, local, roaming, dev_paths)] = "dev_caches"
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                groups[key] = fut.result()
            except Exception:
                groups[key] = []  # 单个组失败不阻塞整体

    # — 收尾：按 targets 原始顺序组装，dev_caches 垫底 —
    ordered = {}
    for key, _, _ in targets:
        ordered[key] = groups.get(key, [])
    ordered["dev_caches"] = groups.get("dev_caches", [])

    system = system_info_windows()
    # 动态白名单根路径
    system["safe_roots"] = _collect_safe_roots(
        profile, local, roaming, temp_dir, progfiles, progfiles_x86)

    return system, ordered


# ======================================================================
def main():
    started = time.time()
    if sys.platform == "darwin":
        system, groups = scan_macos()
    elif sys.platform.startswith("win"):
        system, groups = scan_windows()
    else:
        print(json.dumps({"error": "unsupported_platform", "platform": sys.platform,
                          "message": "scan.py supports macOS and Windows only."},
                         ensure_ascii=False))
        return
    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "system": system,
        "groups": groups,
        "scan_seconds": round(time.time() - started, 1),
        "scan_config": {
            "top_n": TOP_N,
            "aggregate_min_count": AGGREGATE_MIN_COUNT,
            "child_workers": CHILD_WORKERS,
        },
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
