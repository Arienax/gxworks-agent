# MCP 工具目录

本目录与共享工具注册表及工程会话对应。具体暴露的工具列表与完整 JSON Schema 以连接时的 `tools/list` 和服务 instructions 为准。

表中“共享”指注册在共享工具表中的工具；“local”指独立工程会话额外暴露的工程与产物管理工具。支持只读快照的工具会在注册时接受可选的 `source_ref` 参数。

## 工程选择与结果

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `list_projects` | local | 查看已有 Agent 工程列表 | 无参数 | 返回工程清单及当前会话选中的工程与版本。 |
| `create_project` | local | 新建 Agent 工程 | `name`, `plc_model`, `target_mode`；可选 `generation_mode`（`direct`/`design`）, `native_project_type`, `use_labels` | 按指定 CPU、语言与生成模式（完备规格/转写用 `direct`，开放式方案比较用 `design`）创建并选中工程。 |
| `get_design_context` | local | 读取开放式新需求的 Design 分析上下文 | `user_requirement`；可选 `instruction_details` | 返回当前 CPU 与语言的 Design 分析指令、提交 schema 与知识分页；代码转写或完整规格任务无需调用。 |
| `propose_design_plan` | local | 提交或局部修复 1～3 个 Design 方案草稿 | `analysis_id` + `analysis`，或 `analysis_id` + `repair_id` + `repairs`；空参可回读当前草稿 | 校验方案与约束锚点，返回待用户确认的 `draft_id` 或需修补路径。 |
| `confirm_design_plan` | local | 持久化用户选定的方案与参数答复 | `draft_id`, `approach_id`；可选 `parameter_answers` | 保存确认规格并返回后续 `get_generation_context(workflow="detailed")` 动作。 |
| `select_project` | local | 切换当前工程或查看指定版本 | `project_id`；可选 `version_id` | 选中目标工程，或固定查看某个已保存版本。 |
| `import_gxw_project` | local | 将 `.gxw` 导入为可编辑版本 | `path` 或 `data_base64`；可选 `filename`, `program`, `project_id`, `version_id`, `request_id` | 从文件读取 CPU 与源码目录并创建保存版本；多程序时可指定目标 `program`。 |
| `get_current_project` | 共享 | 查询当前选中的工程、CPU 与版本 | 无参数 | 返回当前会话的工程选择状态（未选择时 `selection_status="none"`）。 |
| `get_project_artifacts` | local | 获取当前已保存版本的产物路径 | 无参数（需已选中保存版本） | 返回该版本已生成的受管理产物清单。 |
| `get_operation_status` | 共享 | 查询提案或后台任务的执行回执 | `proposal_id` 或 `execution_job_id`（二选一） | 返回当前任务状态、已完成阶段与结果摘要。 |

## 来源发现与静态分析

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `inspect_gxw_project` | 共享 | 只读检查外部 `.gxw` 文件 | `path` 或 `data_base64`；可选 `program` | 返回文件的 CPU、程序清单、格式能力与只读 `source_ref`。 |
| `list_gxworks2_sessions` | 共享 | 检测 GX Works2 安装、可执行路径及可见窗口 | 无参数 | 后端从用户数据目录 `gxworks2-installation.json` 复用仍有效的安装路径，失效后重新查找。`gx_works2_installed` 和 `gx_works2_executable` 独立于实时窗口列表；每个窗口包含 `session_id` 和 `project_open`，后者未知时为 `null`。 |
| `read_gxworks2_snapshot` | 共享 | 读取指定 GX Works2 窗口中的程序 | `session_id`；可选 `program`（默认 `MAIN`） | 导出临时只读快照并返回 `source_ref`。 |
| `inspect_gxworks2_csv_files` | 共享 | 分析导出的程序 CSV 与注释 CSV | `program_path`/`program_data_base64`、`comment_path`/`comment_data_base64`、`plc_model`；可选 `program_name` | 解析两份 CSV 并返回只读 `source_ref`。 |
| `get_current_program_info` | 共享 | 读取程序摘要与网络列表 | 可选 `source_ref` | 返回当前程序或指定快照的源码标识、网络摘要与软元件统计。 |
| `read_network` | 共享 | 读取单个 Ladder 网络详情 | `network_id`；可选 `source_ref` | 返回该网络的完整指令序列、来源行号与分支结构。 |
| `get_diagnostics` | 共享 | 获取确定性静态诊断 | 可选 `source_ref` | 按当前语言（Ladder/ST/FBD/SFC）返回静态检查结果。 |
| `validate_current_program` | 共享 | 按当前语言运行本地检查 | 可选 `source_ref` | Ladder 检查网络或有序指令；ST 结合声明、类型及调用上下文检查正文；FBD/SFC 返回结构、接口与 issues。 |
| `validate_project` | 共享 | 执行本地静态检查或后台原生编译检查 | `native`（默认 `false`）；`native=true` 时可选 `program`；静态检查时可选 `source_ref` | `native=true` 时在隔离工作区对已保存的 `.gxw` 版本运行 GX Works2 编译与公开检查。 |
| `compile_project` | 共享 | 查询或生成 Core 本地产物 | 无参数 | Ladder IR 可生成 JSON、SVG、衍生 ST、CSV 等临时产物；其他源码形式返回当前静态检查或未编译状态，以 `compiled/status/validation` 为准。GXW 交付由候选保存流程完成。 |

