# 按任务选择工作流

## 新建 Direct / Design

完整步序、时序、算法、跨品牌/语言移植以及明确要求“直接生成”使用 Direct。开放式工艺需要权衡方案时使用 Design；不能仅因程序复杂就强制 Design。

Direct：创建或选定实际工程，完整传入需求，读完 `get_generation_context`。按 `generation_input`、`output_contract`、`instruction_research` 进行实现；必要的手册文档读完后再提交顶层 `implementation_review`。不生成试格式示例版本，不在提交前替换用户的地址、时序或停止/恢复语义。

Design：`get_design_context` 返回当前分析合同；`propose_design_plan` 提交分析。遇到 `repair_required` 按返回的 `repair_id` 和路径补交 repairs，不重写未要求修改的部分。将必要参数和方案选择一起展示给用户；`confirm_design_plan` 的 `draft_id`、`approach_id` 使用当前草稿，`parameter_answers` 使用用户实际提供的答案。确认后按 `next_actions` 从确认规格生成。可选推断无法验证不等于用户显式约束可以忽略。

宿主包装器仅在实际可用且符合用户选定模型时使用。包装器与 MCP 服务版本可能不同，不编造包装器参数，也不要求所有宿主安装同一包装器。

## 规格文档

`get_project_spec` 读取文档；`update_project_spec` 按当前 schema 提交 changes，并用读取结果的 `draft_revision` 作为 `expected_revision`。这是修改草稿，不是确认需求。用户确认展示的具体版本后，才提交 `confirm_project_spec(expected_revision=该修订, user_confirmed=true)`。

`get_generation_context(workflow="plan_review")` 是显式规格评审路径；服从它返回的草稿更新合同。不要把所有新建 Direct 任务都路由进 plan_review。生成/编辑使用当前确认规格；对旧版本的检查也要注意其保存时的规格快照，不能将两个版本的要求混用。

## 四语言生成与已有源码修改

| 目标 | 当前源码合同中的常用路径 |
| --- | --- |
| Ladder 新建 | `create_program_candidate(ladder=...)`；使用生成上下文输出 schema，不自行生成 canonical IR。 |
| Ladder 修改 | 读取当前程序/相关网络；按输出合同提交完整或局部候选，或使用 `patch_program`。 |
| ST 新建 | 支持时 `create_st_candidate(operation="generate", st_text=..., declarations=...)`；`create_st_program` 用于向已有工程的实际 resource/task 添加程序。 |
| ST 修改 | `read_st_project` 或 `read_gxw_source` 读取正文、声明和身份，按返回合同编辑。 |
| FBD 新建 | `get_fbd_catalog`、生成上下文 → `create_fbd_candidate(operation="generate", model=...)`。model 的形状和类型取自当前合同。 |
| FBD 修改 | 读取图形、接口和定义，使用单图候选或联合源码事务。复杂控制可用职责明确的 ST FB 组装，缺少实现先创建完整 FB。 |
| SFC 新建/修改 | 当前版本支持时用 `create_sfc_candidate(operation="generate", ...)` 新建；已有工程先 `read_sfc_project`，按合同编辑。旧服务若仅支持已有源码，不能发送新版生成参数。 |

当前 public 的 ST 新建字段为 `st_text`，不同连接可能暴露旧字段；以实际 schema 为准，不同时塞入猜测的别名。创建 ST Program 需要已有资源和任务；新建 ST 工程使用 generate 合同。

FBD 首个 `create_st_fb` 可按支持的 CPU 无基底创建 FBD 工程，后续定义和主图以实际保存版本为基础。独立状态用独立实例，使用已读端口与类型，不能只画没有实现的空块。模板中的程序名可能被原生保存映射到实际名称，提交后重新读取实际源码身份再编辑，不能继续假定模板名就是保存后的名称。

ST 正文、接口、FBD 调用者和声明有联动时，优先使用 `edit_gxw_project` 同一离线事务提交；固定明确的 `version_id` 和源码身份。它不接受 `generation_context_id`。声明字段省略通常保留原表，提交列表通常替换整张表；按操作合同保留未改成员。软元件注释同样遵守当前替换/保留合同，不把缺省当删除。

## 外部文件与只读分析

外部 GXW 先 `inspect_gxw_project`；需要保存成 Agent 版本时再 `import_gxw_project`。已打开工程先发现窗口，再读取绑定快照。CSV 用 CSV 检查工具。`source_ref` 可传给接受此参数的分析工具，不把它当版本 ID。

多程序文件用读取结果选程序；用户已经给了明确程序时不再提问。仅在真实歧义会改变修改对象时确认。失败或身份未知时保留原文件，不创建另一个同名工程来假装接管成功。

## GXW 后台编译与持续可见编辑

普通交付优先复用共享保存后的 GXW 和已有结果。仅需检查保存版本时使用 `validate_project(native=true)`；需要显式原生步骤时按 `execute_native_engineering` schema 构建后台计划，绑定真实版本。编译、保存、重开分别看执行回执。`compile_project` 只构建 Core 产物。

用户要求持续可见编辑时才启动 live：首次保存返回的 `session_id`，后续读取 `get_native_engineering_session`，按当前 revision 使用 continue。重复 start 会返回已有会话，不代表执行了新 commands。暂停用 `pause_native_engineering_session`；续做未完成步骤用合同指定的 resume_pending，不能重放已经执行的修改。

会话恢复要验证原工作路径和窗口身份；MCP 重启不应创建第二份工作副本。保存是明确阶段，产生新版本以实际回执为准。原生编译后的地址可用 `read_native_device_assignments` 读取；任何源码编辑都会使旧编译与地址失效。声明的地址与编译器分配结果分别描述。

## Simulator2 观察

发现目标后查看状态、读取设备或 trace。`connect_simulator` 连接观察通道；`prepare_simulator_scenario` 准备场景计划，不是已执行测试。读取 `get_simulator_trace` 后结束自己的采样。除非返回证实程序身份匹配，不能把观测结果归因于本次生成程序。下载、RUN、写入/强制设备需要相应工具及明确授权，不能从生成或观察请求推导。

## 指定本地模型与 token 统计

先确认用户指定的代码目录和已保存配置；仅查看脱敏后的配置标识、模型名、接口地址和参数，不输出密钥。内置 Workbench/模型运行时才使用保存的 provider 配置，独立 MCP 通常由宿主模型消费上下文。

复用现有运行入口和配置，不为测试更改全局活动模型或模型调优参数。用户要求多轮独立测试时，每轮使用同一完整输入与新工程/会话；保留实际生成结果与所有请求，包括运行流程自己的修复或重试，并分别汇总轮次和请求数量。

统计流式用量前，确认当前 provider 合同支持并启用了 usage 返回；只调整本次观测选项，不改全局配置或采样参数。输入/输出 token 优先用 provider 的实际 usage，说明是否包含推理 token；RAG token 单独统计实际送入模型的知识文本/对象，说明所用 tokenizer 或估算方法。MCP 分页的预算估算、字符数和整段 system prompt 长度不能冒充精确 RAG token。若服务未返回 usage 或模型 tokenizer 不可用，如实标记缺失或估算，不虚构精确数。
