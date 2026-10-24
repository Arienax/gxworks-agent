# 接入模式、回执判读与错误恢复

## 三种接入模式的提交结果

| 模式 | 识别特征 | 生成与编辑工具的返回结果 |
| --- | --- | --- |
| 独立 `local` | 服务 instructions 标明独立 MCP，并暴露 `list_projects`、`create_project`、`select_project`、`import_gxw_project`、`get_project_artifacts` | 通过共享事务直接保存工程版本，返回 `version_id` 与 `save_result`（`draft` 策略保存的中间稿可保持未激活状态）。 |
| Web `service` | 服务连接已启动的本地 Web 工作台运行时 | 通常生成待审阅提案（`status="pending"`，含 `proposal_id`），由操作员在工作台审阅或批准。 |
| 固定 `context` | 服务绑定单一工程与版本，instructions 说明仅负责候选生成 | 返回候选内容与 `confirmation_required` 状态，由外层调用方决定后续落盘流程。 |

保存每次调用返回的 `project_id`、`version_id`、`proposal_id` 与 `execution_job_id`，以便后续查询或继续操作。

## 各阶段回执的含义

| 阶段 | 回执依据 | 说明与后续衔接 |
| --- | --- | --- |
| 源码读取 | 读取工具返回源码结构、指令列表与可编辑范围 | 可直接用于代码解释、局部定位或构造编辑请求。 |
| 候选生成 | 候选工具返回生成的程序结构与本地校验结果 | 在 `service` / `context` 模式下表示候选已就绪；在 `local` 模式下同时观察是否已完成版本保存。 |
| 版本保存 | 返回有效的 `version_id` 与 `save_result` | 工程版本与衍生文件已落盘，可继续调用 `get_project_artifacts` 或启动原生编译检查。 |
| 本地静态检查 | `validate_current_program`、`validate_project(native=false)` 或 `get_diagnostics` 返回通过 | 证明结构、语法与已知指令约束通过 Core 静态检查；需要编译器结论时继续运行原生编译检查。 |
| 原生编译检查 | `validate_project(native=true)` 返回 `compilation`、`public_check`、`current_source_check` | 反映 GX Works2 对当前保存 `.gxw` 的编译与代码检查结果；查看各项子状态以确认是否全部通过。 |
| 保存代码比对 | `compare_native_compiled_code` 返回资源与三通道比对结果 | 说明 `.gxw` 内保存的编译结果与当前源码重新编译的输出是否一致。 |
| 模拟器观察 | `get_simulator_status`、`read_simulator_devices` 或 `get_simulator_trace` 返回采样数据 | 反映当前连接的 Simulator2 目标状态与信号波形。 |

## 常见错误与恢复方式

| 状态或错误码 | 常见原因 | 处理方式 |
| --- | --- | --- |
| `selection_status="none"` | 当前会话尚未选中 Agent 工程 | 只读外部文件或 GX 窗口时直接调用发现/快照工具；需要操作工程版本时先 `list_projects` → `select_project`，或调用 `create_project` / `import_gxw_project`。 |
| `NO_PROJECT_SELECTED` / `NO_PROGRAM_SOURCE` | 当前工具需要已选中的工程或已保存的 `.gxw` 版本 | 按返回的 `next_actions` 先选中/导入含 `.gxw` 的版本；若只需分析临时快照，改用接受 `source_ref` 的读取工具。 |
| 参数校验错误 | 参数字段与当前工具 schema 或读取到的对象名称不一致 | 对照 `tools/list` schema 及上一步读取到的程序名、声明表名、`record_index` 修正参数后重交。 |
| `UNKNOWN_TOOL` 或工具未暴露 | 当前接入模式未注册该工具（如在 `service` 模式下调用 `local` 专有工具） | 查阅当前连接暴露的工具清单，改用当前模式支持的对应入口。 |
| 原生组件启动或协议错误（如 `WORKSPACE_*`） | 本地 GX Works2 环境、辅助组件版本或桌面会话条件未满足 | 记录返回的 `error.code`、`details` 与失败阶段并如实告知用户，区分环境配置问题与 PLC 源码编译错误。 |
| 原生编译报出源码诊断 | GX Works2 编译器或代码检查发现程序/声明错误 | 读取返回的诊断详情与定位信息，按用户要求修正对应源码或声明后重新提交。 |
| 写入超时或结果未知 | 原生操作或长耗时任务尚未完成或连接中断 | 先调用 `get_operation_status` 或 `get_native_engineering_session` 查询实际完成阶段，需要恢复前台会话时使用 `rebind`。 |
| `pending` / `confirmation_required` / `execution_consent_required` | 当前操作等待工作台批准或操作员配置原生执行许可 | 向用户说明当前待确认状态、提案编号或所需的执行许可配置。 |
| `source_ref` / `session_id` / `target_ref` 失效 | 临时快照过期、GX 窗口重启或模拟器重新连接 | 重新调用对应的发现、快照或连接工具获取最新标识。 |

