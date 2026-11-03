# 接入模式、回执判读与错误恢复

默认交付优先返回 GXW。找到 GX Works2 后，共享保存流程自动编译、保存并冷重开；读取 delivery 与 validation 的实际结果，不根据文件存在宣称编译通过。GXW 无法生成时，Ladder 降级 CSV、ST 降级为代码 TXT 与全局标签 CSV、FBD/SFC 保留结构化候选及预览，并说明原因。原生编译失败仍保留 GXW 诊断候选，不改交 CSV。新建 ST/SFC 使用 get_generation_context 返回的 operation=generate 合同。GX Works2 1.635.0.1 配合 v4 工作区适配器，按实际 CPU 查询原生系列与子系列，无基底创建对应语言工程。遵守该 CPU 的工程类型和语言条件，读取实际创建及验证结果；不使用 IDE 新建对话框、隐藏窗口控件或预制完整工程。

## 三种接入模式的提交结果

`status="saved_native_diagnostic"` 表示 GXW 候选及诊断已保存，原生验证失败，该版本未激活。使用返回的实际 `version_id` 和产物路径；需要继续读取时，先显式选择该诊断版本。不能将它表述为编译通过，也不要因为编译失败改交 CSV。

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
| 写入超时或结果未知 | 原生操作或长耗时任务尚未完成或连接中断 | 先调用 `get_operation_status` 或 `get_native_engineering_session` 查询实际完成阶段；Live 会话先 `rebind` 只读观察，不重放不确定动作。恢复已有队列用空命令列表及 `controls.resume_pending=true`。 |
| Live 原目标丢失、身份不符或持久记录不完整 | 原进程、窗口、工作路径或 CPU 已变化，或旧记录缺少恢复字段 | 按返回错误协调目标；不重复 `start` 新建副本或自动重放。MCP 重启后先查持久状态，再用当前 `expected_revision` 继续。 |
| `pending` / `confirmation_required` / `execution_consent_required` | 当前操作等待工作台批准或操作员配置原生执行许可 | 向用户说明当前待确认状态、提案编号或所需的执行许可配置。 |
| `source_ref` / 快照 `session_id` / `target_ref` 失效 | 临时快照过期、GX 窗口重启或模拟器重新连接 | 重新调用对应的发现、快照或连接工具获取最新标识；持续 Live 会话按上面的原目标恢复规则处理。 |

## 手册检索结果的判读

按 `delivery.next` 读完并合并对象后，使用 `document.results` 中的操作数、用途、条件和示例回答问题。`coverage` 帮助定位目标、已回答方面和待补资料；调用能力字段用于选择当前语言与工程的实现入口。

| 字段 | 如何使用 |
| --- | --- |
| `coverage.targets[].identity_found` | 定位指令、软元件或错误码身份。 |
| `coverage.targets[].status` | `complete`：所问方面已有核实资料；`incomplete`：按 `reason` 和 `aspects` 定位待补方面，继续使用已返回的内容。 |
| `aspects[].status` | `answered`：已核实内容；`unverified`：已有声明、待核验；`missing`：待补资料；`unresolved`：待解析或存在差异。按具体方面说明结果。 |
| `call_available` / `call_admission` | 当前语言与工程的调用支持；`st_not_admitted` 表示当前 ST 入口未准入，`unverified` 表示调用能力待核验。手册中的操作数与用途按返回内容解释。 |
| `documented_st_call_available` / `core_call_available` | 分别读取手册 ST 调用知识和 Core 调用支持；旧字段 `st_call_available` 对应 Core 准入。 |
| `results[].knowledge.facts[].knowledge_status` | `source_verified`：核实内容；`candidate_evidence`：候选依据；`declared_unverified`：已有声明；`unknown`：待补内容；`conflict`：差异项；`not_applicable`：适用性说明。 |
| `count` / `top_k` | `count` 为返回条数；`top_k` 限制补充资料，明确目标和依赖完整返回，条数可超过 `top_k`。 |

具体错误码按该码的记录读取原因、触发条件和恢复方法；症状检索按候选码组织结果。`corpus="absent"` 且 `reason="exact_fact_not_found"` 表示本次查库无精确匹配，`delivery="pending"` 表示后续页待取；各方面的 `unknown` 表示待补资料。

`INVALID_ARGUMENTS`：补齐有效查询；`UNKNOWN_PLC_MODEL`：核对 CPU 身份与 `details.known_model_count`；`KNOWLEDGE_UNAVAILABLE`：按 `details.knowledge_status` 补齐该型号运行资源。需要补充知识时，沿实际型号查资料并更新对应事实。

`device_address_rule` 提供当前 CPU 的编号范围、进制、空洞和字内位条件；默认分配、可配置范围、共享容量及硬件/版本条件按各自字段使用。当前工程分配通过工程上下文读取。

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
