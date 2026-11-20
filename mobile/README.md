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
│   ├── scan_mobile.py           # 只读扫描 + 平台/权限探测（设备内运行）
│   ├── adb_diag.py              # ADB 按 App 精确占用诊断（电脑端，无需 root，核心增量）
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

# 1b) Android 推荐追加：ADB 按 App 精确诊断（电脑端、无需 root，需手机已授权 ADB 调试）
python3 scripts/adb_diag.py > adb_diag.json   # 拿到"微信 8GB / 缓存仅 2%"这类关键数据

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

## 安全（对抗审查后加固）

- 扫描脚本零写入；清理脚本默认 dry-run，`--apply` 才动手。
- **三道硬闸门**：① `capabilities.tier=="full"` 才允许删除；② 路径末段必须是已知缓存名且位于 app-data 前缀下，`databases`/`files`/`MicroMsg`/`shared_prefs` 等硬拒；③ realpath 必须在 `safe_roots` 内（解析符号链接后校验）。仅靠 agent 在 `green[].trash_paths` 里列路径**无法**绕过。
- `pm clear` 默认禁用：包名须在 analysis 的 `approved_clear_packages` 中且格式合法。
- analysis JSON 必须带 `capabilities.tier`；缺失或不匹配时拒绝一切删除。
- 遇安全策略拦截时引导用户手动处理，不绕过。

## 核心认知：清 cache 不是解决方案（依据 Android 官方文档）

深度调研后修正了方向——**"逐个删 App 的 cache"是低 ROI 手段**：

- 官方文档明确：cache 目录 "designed to store a small amount of sensitive data"，且低存储时系统**会自动删除**缓存；手动清了也会重建。
- 官方文档明确：即便授予 `MANAGE_EXTERNAL_STORAGE`，"still can't access the app-specific directories that belong to other apps"——非 root 第三方工具进不了别家 `Android/data`。
- 官方正道是引导：`ACTION_MANAGE_STORAGE`、`ACTION_CLEAR_APP_CACHE`、`getManageSpaceActivityIntent()`（Android 12+，跳到微信等 App 自带的存储管理页）。

**真正能释放空间的手段（按 ROI，均无需 root）**：① 卸载不用的大 App（数 GB）→ ② 各 App 自带存储清理（数百 MB~数 GB）→ ③ 删 Download/媒体里的安装包与旧文件 → ④ 系统清所有 App 缓存（收益短暂）→ ⑤ root 逐 App 删 cache（仅几十~几百 MB，会重建，本技能 cleanup 只做这档）。详见 [references/android.md](references/android.md)。

## 诚实的实用性评估

以上认知也修正了评分——技能的定位从"代理清理"转向"精确定位 + 精确引导"：

| 档位 | 实用性 | 说明 |
|---|---|---|
| full（root） | 6/10 | 能逐 App 清 cache（低 ROI）；真正大头仍需引导 App 内清理 |
| limited（非 root/ADB） | 4/10 | 机内 ADB 多不可用；价值在扫描公共存储 + **精确引导到 App 清理入口**（这部分无需 root、收益最大） |
| guide（iOS） | 2/10 | 沙盒限制只能分析自身近空目录 + 手动建议，本质是"诚实标注的引导" |

**给用户的实话**：想真正腾出空间，靠的不是脚本删缓存，而是按引导卸载/清理大 App 和媒体文件——这是系统限制决定的，不是工具缺陷。

可释放量一律是下限估算，尤其 non-full 档扫描只覆盖可见部分。
