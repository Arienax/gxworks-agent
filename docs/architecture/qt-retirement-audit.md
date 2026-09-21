# Qt retirement audit

日期：2026-09-21。基线：`fix/prompt-cleanup-io-binding-20260921`，提交 `a07d9c9f4acf055e8b754a5d48306c2628cac8ad`。

## 决定与范围

按用户确认的方向，审查 Qt 独占行为，保留可复用能力，删除 Qt，而不是继续等待所有旧界面功能在 Web 上复刻。本次不引入新的 PLC 语义、模型提示词、运行时门禁、TS 领域实现或 C# 领域实现。

审查覆盖 `src/ui/` 下 28 个 Python 文件、15,994 行代码，以及入口、依赖、资源、打包、调用方和测试。Qt 主窗口、经典窗口、对话框、画布、控件、主题、翻译绑定、信号与 QThread 适配均退出产品代码。没有把旧目录改名为 legacy、复制到别的运行目录或保留一个伪装的兼容壳。

## 能力归属与处理

| 原 Qt 行为 | 审查结论 | 本次处理与剩余边界 |
| --- | --- | --- |
| 默认工作区目录 | `SessionStore` 仍动态导入 Qt 的 `QStandardPaths`，是唯一发现的非 UI 运行时 Qt 导入 | 用纯 Python `default_workspace_dir()` 保留旧组织/应用标识。显式目录优先，其次 `PLC_AI_WORKSPACE_DIR`；无隐式搬迁、清空或重新生成工程 |
| SFC 图解释、地址分配、图模型 | 已在 `plc/sfc.py`，不是 Qt 独占领域算法 | 保留现有算法，不复制或重写；删除图形画布 |
| 旧 `.sfc` 文档加载 | Qt 仍拥有 version-1 `blocks/temp_id/connections/source_id/target_id` 的输入投影 | 增加 `document_graph`、`document_requirement` 和只读模块 CLI；分离副本保留节点属性与几何数据，不修改源文件。它只转换需求，不是 GX 原生 SFC 编译器 |
| GXW 结构化 POU 选择/检查 | Qt 仅选择文件/POU 并显示 `gxw.decoder` 的结果 | 保留 decoder、resolver、未知记录及既有命令；增加 `--list-programs`。不声称 Structured decoder 覆盖所有 GXW 程序形式 |
| 原生 CSV 保真回读 | 算法已在 `gxworks2.csv_importer`，QThread 仅调用 `materialize_gxworks2_version` | 原解析、原始指令、注释、原文件保留和往返校验继续存在。不放宽 Web 对未验证 vendor 指令的 `unsupported` 边界；Qt fallback 窗口不再提供 |
| 生成、增量编辑、语言和流式输出 | 共享实现在 `application.generation/planning/review` 与模型运行时 | 删除信号封装；测试直接调用原应用服务，保留 IR/CSV/ST/SVG、修订号、局部编辑、语言快照、fallback 和流式断言 |
| GX 导入、同步、回滚、仿真、报告 | 共享能力在 `application.execution`、`gxworks2/`、`simulator/`、调试服务和 storage | 不删除 COM 协调器、桌面资源锁、同步基线、回滚证据、报告数据和原生 helper。删除仅为 Qt 控件/QThread 存在性编写的测试 |
| 模型配置、密钥、附件、历史会话和多语言 | headless 配置/存储/服务已经存在 | 原数据格式和凭据路径解析保持，不删除共享语言目录；界面控件测试退出，非 GUI 数据行为测试保留 |
| 离线 OpenAI SDK 自检入口 | 旧桌面 `run()` 的无窗口分支 | `scripts/web_entry.py` 保留 `--self-test-openai-sdk`；真实 SDK 自检仍由原 provider 执行，不启动 Web/GX 或调用模型 |

## 明确退役而不是声称已迁移的功能

不再提供 Qt5/Qt6 桌面客户端、Win7 产品包、旧图形 SFC 画布和 Qt 专属原生回读交互。`.sfc` 文件仍可读取并转换成需求文本，但 Web 没有因此获得新的图形编辑器。未知原生指令的底层保真处理仍存在，但 Web 的编辑/审批覆盖范围没有扩大。

历史迁移清单保留，顶部标明它已被本次主动退役决定取代。尚未执行的 Windows/GX Works2/GX Simulator2/MX Component、锁屏/RDP、真实设备回滚和完整安装包人工验收仍然未完成，不从删除 Qt 或通过离线测试推导为完成。

