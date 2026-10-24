---
name: gxworks-mcp-router
description: >-
  为 GXWorks Agent 的 Mitsubishi PLC 工程任务选择 MCP 工具与调用顺序。
  适用于新建或修改 Ladder/ST/FBD/SFC、读取 GXW/CSV/已打开的 GX Works2 工程、
  后台原生编译检查、查询声明分配地址、观察 Simulator2 或驱动持续可见编辑。
metadata:
  compatibility: >-
    需要客户端已连接 GXWorks Agent MCP。原生编译、GX Works2 界面交互与 Simulator2
    采样依赖 MCP 服务端所在的 Windows 环境及本地安装的 Mitsubishi 软件。
  source-repository: Arienax/gxworks-agent
  source-ref: local-working-tree
  source-base-commit: e5f6ca03455c97e0feb7328a21493dcdbf7fa620
  reviewed-on: '2026-10-24'
  version: '1.2.0'
---

# GXWorks MCP 任务路由

根据用户提供的输入形态与目标操作，直接选用对应的 GXWorks Agent MCP 工具链完成工程读取、程序生成、离线修改、原生编译检查或仿真观察。

## 首先识别输入与操作

结合客户端当前暴露的 MCP 工具清单、参数 schema 与服务 instructions（宿主可能为工具名添加命名空间前缀），从上下文中确定三件事：

1. **输入来源**：已保存的 Agent 版本、外部 `.gxw` 文件、导出的 CSV 文件、本机已打开的 GX Works2 窗口，还是新的控制需求。
2. **目标操作**：只读检查、离线编辑保存、原生编译检查、导入 GX Works2，还是模拟器状态/波形观察。
3. **接入模式**：独立 `local` 模式、连接 Web 的 `service` 模式，或绑定单工程的 `context` 模式。

用户已给出的 CPU 型号、I/O 分配、文件路径与程序名直接复用；仅在读取工程后仍缺少关键现场工艺条件或存在真实多选歧义时向用户确认。检查外部文件或已打开的 GX Works2 窗口时可直接调用发现与快照工具，编辑并保存版本时再选择或创建 Agent 工程。

## 按任务选择入口

