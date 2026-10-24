# 来源与维护

本 Skill 原始公开来源为 `Arienax/gxworks-agent` 的 `main@e5f6ca03455c97e0feb7328a21493dcdbf7fa620`。2026-10-24 依据本地工作树补充 FBD 规则、上下文和 MCP 联合提交合同；本次修改尚未发布，不将工作树内容归于旧提交。

## 对应源码模块

| 文件 | 对应内容 |
| --- | --- |
| `src/agent_runtime/tool_policy.py` | 共享工具名、来源工具集合、发现与原生检查分类。 |
| `src/agent_runtime/tool_catalog.py` | 生成、编辑、各语言工具与原生工程操作的描述及 schema 入口。 |
| `src/agent_runtime/generation_tools.py` | FBD 生成合同：新图形走单图候选，已保存源码提供联合事务和单图备选。 |
| `src/application/fbd_context.py` | 必需语言/地址规则、任务目录、完整接口与关联源码、预算及实际支持路径。 |
| `src/application/gxw_files.py` | ST 正文/接口、FBD 调用者和声明在同一离线事务内提交。 |
| `src/plc/fbd_semantics.py`、`src/gxw/fbd_validation.py` | FBD 字面量、端口类型、连线与写入前静态检查。 |
| `src/plc/device_profiles.py`、`resources/plc_models.json` | CPU 地址编号、默认分配、配置条件及独立原生访问边界。 |
| `src/knowledge/structured_facts.py`、`retriever.py` | 当前型号事实和精确地址规则进入最终模型上下文。 |
| `src/agent_runtime/native_tools.py` | FBD 本次 `native_save` 参数校验、用户库导入与 ST 后台 `edit_*` 路由。 |
| `src/application/fbd.py` | FBD 候选保存策略、未变更时的保存处理与实际保存结果。 |
| `src/gxw/library_transfer.py` | 用户库导入后的目标项目源码、任务、调用者及声明保护。 |
| `src/gxw/offline_authoring.py` | ST 声明省略保留与完整列表替换语义。 |
| `src/agent_runtime/inspection_tools.py` | GXW/CSV/GX 会话发现、Simulator2、原生分配地址与代码步号查询参数。 |
| `src/agent_runtime/program_tools.py` | `compile_project` 与 `validate_project` 的处理流程及版本/快照要求。 |
| `src/agent_runtime/tool_errors.py` | 结构化错误码、前置条件与 `next_actions`。 |
| `src/application/engineering_session.py` | 独立 `local` 模式的 5 个扩展工具、工程选择语义与保存流程。 |
| `src/integrations/mcp/server.py` | `local` / `service` / `context` 模式的服务 instructions 与权限配置。 |
| `src/integrations/mcp/local_adapter.py` | 独立会话工具暴露与结果转换。 |

复杂参数结构以运行时连接的 `tools/list` 为准，FBD 操作参数可同时参考 `get_fbd_catalog.parameter_schema`。排查具体参数校验或实现细节时，可进一步查阅 `src/agent_runtime/engineering_schema.py` 及相应的应用层服务。

## 同步与更新

当工具新增或改名、接入模式调整、参数 schema 变更或后台原生行为更新时，对照上表模块同步更新本目录文档。同目录下的 `tool-names.txt` 记录了当前整理的 47 个工具名称，便于核对文档覆盖范围。
