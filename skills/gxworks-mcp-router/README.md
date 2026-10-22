# GXWorks MCP 路由 Skill

入口：`SKILL.md`。完整复制 `gxworks-mcp-router/` 到所用 Agent 支持的技能目录，再按宿主的技能加载方式启用。不要只复制主文件而遗漏 `references/`。具体技能目录取决于宿主，不假定某一种 Agent 的安装路径。

前提是 GXWorks Agent MCP 已经连接；本文件包不会安装、启动或授权 MCP，也不会改变现有工程审批配置。工具命名空间和参数以当前服务器暴露内容为准。

主文件负责意图分流；`tool-catalog.md` 覆盖 42 个共享工具及 5 个 local 模式扩展；`workflows.md` 给出常见调用链；`result-handling.md` 处理结果语义和失败恢复；`sources.md` 记录提炼来源。

该包只根据公开仓库编写，不含私有资料内容或私有资料路径。它是新生成的 Skill 文件，不是已提交到上游仓库的变更。
