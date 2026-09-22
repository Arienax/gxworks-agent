# MCP 接入

外部 Agent 通过 MCP 使用同一组工程工具。先[启动 Web 工作台](../guides/getting-started.md)，打开目标工程，再在“设置 → 模型 → Integrations / MCP”连接客户端。

## Windows 本地服务

工作台把 loopback 地址、Agent 凭据及绑定工程保存到本机凭据存储。选择“测试 MCP 连接”会绑定当前工程并调用真实启动器的检查入口。服务启动和项目绑定的实现分别在 [integrations.web.__main__.main](../../src/integrations/web/__main__.py)与 [service_credentials.bind_project](../../src/integrations/mcp/service_credentials.py)。

普通客户端使用 `gxworks-agent-mcp` 启动器，不需要在客户端配置中复制 token、工作区或服务 URL。保持工作台运行；服务停止后客户端无法执行工程工具。切换到另一工程后重新绑定并测试，客户端不会猜测浏览器当前选择。

## Codex 和其他客户端

选择“连接 Codex”前先测试连接。应用仅更新其管理的 MCP 配置项，保留其他服务器和用户设置。客户端专用步骤见 [Codex](codex.md)。其他支持 stdio MCP 的客户端使用同一启动器和绑定流程。

源码版与发布包版的启动命令由 [application.mcp_integrations](../../src/application/mcp_integrations.py)生成。复制界面给出的完整命令，避免用另一目录的 Python 启动器连接错误实例。

## Headless 或显式服务配置

无本地凭据发现的环境可显式传入 loopback 服务地址、Agent token 环境变量及工程 ID。离线读取已有 SessionStore 时使用 `--standalone`。两种模式的参数、上下文选择和结果状态见 [MCP 接口](mcp.md)。

模型设置与 MCP 客户端设置独立：外部客户端负责它自己的模型请求，工作台 MCP 负责工程工具。审批规则仍由[工作区策略](../architecture/approval-modes.md)决定。