## 手册检索结果的判读

`search_plc_manual` 先按 `delivery.next` 读完并合并对象；解析后的 `document` 包含 `results` 与 `coverage`。检索命中是“有相关证据”，不是“事实已成立”，也不是“可调用”；先读 `coverage`，再读命中正文。

| 字段 | 含义 | 不能由此声称 |
| --- | --- | --- |
| `coverage.targets[].identity_found` | 该指令、软元件或错误码的身份是否被定位 | 被问的具体方面已回答。 |
| `coverage.targets[].status` | `complete` 表示被问方面已有 `source_verified` 事实；`incomplete` 附带 `reason` 与逐项 `aspects` | 未成立方面可以由相邻记录或常识补齐。 |
| `aspects[].status` | `answered`（已核实）、`unverified`（有声明未核验）、`missing`（该方面无事实）、`unresolved`（仅有未知或冲突） | `unverified`/`missing`/`unresolved` 都按未建立处理，说明缺什么，而不是给出猜想的取值。 |
| `call_available` / `call_admission` | 当前语言与工程能否构造调用；`st_not_admitted`、`unverified` 分别表示未准入和未核验 | 不能反推手册语义缺失或该指令不存在。 |
| `documented_st_call_available` / `core_call_available` | 前者说明手册 ST 调用知识是否已核实；后者说明 Core 的调用能力。旧字段 `st_call_available` 仍表示 Core 准入。 | 手册中有调用式不等于 Core 已实现其执行语义。 |
| `results[].knowledge` 中的 `facts[].knowledge_status` | `source_verified`、`candidate_evidence`、`declared_unverified`、`unknown`、`conflict`、`not_applicable` | `candidate_evidence` 与 `declared_unverified` 不能当作核实结论；`conflict` 与 `unknown` 必须原样披露。 |
| `count` / `top_k` | `top_k` 只限制补充资料；明确目标及依赖不计入此限额，`count` 可以更大 | 不能据此认为手册没有相关内容，也不能把低相关记录算作答案。 |

错误码与症状分开处理：给出具体错误码时只把命中该码的记录当作候选，`corpus="absent"` 且 `reason="exact_fact_not_found"` 时表示实际查库无匹配；`delivery="pending"` 只表示尚待交付；只描述症状时返回的是候选错误码，须标明是候选并说明其 `cause`、`recovery`、`trigger` 是否 `unknown`。

空 `query` 返回 `INVALID_ARGUMENTS`；`UNKNOWN_PLC_MODEL` 表示型号不在手册检索范围（`details.known_model_count`），`KNOWLEDGE_UNAVAILABLE` 表示该型号事实尚未随插件发布（`details.knowledge_status`）。两者都不是 GX Works2 工程能力结论。先核对实际 CPU 身份及资源安装，不能为取得结果而把目标换成同系列另一型号。若任务明确要求修复知识缺口，按私有资料路线补齐来源、提炼与运行路径；纯工程调用则报告具体缺项。

