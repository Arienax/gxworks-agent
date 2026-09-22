# Codex 作为 MCP 客户端

Codex 通过 GXWorks Agent 的 stdio MCP 启动器使用工程工具。项目内没有嵌入式 Codex Harness 或 App Server 生成后端。

先完成 [MCP 接入](onboarding.md)并绑定目标工程，再在工作台选择“连接 Codex”。应用测试真实启动器连接后，仅更新 Codex 配置中的 `[mcp_servers.gxworks]` 项，保留其他服务器、信任设置及用户配置。路径解析和原子配置写入由 [application.mcp_integrations](../../src/application/mcp_integrations.py)维护。

重启或刷新客户端的 MCP 连接后，列出工具并读取当前工程，检查工程 ID 与工作台绑定一致。读取正确后再提出编辑需求。客户端的工具调用许可与工作区中的工程审批分别生效，返回 `confirmation_required` 时尚未执行该操作。

手工配置或非 Windows 环境按 [MCP 服务连接](mcp.md#service-mode)设置启动命令。使用当前应用生成的命令，不把会话 token、模型密钥或另一机器的临时路径写入示例配置。

工具、版本选择和结果状态统一由 [MCP 接口](mcp.md)说明，不维护 Codex 专用 PLC 规则或工具目录。
