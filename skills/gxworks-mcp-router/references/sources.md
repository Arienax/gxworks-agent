# 来源与维护

本次依据 `D:\gxworks-agent-public` 于 2026-11-09 的本地工作树整理，HEAD 为 `9ff788da877b375afd9e98f9b3b1ef18be863b3f`。工作树包含尚未提交的新 RAG 接入与 public 修复；这些能力不归于该旧提交，也不代表已发布或当前连接服务已经更新。`D:\gxworks-agent-integration` 是另一份中间版本，不应当作当前运行服务。

## 按问题查源码

| 入口 | 负责内容 |
| --- | --- |
| `src/agent_runtime/tool_policy.py`, `tool_catalog.py`, `engineering_schema.py` | 共享工具集合、描述、参数合同。 |
| `src/application/engineering_session.py` | local 会话扩展、选择版本、提案与共享保存。 |
| `src/integrations/mcp/server.py` | local/service/context 的 instructions 与权限边界。 |
| `src/application/design_session.py` | Design 草稿、修复、确认方案与 parameter_answers。 |
| `src/agent_runtime/specification_tools.py` | 规格读取、draft_revision、草稿修改和确认。 |
| `src/agent_runtime/generation_tools.py` | 语言/版本对应的生成输入、output_contract 和 review 合同。 |
| `src/application/read_delivery.py` | 文档分页、游标与引用交付。 |
| `src/shared/model_context.py` | 默认模型技术投影、来源维护信息与调试视图。 |
| `src/knowledge/structured_facts.py`, `retriever.py` | 当前型号结构化事实、检索与覆盖。 |
| `src/plc/instruction_knowledge.py` | 指令族组合及编程通则的运行时读取。 |
| `src/application/source_plan.py` | 用户显式实现约束与实际源码映射检查。 |
| `src/application/gxw_delivery.py` | 四语言 GXW 优先交付、降级和原生结果。 |
| `src/application/gxw_files.py`, `src/gxw/offline_authoring.py` | 联合源码修改、完整列表/省略保留规则。 |
| `src/agent_runtime/program_tools.py`, `native_tools.py`, `inspection_tools.py` | Core 检查、原生计划、来源发现和模拟器观察。 |
| `src/model_runtime/provider.py`, `src/storage/config.py` | 内置模型请求、保存配置和 usage；不是 MCP 宿主模型配置。 |

## 维护规则

同步仓库 `skills/gxworks-mcp-router/` 与实际安装目录；仅修改文档不会重新加载 MCP 服务。新增或调整工具时从上述注册处核对，不在多份文件中复制完整 schema。`tool-names.txt` 为共享工具、local 扩展与 MCP 会话资料恢复工具名称的并集，实际连接仍可能是其中一部分。

`SKILL.md` 的 `mitsubishi-programming-system:start/end` 标记区块会被 `programming_system_prompt()` 直接放入内置生成模型的 system prompt。路由、架构和操作说明放在标记外；改区块内的文字属于生成行为变更，需要单独考虑影响。本次保持该区块原文。

维护后检查 frontmatter、相对链接、工具注册名称，以及运行时是否仍能提取编程通则。文档更新无需运行整个 PLC 测试集，更不能因此默认启动原生 IDE 或模拟器。
