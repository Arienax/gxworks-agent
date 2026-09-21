# GXWorks Agent：代码导航

面向三菱 FX PLC 的工程工作台。内置 Agent 和外部 MCP 客户端共用确定性的工程工具；不要把计划功能描述成已经实现。

## 语言职责与维护状态（2026-09-21 更新）

- **PLC 领域语义的唯一所有者是 Python Core**：IR、地址/进制/型号范围、指令与操作数、规格和编译、FBD/SFC 建模、仿真断言与设备权限不得在 UI 或原生适配器另写一套。
- Core 是无 UI 的 Python 领域实现，不是一个大文件：`plc/`、`gxw/`、`simulator/` 的领域模块与 `rendering/`；`application/` 组织用例，`integrations/` 只转协议。
- **TypeScript 仅 presentation**：表单原始值、组件状态、选择/缩放/布局、本地化、HTTP 传输、Core 返回结果的展示。默认工程、FBD 修改、声明合并、仿真输入解释由 Python 提供；不在前端推导程序正确性或安全性。
- **C# 仅 vendor/native adapter**：COM/SDK/PInvoke、厂商 ABI、资源释放、错误码与传输；执行 Core 准备的 `key/device/value` 原生调用计划，不判断 PLC 地址类别、范围、写入权限、T/C 语义或 RUN 监视含义。
- 原生适配器继续保留已有鉴权、loopback、请求大小/类型、只读接口和 Simulator2 路由隔离；职责迁移不是删除安全限制。
- **Qt = retired**：Qt/Win7 客户端、专用依赖和打包已删除。Web 是唯一继续维护的 UI；禁止恢复 Qt 兼容壳或让 Core/Web 依赖 GUI。
- 退役不等于完整 GUI parity 或 Windows/GX 验收完成；保留旧数据与可复用能力，详见 `docs/architecture/qt-retirement-audit.md`。
- 本次不引入新的架构检查器、校验层或 CI 门禁；运行已有测试只为检查现有行为是否保留。

详细约定见 `docs/architecture/language-boundaries.md`。

## 架构边界

- 内置路径：`agent_runtime/agent.py → ModelProvider / ToolRuntime → PLC Core`。
- 外部路径：`integrations/mcp → ToolRuntime → PLC Core`。工具定义来自现有注册表，不能为 Codex、Claude、Cursor 等维护另一套 PLC 工具。
- PLC 工程语义属于 Python Core；ToolRuntime 工具实现只组合 Core 用例。适配层只转换协议、上下文和结果，不直接调用 GX/仿真或操作界面。
- `model_runtime/provider.py` 负责模型协议；PLC Core 不导入模型 SDK、Agent 或 MCP。

## 重要入口

- `start-web.cmd`：Windows 发布包/源码 Web 工作台启动入口；`build-web.bat`：源码前端一键构建入口。
- `python -m gxw.decoder`：只读 GXW/POU 解析；`python -m plc.sfc`：旧 SFC 文件转需求文本。
- `src/agent_runtime/agent.py`、`src/model_runtime/provider.py`：内置编排及规范消息类型。
- `src/agent_runtime/runtime.py`、`src/agent_runtime/plc_tools.py`：共享运行时、注册表、白名单、ToolContext。
- `src/plc/core.py`、`src/plc/ir.py`、`src/plc/validation.py`、`src/plc/static_analysis.py`：确定性工程逻辑。
- `src/storage/session.py`：现有项目/版本持久化；显式可写读取可迁移旧版本，外部读取须禁止迁移写入。
- `src/integrations/mcp/`：独立 MCP 服务、上下文提供器与协议适配。
- `src/gxworks2/`、`src/simulator/`、`simulator_gateway/`：GX Works2、GX Simulator2 与 MX Component 网关边界。
- `docs/integrations/mcp.md`、`docs/integrations/codex.md`：启动、配置、已实现与未来边界。
- `packaging/pyinstaller/`：Web 与 MCP 的 PyInstaller 构建描述；不要在仓库根新增 `.spec`。
- `requirements/`：Web、MCP 与 GXW 测试的专用依赖集；根 `requirements.txt` 是无 GUI 的开发、测试及打包环境。