| 用户意图 / 已有输入 | 入口工具 | 调用要点 |
| --- | --- | --- |
| 解释、检查上传或本机 GXW | `inspect_gxw_project` | 传入 `path` 或 `data_base64`，用返回的 `source_ref` 做后续只读分析；多程序工程按返回清单指定程序名。 |
| 修改一个尚未导入的 GXW | `import_gxw_project`（`local` 模式） | 导入为受管理版本后，依次调用 `read_gxw_source` 与 `edit_gxw_project`；其他模式使用当前暴露的导入入口。 |
| 查看当前打开的 GX Works2 程序 | `list_gxworks2_sessions` | 选中匹配的窗口后调用 `read_gxworks2_snapshot`，凭返回的 `source_ref` 读取网络或执行静态分析。 |
| 仅有原生导出的 CSV | `inspect_gxworks2_csv_files` | 传入程序 CSV、注释 CSV 和明确的 `plc_model`，直接获取只读 `source_ref`。 |
| 新建 Ladder 程序 | 选择或创建工程 → `get_generation_context` | 新需求默认使用 `workflow=direct_generation`，按返回合同构造 Ladder JSON 后提交 `create_program_candidate`。 |
| 修改已有 Ladder 的局部网络或指令 | `get_current_program_info` / `read_network` 或 `read_gxw_source` | 按需读取 `get_generation_context`；单程序局部修改用 `patch_program`，多源码或含声明/注释的联合修改用 `edit_gxw_project`。 |
| FBD 生成或修改 | `get_generation_context(user_requirement=...)` | 使用必需语言规则、当前 CPU 地址规则和源码依赖；按 `output_contract` 选择单图候选或 ST/FBD 联合事务。目录不足时精确补取 `get_fbd_catalog(templates=...)`，详见[流程 6](references/workflows.md#fbd-与项目定义)。 |
| FBD 定义、标签、库的查看与维护 | `get_fbd_catalog`，配合 `read_fbd_definition` / `read_fbd_libraries` | 按实际定义和能力执行维护操作；目录命中不能代替生成上下文，也不证明库正文可以导入。 |
| ST Program / FB / FUNCTION | `read_st_project`（新建时配合 `get_generation_context`） | 用 `create_st_candidate` 或 `edit_gxw_project` 提交；新建普通 ST Program 时绑定工程中实际存在的资源与任务。 |
| 已有 SFC 工程内部编辑 | `read_sfc_project` | 通过 `create_sfc_candidate(operation="edit")` 或 `edit_gxw_project` 编辑步骤、转换、动作、分支与子程序。 |
| 本地结构、语法与指令检查 | `validate_project` / `validate_current_program` / `get_diagnostics` | 执行 Core 确定性静态校验，获取结构、操作数、语法及可维护性诊断。 |
| 无界面原生编译检查 | **`validate_project(native=true)`** | 在后台隔离工作区调用 GX Works2 编译并检查当前已保存的 `.gxw` 版本。 |
| 生成本地 JSON/SVG/ST/CSV 产物 | **`compile_project`** | 运行 Core 本地编译管线，导出当前程序的图形与文本产物。 |
| 查询原生分配地址、代码差异或步号定位 | `read_native_device_assignments` / `compare_native_compiled_code` / `locate_native_code_steps` | 基于当前已保存的 `.gxw` 版本运行，传入工程内实际的声明表、POU、资源名与步号。 |
| 将当前 Ladder 导入 GX Works2 | `import_current_program_to_gxworks2` | 按服务端配置的执行许可，将当前版本的 `MAIN` 与软元件注释导入 GX Works2。 |
| 在同一 GX Works2 窗口中连续可见编辑 | `execute_native_engineering`（`mode="live"`） | 保存返回的工程会话 `session_id`；后续步骤先读取当前修订（`expected_revision`），按任务阶段显式编译或保存。 |
| 读取 Simulator2 状态与软元件 | `list_simulator_targets` → `get_simulator_status` / `read_simulator_devices` | 使用返回的 `target_ref` 读取状态和软元件值；需要建立连接时先调用 `connect_simulator`。 |
| 信号波形采样或生成仿真方案 | `start_simulator_trace` / `prepare_simulator_scenario` | `start_simulator_trace` 对目标做只读周期采样并生成时间轴视图；`prepare_simulator_scenario` 生成类型化测试方案。 |
| 查询异步任务或提案状态 | `get_operation_status` | 传入 `proposal_id` 或 `execution_job_id` 查询当前进度与执行回执。 |
| 检索 CPU 指令或软元件事实 | `search_plc_manual` | 生成上下文之外仍需核对指令细节时调用；未选中工程时显式传入 `plc_model`。按 `coverage` 判读命中：`status`、`aspects`、`call_admission` 与 `facts[].knowledge_status` 的含义见[手册检索结果的判读](references/result-handling.md#手册检索结果的判读)。 |

## 完整读取知识与上下文

`search_plc_manual`、`get_generation_context`、`get_fbd_catalog` 返回单份 JSON 正文，`data.context_schema_version=3`。先保存 `document` 和本页 `objects`；只要 `delivery.complete=false`，就原样调用 `delivery.next.tool` 与 `delivery.next.arguments`。合并同一 `document_id` 的全部 `objects` 后解析 `$ref`，再使用下文的 `generation_input`、`output_contract` 或 `results`。没有读完的页不能当作知识缺失。对象身份只在该次读取中使用。

每次返回最多 6,000 个估算 token 且不超过 32 KiB UTF-8。`STALE_READ_CURSOR` 表示查询、CPU、工程版本、事实库或会话发生变化，去掉游标重读；`READ_OBJECT_TOO_LARGE` 表示完整必需对象过大，缩小明确的读取范围，不能截断源码。独立 MCP 不采用 Web 模型配置推断客户端窗口。

手册查询可显式传 `language=ladder|st|fbd` 和 `aspects`（如 `execution`、`effects`、`st_call`）。`top_k` 只限制补充资料，明确指令/地址/错误码及依赖全部解析。按正文的 `coverage`/`knowledge_coverage` 与 `delivery.complete` 分清库中存在、所问知识完整与已交付；手册行为说明、调用准入、Core 执行语义和原生结果分别判断。知识缺失先检查本地来源、提炼和交付，不自动改用网页搜索。

## 各类标识的用途

- `project_id` / `version_id`：Agent 工程与已保存版本标识。`select_project(version_id=...)` 用于切换当前查看或操作的保存版本。
- `source_ref`：由 `inspect_gxw_project`、`read_gxworks2_snapshot` 或 `inspect_gxworks2_csv_files` 返回的只读快照引用，供支持该参数的读取与静态分析工具使用。
- 窗口发现 `session_id`：由 `list_gxworks2_sessions` 返回，标识本机已打开的 GX Works2 窗口，用于 `read_gxworks2_snapshot`。
- 持续工程会话 `session_id`：由 `execute_native_engineering(mode="live")` 返回，用于后续步骤的继续、暂停与修订查询（`get_native_engineering_session`）。
- `target_ref` / `view_ref`：分别标识已连接的 Simulator2 目标与波形采样视图。
- `generation_context_id`：由 `get_generation_context` 返回，仅向 schema 接受该字段的候选工具原样传入；`edit_gxw_project` 使用明确的 `version_id`，不接受该字段。`program`、`source_name`、`network_id` 与声明表名同样取自读取结果。

## 常见工作流要点

**新需求生成。** 复用已给出的 CPU 与 I/O 信息，调用 `get_generation_context` 获取输出合同后直接生成候选并提交。仅在缺少关键现场工艺事实时向用户提问；工程内可读取的事实先通过工具读取。

**已有文件读写。** 只读分析直接走 `inspect_*` 或窗口快照获取 `source_ref`；需要修改并保存新版本时，先将 `.gxw` 导入为工程版本，再通过离线编辑工具在可编辑对象范围内提交修改。

**FBD 规则与联合修改。** 从 `generation_input` 读取 `language_rules`、`device_address_rules`、`source_context` 和 `supported_paths`。FBD 常量使用其自身语言规则；地址编号范围、默认分配、参数限制与本工程实际分配分别处理。已保存工程需要同时修改 ST 正文/接口和 FBD 调用者时，以 `edit_gxw_project` 一次提交全部关联修改。必需规则缺失会阻止生成，不能套用其他 CPU 或 ST 语法补空白。

**FBD 按次保存。** `create_fbd_candidate` 的 `import`、`edit`、`generate` 仅在本次显式传入布尔 `native_save=true` 时请求原生保存；省略或传 `false` 使用离线路径，不继承历史版本的保存选择。源码和声明没有变化时不重复原生保存。该参数不用于 `create_fb`、`create_program` 或 `import_library`，这些操作保留自身的原生创建或导入流程。

**用户库导入。** 对已保存的目标版本使用 `create_fbd_candidate(operation="import_library")`，传入来源 GXW 的 `data_base64` 和选定的 `library_name`。当前转移已覆盖的 ST 实现、接口和库内类型；FBD 库接口可读不表示可转移其正文。保留目标项目的源码集合、任务绑定、调用者正文与声明，不复制来源工程的调用程序。

**ST 后台编辑。** 先读出对象的准确逻辑身份，以 `source_name` 调用 `create_st_candidate` 的 `edit_st_program`、`edit_st_fb`、`edit_st_function` 或 `edit_st_source`。省略 `declarations` 保留原声明；给出列表则按整份列表替换，需包含所有应保留的声明，不能只提交新增或修改行。这些后台操作不扩大前台 `execute_native_engineering` 的操作集合。

**本地产物与原生编译。** `compile_project` 用于生成 Core 本地产物（JSON、SVG、衍生 ST、CSV）；需要调用 GX Works2 编译器做后台无界面检查时使用 `validate_project(native=true)`。若需编译并更新保存的 `.gxw`，使用对应语言支持的保存操作（如 FBD 的 `operation="compile"` 或原生工程方案）。

**持续前台会话。** 当用户要求在 GX Works2 界面中展示编辑过程，或任务需要跨步执行原生工程操作时，使用 `execute_native_engineering` 的 `live` 模式。继续下一步前先通过 `get_native_engineering_session` 读取 `controls.expected_revision`，在关键阶段按任务要求显式执行编译、保存或冷重开验证。

## 回执与错误处理

按工具返回的实际阶段汇报结果（候选生成、版本保存、本地静态校验、原生编译检查、地址/步号映射、模拟器采样）。

不同接入模式的落地方式不同：`local` 模式可直接保存工程版本；`service` 模式通常返回待审批提案（`pending`）；`context` 模式返回待确认候选（`confirmation_required`）。以当前模式说明和返回字段（如 `version_id`、`proposal_id`、`save_result`）为准。

调用失败或超时后，先检查 `error.code`、结构化 `details`、`next_actions` 以及已完成阶段；写入或长任务状态不明时先用 `get_operation_status` 或 `get_native_engineering_session` 核对进展，确认清楚后再决定重试或调整参数。

## 按需读取参考

- 选择具体工具、确认参数要点：[工具目录](references/tool-catalog.md)。
- 多步调用流程与参数示例：[调用流程](references/workflows.md)。
- 接入模式、回执判读与错误恢复：[结果与恢复](references/result-handling.md)。
- 源码模块与维护说明：[来源说明](references/sources.md)。

分页中的 `$ref` 指向同一读取的 `objects`。遇到 `{ "$concat": ... }` 时，解析其引用的数组并按顺序拼接；`{ "$merge": ... }` 则合并引用的对象。它们只拆分目录容器，完整事实、接口和源码仍为单个对象。知识结果的 `knowledge_status` 表示来源知识状态，不授予 Core 执行能力。