## 生成与离线编辑

四语言候选确认保存共用 GXW 优先交付流程：发现可用 GX Works2 后自动编译、保存与冷重开；未安装时交付可生成的离线 GXW，并标注未执行原生编译。无法生成 GXW 时，Ladder 降级为程序及注释 CSV，ST 为代码 TXT 与全局标签 CSV，FBD/SFC 为结构化候选及已有预览。编译失败保留 GXW 和诊断，失败版本不激活。`delivery` 返回首选/实际格式、主产物和降级原因，`validation` 返回各验证阶段状态。

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `search_plc_manual` | 共享 | 检索指定 CPU 的指令、FBD 语言与软元件事实 | `query`；可选 `top_k`（1–8）, `debug`；未选中工程时传 `plc_model` | 精确地址事实区分编号、默认分配及参数条件；不证明当前工程分配。 |
| `get_generation_context` | 共享 | 生成或修改程序前获取工程上下文 | `user_requirement`；可选 `workflow`（`direct_generation`/`detailed`）, `instruction_details`, `execution_mode`；FBD 可选 `catalog_scope`（默认 `task`）和 `templates` | 无 GXW 的 ST/SFC 返回 `generate` 新建合同；FBD 返回语言/地址规则、完整接口、关联源码和 `supported_paths`；四语言未确认 Design 会话均可传 `workflow="direct_generation"` 配合 `user_requirement` 切换为直接生成。按 `output_contract` 提交；读取上下文本身不启动原生组件。 |
| `create_program_candidate` | 共享 | 提交新建或重写的 Ladder 候选程序 | `ladder`（直接传程序对象，勿再包 `{ladder:...}` 或 `{networks:...}`）；含 `instruction_research` 时传顶层 `implementation_review`；可选 `program_name`, `generation_context_id` | 经 Core 校验后按接入模式返回候选、提案或保存版本；`device_comments` 兼容数组与字典。保存时联合写入指令、声明和注释，优先通过 GX Works2 与 v4 组件交付 GXW（CPU 支持性见 `output_contract.gxw_delivery`）。 |
| `patch_program` | 共享 | 对已有 Ladder 执行局部补丁修改 | `patch`（图形 IR 版本用 `operations`；已保存有序 GXW 版本仅用 `instruction_edits`，按原始 `record_index` + `expected` 修改）；可选 `generation_context_id` | 离线修改网络或有序指令；保存阶段按共享流程自动编译、保存及冷重开。 |
| `read_gxw_source` | 共享 | 读取已导入 `.gxw` 中某个源码对象的编辑模型 | 可选 `program`（需已选中保存版本） | 返回该 POU 的离线编辑模型及同工程的源码目录（Program/FB/FUNCTION）。 |
| `edit_gxw_project` | 共享 | 联合编辑 `.gxw` 内的多个源码、声明或注释 | `version_id`；至少提供 `edits`、`declaration_edits`、`device_comments` 之一；可选 `save_policy`（默认 `"draft"`） | ST 接口/正文和全部受影响 FBD 调用者同次提交后校验。两种保存策略均拒绝新增非法 FBD 字面量；不接受 `generation_context_id`。 |