## 安全约束

- 外部适配器必须经过 `ToolRuntime.invoke`，遵守 `SAFE_TOOL_NAMES` 和 `ToolRegistry`。
- 不暴露任意鼠标、键盘、文件删除、`write_plc`、`force_device` 或其他低层写入。
- 独立 MCP 的候选补丁与 GX 导入保持 `confirmation_required`，没有确认执行或桌面桥接接口。Web 工作台由用户选择工作区审批模式：直接请求的本地程序校验后自动保存版本，现有 GX/仿真操作通过工作区策略或人工授权执行；不能放宽 PLC 校验、跨域保护和资源绑定检查。
- `ToolResult.data` 含 UI 私有数据；对外使用 `public_tool_result_data`，不发送 `_candidate_ir`、`_confirmed_spec`。
- stdio 的 stdout 只用于 MCP；诊断输出走 stderr。独立 MCP 使用自己的依赖集；Web 发布包同时构建 MCP 启动器。

## 验证

以下命令在仓库根目录、相应 Python 环境中按改动范围选用：默认运行受影响测试；共享运行时或架构变更考虑全量测试；MCP 相关改动运行烟测。已覆盖的测试不重复运行。

```text
python -m pytest -q
python -m pytest -q tests/test_mcp.py tests/test_plc_agent.py tests/test_plc_core_boundary.py tests/test_architecture_boundaries.py tests/test_model_provider.py tests/test_plc_ir.py
python scripts/mcp_smoke.py
```

完整无界面测试需要根目录 `requirements.txt`；仅 Web 运行使用 `requirements/web.txt`；独立 MCP 使用 Python 3.10+ 和 `requirements/mcp.txt`。未安装 MCP 时该测试模块跳过，不能据此声称 MCP 验证通过。GX/Simulator/MX 的实际集成需要相应 Windows 软件；单元测试使用临时项目和模拟后端，不操作真实 PLC。提交前检查全部改动，保留用户已有修改。


## 源码目录（完整归位）

- `src/` 根只保留 `api.py` 公共兼容入口；禁止新增根目录业务模块。
- `model_runtime/`：模型契约、能力目录、请求策略、协议、观测和验证；不导入 PLC、UI、GX 或 MCP。
- `knowledge/`：检索、重排、向量索引和模式库。SQLite 和指令注册表仍是不同职责。
- `plc/`：确定性 IR、指令、规格、校验、修复和编译；不导入 application、模型、UI 或适配层。
- `agent_runtime/`：规范工具消息、运行时、内置编排和工程工具。中立消息/运行时不依赖模型 SDK。
- `application/`：用例、状态、任务、审批、生成上下文、模型工作流；不导入 Qt。
- `rendering/`：无 GUI 的 SVG 渲染和显示编号；CSV 位于 `gxworks2/csv_export.py`。
- `inspection/`：检查和展示数据，不依赖 GUI。
- `storage/`：配置、会话、凭据；`shared/`：资源路径、语言、追踪和上下文策略。
- 旧 `model_*` / `plc_*` 等导入壳已移除；使用 `tools/source_layout.json` 的精确映射，不再导入旧根模块。
- Web/MCP 使用 `integrations` 包的模块入口；`api.py` 仅显式转出已有公共函数，内部调用使用 `application.model_api`。
- 配置位置由 `storage.config` 决定，保留显式覆盖、用户目录和旧配置兼容；知识库、目录资源、历史会话不因退役被移动或清除。
- 历史 Prompt 对照只在 `research/baselines/generation_context.py`，禁止产品导入研究目录。
- 新文件同时受 `tests/test_source_layout.py` 和架构边界测试约束；不要通过空目录、兼容壳或修改 allowlist 隐藏违规。

完整目录、测试及迁移约定见 `docs/architecture/source-layout.md`。
