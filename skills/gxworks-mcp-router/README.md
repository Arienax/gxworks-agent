# GXWorks MCP 路由 Skill

面向已连接 GXWorks Agent MCP 的客户端，整理 Mitsubishi PLC 工程任务的工具选型、参数要点与调用顺序。

使用时将整个 `gxworks-mcp-router/` 目录（含 `SKILL.md` 与 `references/`）复制到客户端的技能目录并启用。实际可用工具、命名空间前缀和参数定义以当前连接的 MCP 服务为准。

## 目录结构

- `SKILL.md`：按任务意图选择入口工具、区分各类标识并处理返回结果。
- `references/tool-catalog.md`：42 个共享工具与 5 个 `local` 模式扩展工具的参数要点及适用范围。
- `references/workflows.md`：常见读取、生成、离线编辑、原生编译、持续会话与仿真采样调用流程。
- `references/result-handling.md`：`local` / `service` / `context` 模式的回执语义与常见错误恢复。
- `references/sources.md`：对应的源码模块与同步更新位置。
