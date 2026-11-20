# Android 数据布局、权限与分级参考

分析 Android 扫描结果时读这份。核心是：**能访问多少，完全取决于权限档位**。

## 权限档位与可访问范围

| 档位 | 条件 | 可读目录 | 可清理 |
|---|---|---|---|
| full | root（uid 0，Termux 里 `id -u`=0） | `/data/data/*`、`/data/user/0/*`、`/sdcard/Android/data/*`、公共存储 | 各 App 的 `cache` 子目录、`/data/local/tmp`、开发缓存 |
| limited | 非 root + ADB 授权 | 公共存储 `/sdcard/Download`、`DCIM` 等；Termux 自身目录 | `pm trim-caches <size>` 系统级缓存；公共存储里明确的安装包 |
| guide | 无 root 无 ADB | 仅公共存储 + 当前 App 沙盒 | 仅引导用户手动操作 |

**判定**：以扫描 JSON 的 `capabilities.tier` 为准，不要凭感觉。

## 关键目录

| 目录 | 装什么 | 典型分级 |
|---|---|---|
| `/data/data/<pkg>/cache` | 各 App 私有缓存（root 可读） | 🟢 仅 cache 子目录，删后 App 自动重建 |
| `/data/data/<pkg>/databases`、`shared_prefs`、`files` | 聊天记录/登录态/配置 | 🔴 绝不手删 |
| `/sdcard/Android/data/<pkg>/cache` | App 外部缓存 | 🟢 |
| `/sdcard/Android/data/<pkg>/files` | App 外部数据 | 🟡 需判断 |
| `/sdcard/Download` 的 `.apk/.zip` | 安装包残留 | 🟢 装完可删 |
| `/sdcard/DCIM`、`Pictures`、`Movies` | 照片/视频 | 🟡 用户数据，引导手动 |
| `/data/local/tmp`、`/cache` | 临时文件 | 🟢 |
| `/data/data/<pkg>` 本体 | 应用本体+数据 | 🔴 引导「设置→应用→卸载」 |

## 清理命令

- **系统级缓存（无需 root）**：`adb shell pm trim-caches 2G`（清到释放约 2GB，系统标准 API，安全）。对应 `cleanup_mobile.py --trim-caches 2G --apply`。
- **逐 App 缓存（需 root）**：删除 `/data/data/<pkg>/cache` 目录本身（保留父目录），App 下次启动自动重建。
- **危险命令**：`pm clear <pkg>` 会清空该 App 全部用户数据（等同"清除数据"），**默认禁止**，只在用户逐个显式授权后执行。
- **应用卸载**：`adb shell pm uninstall -k --user 0 <pkg>`（`-k` 保留数据）；彻底卸载走系统设置。

## 系统占用（不上灯，归蓝色）

- `/system`、`/vendor`、`/data/dalvik-cache`、`/data/system`：系统核心，不动。
- 间接释放：设置 → 存储 → 清理；开发者选项里限制后台进程数；卸载预装 App（需 ADB/root，谨慎）。

## 注意

- Android 10+ 分区存储（Scoped Storage）：非 root 进程访问不了其他 App 的私有目录，这是系统硬性限制。
- 不要引导普通用户 root/越狱；若用户已 root，按 full 档处理即可。
- 每次删除前展示计划并确认；遇到安全策略拦截就引导手动，不绕过。
