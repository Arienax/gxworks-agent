# GXWorks Agent：代码导航

面向三菱 FX PLC 的工程工作台。内置 Agent 和外部 MCP 客户端共用确定性的工程工具；不要把计划功能描述成已经实现。

## 架构边界

- 内置路径：`agent_runtime/agent.py → ModelProvider / ToolRuntime → PLC Core`。
- 外部路径：`integrations/mcp → ToolRuntime → PLC Core`。工具定义来自现有注册表，不能为 Codex、Claude、Cursor 等维护另一套 PLC 工具。
- PLC 工程逻辑属于 PLC Core、现有校验/渲染模块和 ToolRuntime 工具实现。适配层只转换协议、上下文和结果，不直接调用 GX/仿真或操作界面。
- `model_provider.py` 负责模型协议；PLC Core 不导入模型 SDK、Agent 或 MCP。

## 重要入口

- `start-web.cmd`：Windows 发布包/源码 Web 工作台启动入口；`build-web.bat`：源码前端一键构建入口。
- `src/main.py`、`src/ui/desktop/qt.py`：保留的 PyQt 桌面及 Win7 兼容层。
- `src/agent_runtime/agent.py`、`src/model_provider.py`：内置编排及规范消息类型。
- `src/agent_runtime/runtime.py`、`src/agent_runtime/plc_tools.py`：共享运行时、注册表、白名单、ToolContext。
- `src/plc/core.py`、`src/plc/ir.py`、`src/plc_*validator.py`、`src/plc/static_analysis.py`：确定性工程逻辑。
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
python -m pytest -q tests/test_mcp.py tests/test_agent_runtime/agent.py tests/test_plc_core_boundary.py tests/test_architecture_boundaries.py tests/test_model_provider.py tests/test_plc/ir.py
python scripts/mcp_smoke.py
```

完整桌面测试需要根目录 `requirements.txt`（Win7 使用 `requirements/win7.txt`）；Web 环境使用 `requirements/web.txt`；MCP 测试/烟测另需 Python 3.10+ 和 `requirements/mcp.txt`。未安装 MCP 时该测试模块跳过，不能据此声称 MCP 验证通过。GX/Simulator/MX 的实际集成需要相应 Windows 软件；单元测试使用临时项目和模拟后端，不操作真实 PLC。提交前检查全部改动，保留用户已有修改。

## 源码目录约定

`src` 根目录只保留 `main.py`、`api.py` 入口。新增业务代码进入对应包，禁止再新增根目录业务模块。
完整映射与未完成的深层拆分见 `docs/architecture/source-layout.md`。
不要在生产代码中重新导入旧的平铺模块名；不要用 `import *` 兼容壳复制有状态模块。
SVG 渲染属于无 Qt 的 `rendering/`，不能为了归类而让 Web/MCP 间接依赖桌面界面。
资源和已有用户状态路径通过 `shared.paths` 保持稳定；目录调整不得删除或迁移用户工程。