精确地址命中的 `device_address_rule` 是当前 CPU 的编号规则，保留进制、空洞和字内位条件；默认分配、可配置最大范围、共享容量及硬件/版本条件分别读取。它不证明当前工程分配，也不表示模拟器或设备读写已获支持。

## FBD 上下文与静态诊断

`get_generation_context` 成功后按 `generation_input.language_rules` 和 `device_address_rules` 使用必需规则，按 `supported_paths` 选择提交方式。`get_fbd_catalog` 的目录命中不是规则上下文已齐全；`missing_templates` 也不能被替换成名称相近的块。

Core 诊断使用 `fbd_*` 标识；提交被拒绝时读取 `error.findings` 中的源码、表达式、端口和位置。它与 GX Works2 返回的 C1020、C2054、C2024、C2025 等原生错误码分属不同来源，不能把静态检查结果冒充原生编译结果。`draft` 仍拒绝新增非法 FBD 字面量；保存带有原有错误的可编辑版本不表示错误已修复或原生编译通过。修改源码或接口后，使用当前版本重新取得上下文。

## 常见选型对照

- **后台原生编译 vs. 本地产物导出**：需要用 GX Works2 检查 `.gxw` 能否编译通过时调用 `validate_project(native=true)`；需要生成 SVG、JSON、CSV 等本地产物时调用 `compile_project`。
- **查看已打开的 GX Works2 窗口**：直接调用 `list_gxworks2_sessions` → `read_gxworks2_snapshot`，无需提前创建或选中 Agent 工程。
- **修改现有 `.gxw` 文件**：常规程序与声明修改优先使用 `import_gxw_project` → `read_gxw_source` → `edit_gxw_project` 离线完成；当用户需要观看 GX Works2 界面逐步演示或执行前台专属操作时使用 `execute_native_engineering(mode="live")`。
- **静态代码诊断 vs. 运行状态查看**：排查语法、操作数和结构问题用 `get_diagnostics` / `validate_project`；查看模拟器 RUN 状态和软元件实时值用 `get_simulator_status` / `read_simulator_devices`。
- **仿真测试方案 vs. 实时波形采样**：`prepare_simulator_scenario` 用于生成测试方案配置；采集已连接 Simulator2 的实时信号波形使用 `start_simulator_trace`。
- **获取已保存版本的导出文件**：直接调用 `get_project_artifacts` 读取已有产物路径。


## 分页与适用条件

正文为 `data.document` 与 `data.objects`；`$ref` 指向同一读取中已返回或后续页将返回的对象。`delivery.complete` 只说明该读取序列已交付完毕，不代表所有所问知识已成立。正文 `coverage`/`knowledge_coverage` 的目标条目中，`corpus` 为 `present`/`absent`，`knowledge` 保留 `complete`/`incomplete`/`unresolved`。`delivery=see_delivery_complete` 在读完全部页前按待交付处理，读完后为已交付；`not_available` 表示没有可交付内容。页头 `delivery.coverage` 只给数量汇总，避免重复整张目标表。源码、接口、完整事实不会切半返回；参数、类型、条件、依赖及冲突必须随事实一起判读。

`facts[].depends_on` 引用同一事实包内的局部事实身份，不能只取主结论而略过被引用前提。知识状态不授予模拟器或真实设备执行能力。`generation_instructions` 仅给操作规则；事实位于 `generation_input.knowledge`，接口/源码/语言规则分别使用结构化字段。外层引用不再重复整份目录、当前程序或 schema。

分页中的 `$ref` 指向同一读取的 `objects`。遇到 `{ "$concat": ... }` 时，解析其引用的数组并按顺序拼接；`{ "$merge": ... }` 则合并引用的对象。它们只拆分目录容器，完整事实、接口和源码仍为单个对象。知识结果的 `knowledge_status` 表示来源知识状态，不授予 Core 执行能力。
