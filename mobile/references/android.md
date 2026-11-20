# Android 存储底层逻辑、可清理项与分级参考（基于官方文档核实）

分析 Android 扫描结果时读这份。**本文件在对抗审查后依据 Android 官方文档重写**——核心结论是：
**"逐个删 App 的 cache 目录"是低 ROI 手段，真正的大头在媒体文件/聊天记录/数据库，必须按 App 粒度治理。**
不要向用户承诺"清 cache 能大幅释放空间"。

## 一、必须理解的三条官方事实（决定了能力边界）

来源：Android Developers — App-specific storage、Manage all files、StorageManager

1. **cache 目录天生小额，且系统会自己清**
   > "This cache directory is designed to store a small amount of your app's sensitive data."
   > "When the device is low on internal storage space, Android may delete these cache files to recover space."

   含义：`/data/data/<pkg>/cache` 是给"少量临时数据"用的，系统在空间紧张时会自动回收。**手动清它的边际收益很低，且马上会被重建。** 可用 `StorageManager.getCacheQuotaBytes()` 查配额。

2. **连 ALL FILES ACCESS 都进不了别家 App 的数据目录**
   > "Apps that are granted [MANAGE_EXTERNAL_STORAGE] still can't access the app-specific directories that belong to other apps, because these directories appear as subdirectories of Android/data/."

   含义：Android 11+ 分区存储下，非 root 的第三方工具**无法读写其他 App 的 `/sdcard/Android/data/<pkg>`**，更不用说 `/data/data/<pkg>`。所谓"All Files Access"也做不到。Google Play 还严格限制该权限只给文件管理器/备份/杀毒等核心用例。→ 这解释了为什么本技能的 limited 档价值有限，是系统限制不是代码缺陷。

3. **官方给的正规清理路径都是"引导用户/App 自己清"**
   - `ACTION_MANAGE_STORAGE`：让用户选择要删除的文件，并可显示可用空间。
   - `ACTION_CLEAR_APP_CACHE`：请求用户清除**所有 App** 的缓存（官方警告："can substantially affect device battery life and might remove a large number of files"）。
   - `getManageSpaceActivityIntent()`（Android 12+）：有 MANAGE_EXTERNAL_STORAGE + QUERY_ALL_PACKAGES 的文件管理器，可跳到**另一个 App 自定义的"管理空间"Activity**（如微信的存储清理页），即使那个 Activity 未导出。
   - App 自身通过 `cacheDir.delete()` / `externalCacheFile.delete()` 清理自己的缓存。

   → 正道是"诊断谁占空间 + 把用户带到正确的清理入口"，而不是代理删文件。

权威链接：
- https://developer.android.com/training/data-storage/app-specific
- https://developer.android.com/training/data-storage/manage-all-files
- https://developer.android.com/reference/android/os/storage/StorageManager

## 二、真正有效的释放手段（按 ROI 排序）

给用户的建议按此顺序，别反过来：

| 优先级 | 手段 | 权限 | 释放量级 | 风险 / 说明 |
|---|---|---|---|---|
| 1 | 卸载长期不用的大 App（系统设置→应用→卸载） | 无 | 数 GB | 最大收益；「卸载 App」保留数据、「删除 App」彻底清 |
| 2 | 各 App **自带**的存储清理（微信存储空间管理、抖音清缓存、相册"最近删除"） | 无 | 数百 MB~数 GB | 正道；App 最懂哪些能删，不会误删数据库 |
| 3 | 删除 Download/DCIM 里的安装包、旧视频、重复照片 | 无 | 视情况，常达数 GB | 用户数据，需用户确认；可用技能扫描定位 |
| 4 | `ACTION_CLEAR_APP_CACHE`（清所有 App 缓存） | 系统 intent | 数十 MB~数百 MB，短暂 | 官方警告影响电量、删除量大；收益短暂 |
| 5 | 逐 App 删私有 `cache` 目录 | **root** | 每 App 几十~几百 MB | 安全但低 ROI、会重建；本技能 cleanup 只做这一档 |

