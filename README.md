# Storage Analyzer

一个给 AI Agent 调用的**只读磁盘存储分析技能**（macOS / Windows 自动识别）。Agent 扫描磁盘占用、识别大户、三级分类、生成交互式 HTML 报告，用户可在网页上逐项确认后一键清理缓存。

> 扫描与分析全程只读；删除必须经用户在报告网页上逐项确认，且受路径白名单约束。

## 它解决什么

"磁盘满了 / C 盘红了 / 什么占空间 / 帮我清缓存"——这类问题交给它，会产出一份这样的报告：

- **磁盘总览**：容量、进度条、三色分段（可清 / 需判断 / 不建议动 / 系统及其他）
- **占用 Top 5**：谁在吃硬盘
- **三级决策清单**：
  - 🟢 可自动清理：纯缓存、临时文件、安装包残留（可再生、不丢数据）
  - 🟡 需人工判断：聊天记录、浏览器数据、项目代码等（给处置路径 + 风险）
  - 🔴 谨慎清理：应用本体（不给删除按钮，只引导正规卸载）
- **一键操作**：绿灯可「移到废纸篓」或「直接删除」，橙灯只能打开定位或移废纸篓，红灯只能打开定位

## 作为技能安装

本仓库根目录就是技能目录（`SKILL.md` 在根）。克隆到你的 Agent 技能目录即可：

```bash
# Claude Code
git clone https://github.com/821920046/storage-analyzer.git ~/.claude/skills/storage-analyzer

# WorkBuddy（用户级技能目录）
git clone https://github.com/821920046/storage-analyzer.git ~/.workbuddy-ai/skills/storage-analyzer
```

安装后，用自然语言触发即可，例如"帮我看看 C 盘为什么满了"。完整触发词见 `SKILL.md` 的 frontmatter `description`。

## 📱 手机版（Android / iOS）

`mobile/` 目录下是给**运行在手机上的 agent** 用的移动分支，支持 Android root/ADB 与 iOS 沙盒，按权限自动降级。触发场景："手机空间不够""清理手机缓存""iPhone 存储空间"等。详见 [mobile/README.md](mobile/README.md)。

> 关键差异：iOS 沙盒下第三方无法清理其他 App 缓存，手机版会如实降级为引导方案，不虚报可释放量。

## 目录结构

```
storage-analyzer/
├── SKILL.md                       # 技能定义（Agent 读这个：铁律 + 执行流程）
├── README.md                      # 本文件（给人看）
├── scripts/
│   ├── scan.py                    # 只读扫描，输出 JSON 到 stdout
│   ├── build_report.py            # 分析 JSON → 静态 HTML（无删除按钮）
│   └── server.py                  # 本地服务 + 受保护的删除 API（默认入口）
├── references/
│   ├── macos.md                   # macOS 数据布局与分级参考
│   └── windows.md                 # Windows 数据布局与分级参考
├── assets/
│   └── report_template.html       # 报告模板（占位符 __REPORT_DATA__ / __DELETE_CONFIG__）
├── mobile/                        # 📱 手机版（Android / iOS）
│   ├── SKILL.md                   # 移动技能定义（含三档权限能力表）
│   ├── README.md
│   ├── scripts/{scan_mobile,cleanup_mobile}.py
│   ├── references/{android,ios}.md
│   └── examples/
└── examples/
    ├── scan_example.json          # 脱敏的扫描输出示例
    ├── analysis_example.json      # 字段齐全的分析 JSON 示例（生成模板）
    └── write_analysis.py          # 从扫描数据构造分析 JSON 的参考脚本（脱敏）
```

## 工作流

```
scan.py ──(只读扫描)──> scan.json
                           │
                   Agent 分析分级（读 references）
                           │
                           ▼
                    analysis.json ──> server.py ──> 浏览器报告 + 删除 API
                                 └──> build_report.py ──> 静态 HTML（留存/分享）
```

1. **扫描**：`python3 scripts/scan.py > scan.json`（Windows 用 `python`）
2. **分析**：Agent 读 `scan.json` + 对应平台的 `references/*.md`，产出 `analysis.json`（字段见 `examples/analysis_example.json`）
3. **报告**：`python3 scripts/server.py analysis.json` 起本地服务并打开浏览器；或 `build_report.py` 生成静态留存版
4. **摘要**：Agent 在对话里给结论先行的摘要

## 安全模型

- **扫描只读**：`scan.py` 只用标准库做统计，不写任何文件、不删任何东西。
- **三套白名单**（`server.py`）：`rm` 仅绿灯 `trash_paths`；`trash` = 绿灯 + 橙灯 `trash_paths`（橙灯永不 rm）；`open` = 上述全部 + 橙灯 `path` + 红灯 `app_paths`（非破坏性）。
- **每请求校验**：路径 realpath 解析 + 必须在 `system.safe_roots` 内 + session token + Host 白名单（防 DNS rebinding）。
- **每次点击二次确认**：浏览器 `confirm()` 后才发请求。
- **本地绑定**：只监听 `127.0.0.1`，随机端口 + 随机 token，关掉服务按钮即失效。
- **受管控环境**：若主机安全策略拦截脚本发起的删除（要求交互确认），请在资源管理器里手动删除——不要尝试绕过安全策略。

## 依赖

零第三方依赖，全部 Python 3 标准库。macOS 开箱即用；Windows 需先装 Python 3。

## 许可证

MIT — 见 [LICENSE](LICENSE)。
