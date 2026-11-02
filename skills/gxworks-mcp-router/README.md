# GXWorks MCP 任务路由 Skill

本 Skill 面向已接入 GXWorks Agent MCP 服务的 AI 客户端（如 Claude Desktop、Cursor、Codex 等），提供针对三菱 PLC 工程任务的 MCP 工具选型指南、核心调用流程、参数约束及返回结果处理规范。

## 部署与启用

将整个 `gxworks-mcp-router/` 目录（包含 `SKILL.md` 及 `references/` 子目录）复制到目标 AI 客户端的技能目录（Skills Directory）中并启用。

> [!NOTE]
> 实际可用工具列表、工具名称前缀（如是否有命名空间前缀）以及具体参数 schema，以客户端当前连接的 MCP 服务端所公布的工具定义为准。

## 目录结构与参考文档

- **[SKILL.md](SKILL.md)**：核心路由指南。按任务目标（读取、离线编辑、生成、原生编译、持续会话、仿真采样等）规划入口工具、区分各类标识符并规范回执判读流程。
- **[references/tool-catalog.md](references/tool-catalog.md)**：工具全景索引。系统整理共享工程工具与本地模式专属工具的功能定位、参数约束及适用范围。
- **[references/workflows.md](references/workflows.md)**：典型任务调用链。详解单源码与多源码编辑、ST/FBD/SFC 候选生成、已打开 GX Works2 窗口接管、后台静默编译及仿真观测的完整操作序列。
- **[references/result-handling.md](references/result-handling.md)**：回执语义与容错恢复。详解 `local`、`service`、`context` 三种运行模式下的回执判定标准、手册检索结果解读与常见异常恢复策略。
- **[references/sources.md](references/sources.md)**：源码映射与更新追踪。记录工具定义与对应 Python Core / MCP 模块的映射关系，便于协议升级时同步维护。