**关键**：前 3 项才是"提升手机存储空间"的主战场，且都**不需要 root**。技能的真正价值应放在"精确定位 + 精确引导到第 2/3 项"，第 5 项只是能力展示。

## 三、App 私有目录语义与可清理性（root 档参考）

| 子目录 | 装什么 | 判断 |
|---|---|---|
| `cache` | 少量临时缓存（系统可自动清） | 🟢 可删但低收益、会重建 |
| `code_cache` | ART/JIT 编译的临时代码缓存 | 🟢 可再生（社区资料：删除后重新编译，首次启动略慢）* |
| `files` | 持久文件 | 🔴 用户/应用数据，删了丢东西 |
| `databases` | SQLite 数据库（聊天记录等） | 🔴 绝不手删 |
| `shared_prefs` | 配置/登录态 | 🔴 删了丢登录 |
| `MicroMsg`、`msg`、`media` 等 App 自定义数据目录 | 媒体/消息 | 🟡 真大头在这，属用户数据，只能引导 App 内清理 |
| `app_webview` | WebView 缓存 | 🟢 通常可再生，但含 Cookie 时删了要重新登录 |
| `lib`、`no_backup` | 原生库/禁止备份数据 | 🔴 不动 |

\* 标注"社区资料"的结论未获官方文档直接确认，如实标注，不要当成官方保证。

## 四、ART / dalvik-cache / OTA 残留（诚实标注）

- `/data/dalvik-cache`、`/data/app/<pkg>/oat/*.odex|.vdex|.art`：ART 编译产物。社区资料称删除后系统会重新编译、首次启动变慢，**未找到官方文档直接确认**；且现代 Android 上这些多在系统分区/对普通用户不可见。→ **不作为推荐清理项**，不承诺收益。
- OTA 更新包残留：部分机型在 `/data/ota_package` 或下载目录留下 update.zip。是否自动清理因厂商而异，**未找到统一官方结论**。→ 若扫描发现且体积大，归 🟡 让用户自己确认，不自动删。
- `/system`、`/vendor`、`/cache`(recovery)：系统分区，不动。

## 五、常见 App 的大头在哪（引导用，非删除用）

| App | 真正占空间的位置 | 正确的清理入口 |
|---|---|---|
| 微信 | `MicroMsg` 下的图片/视频/语音、数据库（多 GB） | 微信→我→设置→通用→存储空间→清理缓存/管理聊天记录 |
| 抖音/快手 | 外部缓存的已下载视频、`cache` | App→设置→清除缓存 |
| QQ | 聊天图片/文件、数据库 | QQ→设置→隐私→存储空间清理 |
| 相册 | 照片视频本体 + "最近删除"相簿 | 相册→相簿→最近删除→清空；开启云盘优化 |
| 浏览器 | 网站数据、离线页面 | 浏览器→设置→清除浏览数据 |

引导时给到"App→菜单→具体项"的粒度，不要只说"去设置里清理"。

## 六、能力分档（诚实版）

- **full（root）**：能删各 App `cache`/`code_cache`（安全但低 ROI）。真正大头仍需引导 App 内清理。
- **limited（非 root/ADB）**：机内 ADB 通常不可用的话，`pm trim-caches` 未必能执行；主要价值是扫描公共存储 + 精确引导。**别承诺能清 App 缓存。**
- **guide（iOS）**：见 ios.md。

## 七、执行纪律

- 任何删除前展示计划 + 用户确认；清理器三道硬闸门不可绕过。
- 引导用户用系统/App 自带入口时，说明这是官方推荐方式，比第三方代删更安全。
- 遇到安全策略拦截就引导手动，不绕过。
- 涉及"可释放空间"一律是下限估算，cache 收益注明"会被重建、系统也会自动清"。
