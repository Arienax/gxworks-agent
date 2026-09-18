# GXWorks Agent：代码导航

面向三菱 FX PLC 的工程工作台。内置 Agent 和外部 MCP 客户端共用确定性的工程工具；不要把计划功能描述成已经实现。

## 架构边界

- 内置路径：`agent_runtime/agent.py → ModelProvider / ToolRuntime → PLC Core`。
- 外部路径：`integrations/mcp → ToolRuntime → PLC Core`。工具定义来自现有注册表，不能为 Codex、Claude、Cursor 等维护另一套 PLC 工具。
- PLC 工程逻辑属于 PLC Core、现有校验/渲染模块和 ToolRuntime 工具实现。适配层只转换协议、上下文和结果，不直接调用 GX/仿真或操作界面。
- `model_runtime/provider.py` 负责模型协议；PLC Core 不导入模型 SDK、Agent 或 MCP。

## 重要入口

- `start-web.cmd`：Windows 发布包/源码 Web 工作台启动入口；`build-web.bat`：源码前端一键构建入口。
- `src/main.py`、`src/ui/desktop/qt.py`：保留的 PyQt 桌面及 Win7 兼容层。
- `src/agent_runtime/agent.py`、`src/model_runtime/provider.py`：内置编排及规范消息类型。
- `src/agent_runtime/runtime.py`、`src/agent_runtime/plc_tools.py`：共享运行时、注册表、白名单、ToolContext。
- `src/plc/core.py`、`src/plc/ir.py`、`src/plc/validation.py`、`src/plc/static_analysis.py`：确定性工程逻辑。
- `src/storage/session.py`：现有项目/版本持久化；默认桌面读取可迁移旧版本，外部读取须禁止迁移写入。
- `src/integrations/mcp/`：独立 MCP 服务、上下文提供器与协议适配。
- `src/gxworks2/`、`src/simulator/`、`simulator_gateway/`：GX Works2、GX Simulator2 与 MX Component 网关边界。
- `docs/integrations/mcp.md`、`docs/integrations/codex.md`：启动、配置、已实现与未来边界。
- `packaging/pyinstaller/`：桌面、Win7 与 Web 的 PyInstaller 构建描述；不要在仓库根新增 `.spec`。
- `requirements/`：Web、MCP、Win7 与 GXW 测试的专用依赖集；根 `requirements.txt` 仅保留 Qt 桌面开发环境。

## 安全约束

- 外部适配器必须经过 `ToolRuntime.invoke`，遵守 `SAFE_TOOL_NAMES` 和 `ToolRegistry`。
- 不暴露任意鼠标、键盘、文件删除、`write_plc`、`force_device` 或其他低层写入。
- 独立 MCP 的候选补丁与 GX 导入保持 `confirmation_required`，没有确认执行或桌面桥接接口。Web 工作台由用户选择工作区审批模式：直接请求的本地程序校验后自动保存版本，现有 GX/仿真操作通过工作区策略或人工授权执行；不能放宽 PLC 校验、跨域保护和资源绑定检查。
- `ToolResult.data` 含 UI 私有数据；对外使用 `public_tool_result_data`，不发送 `_candidate_ir`、`_confirmed_spec`。
- stdio 的 stdout 只用于 MCP；诊断输出走 stderr。依赖保持可选，不向 Win7 桌面依赖集加入 MCP。

## 验证

以下命令在仓库根目录、相应 Python 环境中按改动范围选用：默认运行受影响测试；共享运行时或架构变更考虑全量测试；MCP 相关改动运行烟测。已覆盖的测试不重复运行。

```text
python -m pytest -q
python -m pytest -q tests/test_mcp.py tests/test_plc_agent.py tests/test_plc_core_boundary.py tests/test_architecture_boundaries.py tests/test_model_provider.py tests/test_plc_ir.py
python scripts/mcp_smoke.py
```

完整桌面测试需要根目录 `requirements.txt`（Win7 使用 `requirements/win7.txt`）；Web 环境使用 `requirements/web.txt`；MCP 测试/烟测另需 Python 3.10+ 和 `requirements/mcp.txt`。未安装 MCP 时该测试模块跳过，不能据此声称 MCP 验证通过。GX/Simulator/MX 的实际集成需要相应 Windows 软件；单元测试使用临时项目和模拟后端，不操作真实 PLC。提交前检查全部改动，保留用户已有修改。


## 源码目录（完整归位）

- `src/` 只保留 `main.py` 和 `api.py` 两个稳定入口；禁止新增根目录业务模块。
- `model_runtime/`：模型契约、能力目录、请求策略、协议、观测和验证；不导入 PLC、UI、GX 或 MCP。
- `knowledge/`：检索、重排、向量索引和模式库。SQLite 和指令注册表仍是不同职责。
- `plc/`：确定性 IR、指令、规格、校验、修复和编译；不导入 application、模型、UI 或适配层。
- `agent_runtime/`：规范工具消息、运行时、内置编排和工程工具。中立消息/运行时不依赖模型 SDK。
- `application/`：用例、状态、任务、审批、生成上下文、模型工作流；不导入 Qt。
- `rendering/`：无 GUI 的 SVG 渲染和显示编号；CSV 位于 `gxworks2/csv_export.py`。
- `inspection/`：检查和展示数据；Qt 控件只在 `ui/desktop/`。
- `storage/`：配置、会话、凭据；`shared/`：资源路径、语言、追踪和上下文策略。
- `src/ui/desktop/main_window.py` 是工作台控制器；styles、workers、dialogs、workbench 和 SFC 已分离。
- 旧 `model_*` / `plc_*` 等导入壳已移除；使用 `tools/source_layout.json` 的精确映射，不再导入旧根模块。
- `src/main.py` 保持桌面启动方式；`api.py` 仅显式转出已有公共函数，内部调用使用 `application.model_api`。
- 配置仍位于源码 `src/config.json` 或打包可执行文件旁；知识库、目录资源、历史会话不随模块位置变化。
- 历史 Prompt 对照只在 `research/baselines/generation_context.py`，禁止产品导入研究目录。
- 新文件同时受 `tests/test_source_layout.py` 和架构边界测试约束；不要通过空目录、兼容壳或修改 allowlist 隐藏违规。

完整目录、测试及迁移约定见 `docs/architecture/source-layout.md`。
