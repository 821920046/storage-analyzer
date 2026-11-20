"""参考脚本：从扫描数据构造 analysis JSON。

这是给 Agent / 开发者看的**结构示例**——真实使用时，Agent 应读取 scan.py 的
输出并做智能分级，而不是照抄本文件。本脚本只演示 analysis JSON 的字段结构
与路径构造方式，不含任何真实机器数据。

用法（可选）：
    python examples/write_analysis.py > storage_analysis.json

环境变量：LOCALAPPDATA / APPDATA / USERPROFILE（Windows）；macOS 用 HOME。
"""
import json
import os
import sys

L = os.environ.get("LOCALAPPDATA", os.path.expanduser("~/AppData/Local"))
R = os.environ.get("APPDATA", os.path.expanduser("~/AppData/Roaming"))
U = os.environ.get("USERPROFILE", os.path.expanduser("~"))

analysis = {
    "generated_at": "2026-01-01 12:00:00",
    "scan_seconds": 30.0,
    "system": {
        "os": "Windows 11",
        "build": "10.0.26100",
        "arch": "AMD64",
        "user": os.environ.get("USERNAME", os.environ.get("USER", "user")),
        "home": U,
        "filesystem": "NTFS",
        "disk_total": "512.0 GB",
        "disk_used": "300.0 GB",
        "disk_free": "212.0 GB",
        "purgeable": "",
        "disk_name": "C:\\",
        "disks": [
            {"name": "C:\\", "total": "512.0 GB", "used": "300.0 GB", "free": "212.0 GB"}
        ],
        # 必须与 scan.py 输出的 system.safe_roots 一致，否则 server 会拒绝删除
        "safe_roots": [U, L, R, os.path.join(U, "Temp"), os.path.join(U, "Downloads"),
                       "C:\\Program Files", "C:\\Program Files (x86)", "C:\\"],
    },
    "green": [
        {
            "name": "pip 缓存",
            "path": os.path.join(L, "pip", "Cache"),
            "size_estimate": "约 1.2 GB",
            "kill_processes": [],
            "trash_paths": [os.path.join(L, "pip", "Cache")],
            "commands": [{"label": "官方命令", "cmd": "pip cache purge"}],
        },
        {
            "name": "node-gyp 编译缓存",
            "path": os.path.join(L, "node-gyp"),
            "size_estimate": "约 51 MB",
            "kill_processes": [],
            "trash_paths": [os.path.join(L, "node-gyp")],
            "commands": [{"label": "说明", "cmd": "下次编译自动重建"}],
        },
    ],
    "yellow": [
        {
            "name": "浏览器 User Data（含账号数据）",
            "path": os.path.join(L, "Google", "Chrome", "User Data"),
            "size": "约 15.0 GB",
            "content_profile": "Profile 数据 + AI 模型缓存 + 组件缓存",
            "why_manual": "含书签/密码/登录态，仅缓存子目录可安全清理",
            "disposal": "Chrome 内清除浏览数据缓存；或仅删 OptGuideOnDeviceModel、component_crx_cache 子目录",
            "risk": "删 Profile 目录会丢失密码/书签/登录",
            "trash_paths": [
                os.path.join(L, "Google", "Chrome", "User Data", "OptGuideOnDeviceModel"),
                os.path.join(L, "Google", "Chrome", "User Data", "component_crx_cache"),
            ],
            "open_note": "Default/ 与 Profile 1/ 含账号数据，不建议手动挑选删除。",
        }
    ],
    "red": [
        {
            "name": "Microsoft Office",
            "path": "C:\\Program Files\\Microsoft Office",
            "size": "约 4.7 GB",
            "why_keep": "应用本体，手删会残留注册表和服务项",
            "indirect_release": "设置 → 应用 → 搜索 Microsoft 365 → 卸载",
            "app_paths": ["C:\\Program Files\\Microsoft Office"],
        }
    ],
    "denied": [],
    "summary": {
        "overview": "最大的可清理项是开发缓存，浏览器数据需在应用内审查。",
        "tier_stats": {"green": "约 1.3 GB", "yellow": "约 15.0 GB", "red": "约 4.7 GB"},
        "priority": [
            "先清绿灯：开发缓存约 1.3 GB，可再生零风险",
            "浏览器：在 Chrome 内清除缓存，或仅移废纸篓列明的缓存子目录",
        ],
        "long_term": [
            "开启 Windows 存储感知自动清理",
            "cleanmgr 清理系统文件",
        ],
    },
}

json.dump(analysis, sys.stdout, ensure_ascii=False, indent=2)
print(file=sys.stderr)  # 保持 stdout 是纯 JSON