## FBD、ST 与 SFC

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `get_fbd_catalog` | 共享 | 查询当前 FBD 工程的能力、定义、端口与参数 schema | 可选 `program`, `catalog_scope`（默认 `full`）, `templates` | 精确筛选保留完整接口与依赖，未命中见 `missing_templates`；生成仍需 `get_generation_context`。 |
| `read_fbd_project` | 共享 | 离线读取 FBD 图形、标签、端口与 SVG | 可选 `program`, `data_base64`, `source_ref` | 返回 FBD 网络结构、节点端子连接及声明信息。 |
| `read_fbd_definition` | 共享 | 读取工程内的结构体或 FB 定义 | 可选 `source_name`, `source_ref` | 省略 `source_name` 时列出定义目录；指定时返回该定义的详细接口与实现。 |
| `read_fbd_libraries` | 共享 | 查看 `.gxw` 中的用户库及库内定义 | 可选 `data_base64`, `library_name`, `include_source` | 只读列出用户库目录及定义详情，供后续 `import_library` 参考。 |
| `create_fbd_candidate` | 共享 | 执行 FBD 图形编辑、定义维护、库导入或原生编译保存 | `operation` 及对应参数以工具 schema 为准；图形 `model` 见 `parameter_schema`；`import`/`edit`/`generate` 可传布尔 `native_save` | 无保存版本的 FBD 工程可用首个 `create_st_fb` 无基底创建并写入定义，后续基于该版本维护定义与主图。`native_save=true` 仅请求本次准备阶段原生保存；确认保存后仍默认编译、保存及冷重开。库导入用来源 GXW 的 `data_base64` 与 `library_name`，转移已覆盖的 ST 库实现，保留目标调用者；FBD 库正文转移不支持。`compile` 单独执行原生编译保存。 |
| `read_st_project` | 共享 | 读取 ST Program、FB 与 FUNCTION | 可选 `program`, `data_base64`, `source_ref` | 返回 ST 正文、返回类型、局部/全局声明与任务绑定信息。 |
| `create_st_candidate` | 共享 | 创建、导入或离线编辑 ST 源码与声明 | `operation`（如 `generate`、`import`、`from_candidate`、`create_st_program`、`edit_st_program`、`edit_st_fb`、`edit_st_function`、`edit_st_source`）；编辑时以准确逻辑身份指定 `source_name` | `edit_*` 省略 `declarations` 保留原声明，给出列表则整体替换；新工程按 `get_generation_context` 使用 `generate`；`create_st_program` 仅向已有工程添加 Program，需指定该工程实际存在的资源与任务。 |
| `read_sfc_project` | 共享 | 读取已有 SFC 工程结构与子程序 | 可选 `program`, `data_base64`, `source_ref`, `native`（默认 `false`） | 返回 SFC 关系图、步骤、转换、动作与子程序；`native=true` 时补充原生格点信息。 |
| `create_sfc_candidate` | 共享 | 新建 SFC 候选、导入或编辑已有 SFC 内部对象 | `operation`（`generate`/`import`/`edit`）、`child_edits`, `graph_edits`, `declaration_edits`；可选 `block` | 新工程使用 `generate` 提交完整图形、子程序及声明；块默认 `Block`/`0`/SFC，可指定名称、标题、块号。`zoom`/`transition` 写正文，`action` 维护编号和注册。v4 后台组件按实际 CPU 创建、编译、保存及冷重开；已有工程按读取合同编辑。 |

## 原生工程与编译分析

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `import_current_program_to_gxworks2` | 共享 | 将当前选中的 Ladder 版本导入 GX Works2 | 可选 `project_path`（当前打开的 `.gxw` 绝对路径；需已选中保存版本） | 标题截断或只有文件名时须提供路径并核对当前进程的文件占用；不会打开或切换工程。按服务端审批策略执行导入，返回操作回执。 |
| `execute_native_engineering` | 共享 | 四语言已保存 GXW 的后台编译保存与新版本交付；保留原有 FBD/ST 可见会话 | 后台传 `plan={schema_version:1,mode:"background",commands:[{operation:"compile"},{operation:"save"},{operation:"reopen"}]}` | 后台模式不打开可见编辑会话，编译保存与重开核对成功后交付新版本。显式 `mode="live"` 继续复用原路径与窗口；重复 `start` 不执行本次命令，`end` 结束会话。 |
| `get_native_engineering_session` | 共享 | 查询持续原生工程会话状态与修订号 | 可选 `session_id` | MCP 重启后加载持久状态；返回活动 `revision`、`saved_revision`、`version_revision` 与待处理步骤，继续前读取当前修订。 |
| `pause_native_engineering_session` | 共享 | 请求持续原生会话在当前步骤完成后暂停 | `session_id` | 在当前原生操作完成后安全暂停会话，便于审阅或后续恢复。 |
| `read_native_device_assignments` | 共享 | 原生编译并查询声明或 FB 成员的分配地址 | `queries`（含 `source`, `name`, 可选 `scope="local"`/`"global"`，最多 128 项）；可选 `program`, `compare_saved_code` | 返回编译器通过原生交叉引用分配的实际软元件地址。 |
| `compare_native_compiled_code` | 共享 | 比对已保存 `.gxw` 的代码通道与最新编译结果 | 可选 `program` | 对比已保存代码与新编译输出的三通道一致性。 |
| `locate_native_code_steps` | 共享 | 将原生资源步号定位到源码、网络或调用实例 | `queries`（含 `resource`, `step`，最多 64 项）；可选 `program`, `compare_saved_code` | 返回步号对应的 ST 行、声明项、FBD 网络/节点或调用实例（存在多解时标记为 `unresolved`）。 |

