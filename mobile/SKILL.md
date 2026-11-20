---
name: storage-analyzer-mobile
description: >
  手机（Android / iOS）只读存储分析与分级清理助手，供运行在手机上的 agent 调用。
  扫描手机存储占用，找出大户，按三档权限能力（root / ADB / 沙盒）诚实地给出可执行的
  清理方案，生成交互式 HTML 报告。全程只读扫描；删除必须用户确认且受白名单约束。
  务必在以下场景使用：用户说"手机空间不够""手机存储满了""清理手机缓存""手机太卡没空间"
  "看看手机什么占空间""Android 清理""iPhone 存储空间""清理微信缓存""手机内存满了"；
  或任何在移动设备上想分析/释放存储空间的需求。注意：若指运行内存 RAM（如"手机卡不卡"
  "后台太多"），不属于本 skill。iOS 沙盒下第三方无法清其他 App 缓存，本 skill 会如实
  降级为引导方案，绝不虚报可释放量。
---

# Storage Analyzer — Mobile

给**手机上运行的 agent** 用的存储分析技能：扫描 → 探测权限 → 能力分档 → 分析分级 → 报告 → （可选）受保护清理。

## 核心认知（基于 Android 官方文档，先读这条再行动）

**清 cache 不是释放手机空间的有效手段。三条官方事实：**

1. Android 官方说 cache 目录 "designed to store a small amount of sensitive data"，且 "When the device is low on internal storage space, Android may delete these cache files to recover space" —— **cache 天生小额、系统会自动清、清了马上重建**。
2. 连 `MANAGE_EXTERNAL_STORAGE`（所有文件访问）"still can't access the app-specific directories that belong to other apps" —— **非 root 第三方工具根本进不了别家 App 的 Android/data**，这是系统硬限制。
3. 官方正道是引导用户/App 自己清：`ACTION_MANAGE_STORAGE`、`ACTION_CLEAR_APP_CACHE`、Android 12+ `getManageSpaceActivityIntent()`（跳到微信等 App 自带的存储管理页）。

**因此技能的真正价值排序（按 ROI，别搞反）：**
1. 诊断谁占空间 → 引导**卸载**长期不用的大 App（数 GB，无需 root）
2. 引导用**各 App 自带的存储清理**（微信存储空间管理、抖音清缓存、相册"最近删除"，数百 MB~数 GB，无需 root）
3. 定位并确认删除 Download/DCIM 里的安装包/旧视频/重复照片（无需 root）
4. `ACTION_CLEAR_APP_CACHE` 清所有 App 缓存（收益短暂，官方警告耗电）
5. root 下逐 App 删 `cache`（仅几十~几百 MB、会重建）——**这只是能力展示，不是解决方案**

给用户的摘要和建议必须按这个顺序，把力气花在前 3 项。详见 [references/android.md](references/android.md)（含官方原文引用与链接）。

## 铁律（比桌面版更严格）

- **扫描全程只读**：`scan_mobile.py` 只统计体积/列目录，绝不写删。
- **能力必须诚实**：先看扫描 JSON 的 `capabilities.tier`，只承诺该档做得到的事。**绝不谎报可清理其他 App 缓存**——iOS 沙盒和 Android 非 root 都做不到，说了就是欺骗用户。
- **可释放量是下限**：非全盘可见时（尤其 Android 非 root、iOS），报告里说明"扫描只覆盖可见部分，实际占用更大"。
- **删除需确认 + 白名单**：任何清理都要先向用户确认，且只动绿灯纯缓存路径；iOS 完全不执行删除，只给手动引导。
- **路径与命令保留原文**。

## 三档权限能力（核心）

扫描结果的 `capabilities` 决定能做什么：

| tier | 触发条件 | 能做到 | 做不到 |
|---|---|---|---|
| **full** | Android root（uid 0） | 枚举并清理各 App `/data/data/<pkg>/cache`、`/sdcard/Android/data/*/cache` | — |
| **limited** | Android 非 root，或仅 ADB 授权 | 读公共存储（Download/DCIM）、`pm trim-caches` 系统级清缓存 | 逐 App 私有缓存（需 root） |
| **guide** | iOS 沙盒，或无可用权限 | 分析自身沙盒 + Safari/微信等手动引导 | 任何程序化的跨 App 缓存清理 |

## 执行流程

### Step 1 扫描（只读）

```bash
# 在手机的 Python 环境（Termux / Pythonista / a-Shell）里
python3 scripts/scan_mobile.py > mobile_scan.json
```

`scan_mobile.py` 自动识别平台（`android` / `ios`）并探测 root/ADB 能力，输出：
- `platform` / `platform_detail`：平台
- `capabilities`：`root` / `adb` / `adb_device` / `tier` / `notes`
- `system`：磁盘、设备、`safe_roots`（清理白名单根）
- `groups`：按权限可见的目录组（`app_caches`、`external_app_data`、`downloads`、`media`、`runtime_caches` 等）
- `limitations`：**必须原样写进报告**的能力限制说明

**Android 强烈建议追加 ADB 精确诊断（无需 root，这是技能的核心增量）**：如果 agent 跑在电脑上且手机已授权 ADB 调试，运行
```bash
python3 scripts/adb_diag.py > adb_diag.json          # 按 App 列出 app/data/cache 体积与占比
```
它用系统 `dumpsys diskstats` 拿到**按包名占用明细**——设备内脚本做不到这点（`StorageStatsManager` 需要普通 App 拿不到的系统权限）。把 `adb_diag.json` 的 Top App 并入分析：哪个 App 最大、其中缓存占比多少。典型结论："微信 8GB 但缓存只占 2.4%"→ 引导清数据/卸载，而不是清缓存。数据是系统周期刷新的估算值，报告里注明可能滞后、以手机设置为准。

