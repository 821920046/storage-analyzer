#!/usr/bin/env python3
"""ADB-backed per-app storage diagnosis for Android (NO root required).

This is the missing piece for honest Android diagnosis. It runs on the
*computer* with adb connected to a phone, and uses Android's own system
service to get per-package storage figures — something a non-root app cannot
do on-device.

Why this works (AOSP DiskStatsService.java, framework/base):
  `adb shell dumpsys diskstats` prints cached values read from
  /data/system/diskstats_cache.json, including FOUR parallel JSON arrays:
      Package Names:  ["com.tencent.mm", ...]
      App Sizes:      [123456, ...]        # KB, APK + optimized code + libs
      App Data Sizes: [789012, ...]        # KB, includes cache
      Cache Sizes:    [12345, ...]         # KB, cache subset
  The dump is guarded by checkDumpAndUsageStatsPermission(); the adb `shell`
  UID holds DUMP, so this works on a non-rooted device once ADB debugging is
  authorized. NOTE: figures come from a periodically-refreshed cache file and
  can be stale; treat as estimates, and prefer the phone's own Settings UI
  for the final word.

Optionally also queries `dumpsys usagestats` for last-used time (best effort,
device-dependent output) to support "unused big app" suggestions.

STRICTLY READ-ONLY: issues only dumpsys queries. Never clears or deletes.

Usage:
    python3 adb_diag.py > adb_diag.json
    python3 adb_diag.py --serial <device> > adb_diag.json
    python3 adb_diag.py --no-usage > adb_diag.json   # skip usagestats
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time

LABELS = {
    "Package Names:": "packages",
    "App Sizes:": "app_kb",
    "App Data Sizes:": "data_kb",
    "Cache Sizes:": "cache_kb",
}


def human(kb):
    n = float(kb or 0) * 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{int(n)} {unit}"
        n /= 1024


def adb_base(serial=None):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    return cmd


def adb_shell(args, serial=None, timeout=120):
    if not shutil.which("adb"):
        raise RuntimeError("未找到 adb，请先安装 Android platform-tools 并加入 PATH")
    cmd = adb_base(serial) + ["shell"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"adb 命令失败: {' '.join(cmd)} -> {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def parse_diskstats(text):
    """Parse the four labeled JSON arrays from `dumpsys diskstats`."""
    out = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        for label, key in LABELS.items():
            if stripped == label or stripped.startswith(label):
                # value is the remainder after the label (usually same line)
                value = stripped[len(label):].strip()
                if not value and i + 1 < len(lines):
                    value = lines[i + 1].strip()
                try:
                    out[key] = json.loads(value)
                except json.JSONDecodeError:
                    out[key] = None
                break
    required = ("packages", "app_kb", "data_kb", "cache_kb")
    if not all(isinstance(out.get(k), list) for k in required):
        raise RuntimeError("无法从 dumpsys diskstats 解析出四个数组；"
                           "设备可能未授权 ADB 或系统版本格式不同。")
    lens = {len(out[k]) for k in required}
    if len(lens) != 1:
        raise RuntimeError(f"diskstats 数组长度不一致: { {k: len(out[k]) for k in required} }")
    rows = []
    for i, pkg in enumerate(out["packages"]):
        app = out["app_kb"][i] or 0
        data = out["data_kb"][i] or 0
        cache = out["cache_kb"][i] or 0
        rows.append({
            "package": str(pkg),
            "app_kb": app,
            "data_kb": data,
            "cache_kb": cache,
            "total_kb": app + data,            # data already includes cache
            "cache_pct": round(100.0 * cache / data, 1) if data else 0.0,
            "app_h": human(app),
            "data_h": human(data),
            "cache_h": human(cache),
            "total_h": human(app + data),
        })
    rows.sort(key=lambda r: r["total_kb"], reverse=True)
    return rows


def parse_usagestats(text):
    """Best-effort extraction of last-used ms per package from dumpsys usagestats.

    Output format varies a lot across Android versions; return {} if not found.
    """
    result = {}
    # Common forms: "com.foo.bar ... Last used: 2026-01-01 ..." or
    # "  com.foo.bar: ... lastUseTime=1234567890"
    for m in re.finditer(r"([a-zA-Z][\w]*(\.[\w]+)+)[\s\S]{0,200}?lastUseTime[=: ]+(\d+)", text, re.I):
        try:
            result[m.group(1)] = int(m.group(3))
        except ValueError:
            pass
    return result


def classify(rows, usage):
    """Attach a coarse, honest suggestion to each package."""
    for r in rows:
        pkg = r["package"]
        r["last_used_ms"] = usage.get(pkg)
        # Suggestions are guidance only, never auto-executed.
        if r["cache_pct"] >= 30 and r["cache_kb"] > 50 * 1024:
            r["suggestion"] = f"缓存占 {r['cache_pct']}%（约 {r['cache_h']}），建议在该 App 内「清除缓存」"
        else:
            r["suggestion"] = "大头多为数据/媒体，建议 App 内存储管理或按需卸载"
        r["open_settings_cmd"] = (
            f"adb shell am start -a android.settings.APPLICATION_DETAILS_SETTINGS -d package:{pkg}"
        )
    return rows


def main():
    p = argparse.ArgumentParser(description="ADB 按 App 存储诊断（只读，无需 root）")
    p.add_argument("--serial", help="adb 设备序列号（多设备时指定）")
    p.add_argument("--no-usage", action="store_true", help="跳过 usagestats 查询")
    args = p.parse_args()

    try:
        disk = adb_shell(["dumpsys", "diskstats"], serial=args.serial)
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)

    try:
        rows = parse_diskstats(disk)
    except Exception as e:
        print(json.dumps({"error": str(e), "raw_head": disk[:500]}, ensure_ascii=False, indent=2))
        sys.exit(1)

    usage = {}
    if not args.no_usage:
        try:
            u = adb_shell(["dumpsys", "usagestats"], serial=args.serial, timeout=120)
            usage = parse_usagestats(u)
        except Exception:
            usage = {}   # optional signal; never fail the whole diagnosis

    rows = classify(rows, usage)
    total = sum(r["total_kb"] for r in rows)
    cache_total = sum(r["cache_kb"] for r in rows)
    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "method": "adb shell dumpsys diskstats",
        "source": "/data/system/diskstats_cache.json (系统周期刷新，可能滞后，为估算值)",
        "root_required": False,
        "platform": "android",
        "summary": {
            "apps": len(rows),
            "apps_total_h": human(total),
            "apps_cache_total_h": human(cache_total),
            "note": "缓存会被系统自动清理且立即重建；真正大头通常是数据/媒体。按 App 明细引导用户在系统设置或 App 内清理。",
        },
        "apps": rows[:60],
        "limitations": [
            "数据来自系统缓存文件，非实时，可能滞后数小时。",
            "需要已授权 ADB 调试（开发者选项）；shell UID 具备 DUMP 权限，无需 root。",
            "usagestats 输出格式因 Android 版本而异，最后使用时间可能缺失。",
            "本脚本只读，不执行任何清理；删除/引导需用户确认。",
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
