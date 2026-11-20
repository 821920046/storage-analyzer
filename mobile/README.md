# Storage Analyzer — Mobile

`storage-analyzer` 技能的移动端分支：给**运行在手机上的 agent** 用，分析并（在权限允许时）清理手机存储。

> ⚠️ 能力随权限诚实降级：Android root 可逐 App 清缓存；ADB 可做系统级清理；**iOS 沙盒下无法程序化清理其他 App 缓存，只做分析与手动引导**。绝不虚报可释放量。

## 设计原则

和桌面版最大的不同是**权限不确定**。因此本分支：

1. 扫描时先探测平台与权限，输出 `capabilities.tier`（`full` / `limited` / `guide`）。
2. Agent 必须按档位行动——只承诺该档做得到的事。
3. 扫描全程只读；清理需确认 + 白名单；iOS 完全不执行删除。

## 文件

```
mobile/
├── SKILL.md                     # 移动技能定义（agent 读；含三档能力表与执行流程）
├── README.md                    # 本文件
├── scripts/
│   ├── scan_mobile.py           # 只读扫描 + 平台/权限探测
│   └── cleanup_mobile.py        # 受保护清理执行器（默认 dry-run）
├── references/
│   ├── android.md               # Android 权限档位 / 目录 / 命令 / 风险
│   └── ios.md                   # iOS 沙盒限制 / 手动引导
└── examples/
    ├── android_root_example.json
    └── ios_guide_example.json
```

报告生成复用仓库根目录的 `scripts/build_report.py`。

##  Quick start

```bash
# 1) 扫描（只读）
python3 scripts/scan_mobile.py > mobile_scan.json

# 2) Agent 读 mobile_scan.json + references/<平台>.md，产出 mobile_analysis.json

# 3) 生成报告（复用根脚本）
python3 ../scripts/build_report.py mobile_analysis.json mobile-report.html

# 4) 清理（可选，先预览再确认执行）
python3 scripts/cleanup_mobile.py mobile_analysis.json           # 预览
python3 scripts/cleanup_mobile.py mobile_analysis.json --apply   # 确认后执行
```

## 运行环境

- **Android**：Termux（F-Droid / GitHub 版）+ `pkg install python android-tools`。root 可选，非本技能引导范围。
- **iOS**：Pythonista / a-Shell 等沙盒 Python；能力固定为 `guide`。

## 安全

- 扫描脚本零写入；清理脚本默认 dry-run，`--apply` 才动手。
- 所有删除路径必须在 `system.safe_roots` 内且属于绿灯纯缓存。
- `pm clear` 默认禁用，需用户逐个显式授权。
- 遇安全策略拦截时引导用户手动处理，不绕过。