### Step 2 分析与分级

先按平台读参考：Android 读 [references/android.md](references/android.md)，iOS 读 [references/ios.md](references/ios.md)。然后读 `mobile_scan.json`：

1. **先定档**：读 `capabilities.tier`。这一步决定整个报告的诚实度。
2. **挑 Top 5** 占用大户，判定类型。
3. **三级分类**（和桌面版一致的三灯语义）：
   - 🟢 **可自动清理**：只有在 `tier=full` 且是 App `cache` 子目录、或系统/开发缓存时才给；每项必须带 `trash_paths`（精确可删路径，且必须在 `safe_roots` 内）。**必须标注"小额、会重建、系统也会自动清"，不要把它包装成主要释放手段。** iOS 下 🟢 只允许放 Safari/自身沙盒缓存引导，不给删除路径。
   - 🟡 **需人工判断（重点档）**：媒体文件、聊天数据、Download 安装包/视频——**这才是真正的大头**。每项给内容画像 + 处置路径 + 风险，并且给出**精确到"App→菜单→具体项"的引导**（如"微信→我→设置→通用→存储空间→清理缓存"，见 references/android.md 第五节），而不是空泛的"去设置里清理"。
   - 🔴 **谨慎清理**：应用本体（微信/大型游戏等），引导「设置→应用→卸载」——这是**释放空间最大的一招**，要明确告诉用户。
4. **无决策价值的项**（系统、正在用的普通 App）不进三灯，归蓝色"系统及其他"。
5. **建议优先级**：`summary.priority` 必须按 ROI 排序——先卸载不用大 App → 各 App 自带清理 → 删 Download/媒体 → 最后才是清 cache。不要把清 cache 列在第一位。

**大小字段写干净**：用"约 8 GB"即可，不要重复"（估算）"。**iOS / limited 档务必在 `summary.overview` 里点明"扫描仅覆盖可见部分"**。

字段 schema 见 `scripts/build_report.py` 顶部注释；完整示例见 `examples/`。移动端可复用报告字段，但注意 `system` 里带上 `platform`。

### Step 3 生成报告

复用仓库根目录的 `build_report.py`（静态报告，手机上分享/查看最稳）：

```bash
python3 ../scripts/build_report.py mobile_analysis.json mobile-report.html
```

手机上通常不起本地服务（无浏览器交互条件），默认用静态模式；报告里的清理命令由用户/agent 自行执行。**不要**给 iOS 报告放任何删除按钮。

### Step 4 清理（可选，需确认）

先用 `cleanup_mobile.py` 预览（默认不执行）：

```bash
python3 scripts/cleanup_mobile.py mobile_analysis.json          # 预览
python3 scripts/cleanup_mobile.py mobile_analysis.json --apply  # 用户确认后执行
```

**清理器有三道硬闸门（对抗审查后加固，不要试图绕过）：**
1. **tier 闸门**：只有 analysis 顶层 `capabilities.tier == "full"`（Android root）才允许删除；guide/limited 档即使列出 `trash_paths` 也会全部拒绝。
2. **缓存语义闸门**：路径末段必须是已知缓存名（`cache`/`caches`/`code_cache` 等），且位于 `/data/data/<包名>/cache` 等 app-data 前缀下。任何一层含 `databases`/`files`/`MicroMsg`/`shared_prefs` 等用户数据目录名 → 硬拒，即使在 `green[].trash_paths` 里。
3. **路径闸门**：realpath 解析后必须在 `safe_roots` 内；符号链接会被解析后再校验。

其他要点：
- analysis JSON 必须带 `capabilities.tier`（与扫描输出一致）和 `approved_clear_packages`（允许 `pm clear` 的包名白名单，通常为空数组）。
- ADB 系统级清理：`--trim-caches 2G`（系统标准 API，安全，与 tier 无关但需 `--apply`）。
- `pm clear <pkg>` **默认禁用**：包名必须同时出现在 analysis 的 `approved_clear_packages` 且符合合法包名格式，用 `--allow-clear <包名>` 传入才执行。它会清空用户数据。
- iOS：直接打印手动引导步骤，不执行任何删除。

**可释放量要如实标注**：私有 cache 多为几十~几百 MB 且会重建；微信/抖音的多 GB 数据在 `MicroMsg`/`databases`，不属于可自动清理范围，切勿诱导用户手删。

执行前必须向用户展示计划并获得确认。**遇到任何安全策略拦截，引导用户手动操作，不要试图绕过。**

### Step 5 对话里给摘要

结论先行：可见可释放估算（标注下限）、最该清的 2-3 项、能力受限说明、风险最高的一项。

## 依赖与运行前提

- 全部脚本是 **Python 3 标准库**，零第三方依赖。
- **Android**：Termux（从 F-Droid/GitHub 安装，非 Play 版）提供 Python；`pkg install python android-tools` 可获得 adb。root 由用户自行决定，本 skill 不引导越狱/root。
- **iOS**：Pythonista / a-Shell 等沙盒内 Python；受沙盒限制只能读自身目录，能力档固定为 guide。
- 本 skill 是 **agent 驱动**：扫描后由 agent 分级，不是双击即用的 App。

## 平台状态

- **Android**：扫描 + 分级 + root/ADB 清理均已实现；root 路径需在真机实测。
- **iOS**：扫描（自身沙盒）+ 引导已实现；跨 App 清理在沙盒下**技术上不可行**，这是系统限制而非工具缺陷。