## Simulator2 观察与方案

`config.signals` 使用唯一 `id`，支持 BOOL、INT、UINT、WORD、DINT、UDINT、DWORD、REAL 和 RAW。BOOL 绑定位软元件，数值类型绑定 D 起始地址，32 位类型自动展开连续两字且低字在前；RAW 用于原始值观察。最多 256 个信号，展开去重后最多 256 个物理地址；`read_simulator_devices` 单次读取另限 64 个地址。实时网关当前支持 FX3U。

`config.initial` 与 `steps[].set/expect` 按信号 `id` 填值；写入计划使用 `role=input`，`initial` 覆盖所有待写信号；期望支持 `{value, tolerance}`。步骤时间按非递减顺序排列且在 `duration_ms` 内。方案准备与只读采样均不执行输入写入或断言，实际测试交给既有版本绑定执行服务。

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `list_simulator_targets` | 共享 | 查看当前已连接的 Simulator2 目标 | 无参数 | 返回可用目标列表及 `target_ref`。 |
| `connect_simulator` | 共享 | 连接本机运行中的 GX Simulator2 | 无参数 | 启动本地网关并建立连接（当前路由支持 FX3U），返回 `target_ref`。 |
| `get_simulator_status` | 共享 | 读取模拟器的 RUN/STOP 等运行状态 | `target_ref` | 返回目标模拟器的当前状态信息。 |
| `read_simulator_devices` | 共享 | 只读读取模拟器内的软元件当前值 | `target_ref`, `addresses`（1–64 项） | 返回指定软元件的当前采样值。 |
| `prepare_simulator_scenario` | 共享 | 构建类型化仿真测试方案 | `config.signals`；可选 `config.initial`, `config.steps` | 返回测试 DSL `suite`、`execution_started=false`、`view_ref` 和时间轴 URL。 |
| `start_simulator_trace` | 共享 | 启动有限时长的只读信号波形采样 | `target_ref`, `config.signals`；可选 `config.sample_ms`（默认100，50–5000）, `config.duration_ms`（默认10000，最长300000） | 按声明的存储类型解码采样值，返回 `view_ref`、最新数值和时间轴 URL。 |
| `get_simulator_trace` | 共享 | 查询波形采样进度、最新数值与查看器 URL | `view_ref` | 返回 `status`、`latest.values/errors`、`sample_count`、`dropped_samples`、`elapsed_ms`；工具摘要的 `samples` 为空，完整采样在 Web 时间轴查看。 |
| `stop_simulator_trace` | 共享 | 提前结束指定的波形采样任务 | `view_ref` | 停止采样并保留已采集的波形数据供回放。 |

## 易混工具说明

- **本地生成、原生检查与保存**：`compile_project` 运行 Core 本地管线；候选确认保存会优先交付 GXW 并自动原生编译；单独检查已保存 GXW 用 `validate_project(native=true)`；单独编译保存为新版本用 `execute_native_engineering` 的 `background` 模式。
- **只读发现与检查工具的本地动作**：`read_gxworks2_snapshot` 会导出临时快照文件，`connect_simulator` 会启动本地模拟器网关，`start_simulator_trace` 会启动周期采样，`validate_project(native=true)` 会在隔离目录运行原生编译检查；调用时按用户任务需求选用即可。

来源：`src/agent_runtime/tool_policy.py`、`tool_catalog.py`、`inspection_tools.py`、`program_tools.py`、`src/application/engineering_session.py`。


三个上下文读取工具均支持 `cursor`；按返回的 `delivery.next` 连续读取，不自行修改续读参数。`search_plc_manual` 另支持 `language` 和 `aspects`，`top_k` 仅限制补充相关资料。MCP 正文只有一份 JSON，不依赖 `structuredContent` 镜像。

分页中的 `$ref` 指向同一读取的 `objects`。遇到 `{ "$concat": ... }` 时，解析其引用的数组并按顺序拼接；`{ "$merge": ... }` 则合并引用的对象。它们只拆分目录容器，完整事实、接口和源码仍为单个对象。知识结果的 `knowledge_status` 表示来源知识状态，不授予 Core 执行能力。
