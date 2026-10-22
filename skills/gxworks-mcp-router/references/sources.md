# 来源与维护

本 Skill 从 `Arienax/gxworks-agent` 的公开 `main` 读取，提交为 `278edb699dc40e9658970bb66095edeed8bd47b1`，读取日期 2026-10-22。主文件和参考表是对接口及实现的任务化归纳，不是工具运行结果，也不代表已经安装全部可选组件。

## 直接核对的来源

| 文件 | 用途 |
| --- | --- |
| src/agent_runtime/tool_policy.py | 共享工具名、来源工具集合、发现与原生检查分类。 |
| src/agent_runtime/tool_catalog.py | 生成、编辑、语言工具和原生工程操作的描述及 schema 入口。 |
| src/agent_runtime/inspection_tools.py | GXW/CSV/GX 会话发现、Simulator2、原生分配与代码查询参数。 |
| src/agent_runtime/program_tools.py | compile_project 与 validate_project 的真实处理差异；版本/快照要求。 |
| src/agent_runtime/tool_errors.py | 结构化前置条件和 next_actions。 |
| src/application/engineering_session.py | 独立 local 模式的五个额外工具、选择语义与保存流程。 |
| src/integrations/mcp/server.py | local / service / context 的服务 instructions 与权限边界。 |
| src/integrations/mcp/local_adapter.py | 独立会话工具暴露及结果转接。 |

工具复杂参数必须从连接的 `tools/list` 取；FBD 参数还从 `get_fbd_catalog.parameter_schema` 取。需要源码级排查时再阅读 `src/agent_runtime/engineering_schema.py` 和相应应用处理函数，不用静态 Skill 替代源码。

## 更新条件

工具新增/改名、模式改变、schema 变更、后台原生行为变更时，重新核对上表与当前服务。已经存在于公开分支但尚未合入或未进入安装包的能力，不作为默认行为。

工具清单汇总在同目录 `tool-names.txt`，用于检查文档覆盖，不是宿主 allowlist。不要硬性要求运行时工具数量等于 47；不同模式和版本可以不同。

Skill 的打包采用 Agent Skills 的 `SKILL.md` + YAML frontmatter + 按需 references 结构。它不注册 MCP、不提供 GX Works2、没有预设全访问权限，也不要求持有源码才能进行普通工程任务。