## 数据与命令

Windows 旧标准默认工作区为 `%APPDATA%/PLC AI Studio/PLC AI Workbench/workspace`。macOS/Linux 保留对应应用数据根及相同标识。特殊环境可继续通过显式目录或 `PLC_AI_WORKSPACE_DIR` 指向原工作区。本次不会替用户选择、打开或修改生产工程。

源码环境中设置 `PYTHONPATH=src` 后：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m integrations.web --workspace "D:\PLCWorkspaces\existing-workspace"
python -m gxw.decoder "example.gxw" --list-programs
python -m gxw.decoder "example.gxw" --program "1.Program.pou"
python -m plc.sfc "control_flow.sfc"
```

独立原生 CSV 解析接口仍可在 Python 中使用，不会调用 GX 或生成 Web 候选：

```python
from gxworks2.csv_importer import parse_gxworks2_csv
parsed = parse_gxworks2_csv("program.csv", "comments.csv")
print(parsed.network_instructions)
```

这些命令和接口仅在其现有格式能力范围内工作，不将“读取成功”描述为原生编译通过。

## 删除与构建

删除 `src/ui/`、`src/main.py`、Qt/Win7 的两个 PyInstaller spec、`requirements/win7.txt`、Qt 专用 `tools/build_release.ps1` 和只被 Qt 使用的 codicons 资源。保留 `web.spec`、`mcp.spec`、Web 启动/构建脚本、GUI 自动化所需的 Windows vendor 集成、共享图标和知识库。

根 `requirements.txt` 改为 headless Web/Core 开发、测试和打包依赖，不安装 Qt。现有 source-layout workflow 用完整 headless suite 与 Windows Web/MCP 打包烟测替代 Qt 安装/桌面打包和过渡期 missing-test 比较；没有增加 workflow 文件。完整 pytest 保留真实失败，不新增旧失败 allowlist 或自动 skip。Confirmed Generation workflow 移除三条旧 Qt-worker 排除表达式；相应 IR 集成测试现在直接运行。

## 测试处理

删除六个纯 Qt 测试文件：`test_codicon_safety.py`、`test_combo_option_cards.py`、`test_config_dialog_profiles.py`、`test_gxworks2_simple_bridge_ui.py`、`test_legacy_qt_smoke.py`、`test_spec_review_workbench.py`。

混合测试文件只移除控件、QThread、对话框或已删除入口的断言，保留设置、附件、显示名称、语言、原生 CSV、同步、报告和执行协调器的领域测试。三条 CompilerThread/IR 测试、生成语言/fallback 测试及仿真计划流式测试改为调用已有无界面服务，不以删除测试代替迁移。

新增覆盖：旧默认工作区身份和优先级、无创建/迁移行为、旧 SFC 文件投影与只读转换、GXW POU 列举和只读检查、Web SDK 自检分支。`tests/README.md` 的现有归属清单同步更新。

## 本地验证与限制

环境为 Linux / Python 3.13.5，未安装任何 Qt binding。生产 Python 的静态导入审查未发现 Qt、PySide、qtpy 或 `ui` 依赖；`compileall` 通过。退役相关最终定向回归为 **266 passed**。

完整原分支对照为 **2944 passed、22 failed、19 skipped、10 collection errors**。第一次完整退役分支检查为 **3031 passed、14 failed、19 skipped、0 collection errors**。13 条失败与原分支重合；另一个语言目录检查此前因模块顶层导入 Qt 无法收集，单独执行同一检查确认原分支与候选均缺失同样 9 条英文翻译，没有新增条目。没有改写这些无关失败以制造绿色结果。

完整失败涉及既有 GX 环境模拟、修复结果断言、提示词文字断言、Windows 路径/原生证据 hash，以及本机缺少 OpenAI SDK。MCP/独立 MS-CFB reader 等依赖不足导致的 skip 不计为验证通过。最终完整结果与逐项对照随交付包的验证记录提供。

Node 测试本机通过 32 项；`module-resolution.test.mjs` 因缺少 `typescript` 不能运行。离线 `npm ci` 缓存不完整，未完成前端生产构建。未在本机执行 Windows 发布包构建、真实 SDK/stdio MCP 烟测、GitHub CI 或 GX/PLC 操作。CI 配置的更新是待运行的验证步骤，不是其已通过的声明。

