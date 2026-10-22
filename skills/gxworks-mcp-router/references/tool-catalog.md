# MCP 工具目录

对应公开分支 `main@278edb6`，涵盖 42 个共享工具与 5 个独立 `local` 会话扩展工具（`get_operation_status` 在两种模式下同名复用）。具体暴露的工具列表与完整 JSON Schema 以连接时的 `tools/list` 和服务 instructions 为准。

表中“共享”指注册在共享工具表中的工具；“local”指独立工程会话额外暴露的工程与产物管理工具。支持只读快照的工具会在注册时接受可选的 `source_ref` 参数。

## 工程选择与结果

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `list_projects` | local | 查看已有 Agent 工程列表 | 无参数 | 返回工程清单及当前会话选中的工程与版本。 |
| `create_project` | local | 新建 Agent 工程 | `name`, `plc_model`, `target_mode`；可选 `native_project_type`, `use_labels` | 按指定 CPU 与语言创建并选中工程。 |
| `select_project` | local | 切换当前工程或查看指定版本 | `project_id`；可选 `version_id` | 选中目标工程，或固定查看某个已保存版本。 |
| `import_gxw_project` | local | 将 `.gxw` 导入为可编辑版本 | `path` 或 `data_base64`；可选 `filename`, `program`, `project_id`, `version_id`, `request_id` | 从文件读取 CPU 与源码目录并创建保存版本；多程序时可指定目标 `program`。 |
| `get_current_project` | 共享 | 查询当前选中的工程、CPU 与版本 | 无参数 | 返回当前会话的工程选择状态（未选择时 `selection_status="none"`）。 |
| `get_project_artifacts` | local | 获取当前已保存版本的产物路径 | 无参数（需已选中保存版本） | 返回该版本已生成的受管理产物清单。 |
| `get_operation_status` | 共享 | 查询提案或后台任务的执行回执 | `proposal_id` 或 `execution_job_id`（二选一） | 返回当前任务状态、已完成阶段与结果摘要。 |

## 来源发现与静态分析

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `inspect_gxw_project` | 共享 | 只读检查外部 `.gxw` 文件 | `path` 或 `data_base64`；可选 `program` | 返回文件的 CPU、程序清单、格式能力与只读 `source_ref`。 |
| `list_gxworks2_sessions` | 共享 | 发现本机已打开的 GX Works2 窗口 | 无参数 | 返回当前运行的 GX Works2 窗口候选及各自的 `session_id`。 |
| `read_gxworks2_snapshot` | 共享 | 读取指定 GX Works2 窗口中的程序 | `session_id`；可选 `program`（默认 `MAIN`） | 导出临时只读快照并返回 `source_ref`。 |
| `inspect_gxworks2_csv_files` | 共享 | 分析导出的程序 CSV 与注释 CSV | `program_path`/`program_data_base64`、`comment_path`/`comment_data_base64`、`plc_model`；可选 `program_name` | 解析两份 CSV 并返回只读 `source_ref`。 |
| `get_current_program_info` | 共享 | 读取程序摘要与网络列表 | 可选 `source_ref` | 返回当前程序或指定快照的源码标识、网络摘要与软元件统计。 |
| `read_network` | 共享 | 读取单个 Ladder 网络详情 | `network_id`；可选 `source_ref` | 返回该网络的完整指令序列、来源行号与分支结构。 |
| `get_diagnostics` | 共享 | 获取确定性静态诊断 | 可选 `source_ref` | 按当前语言（Ladder/ST/FBD/SFC）返回静态检查结果。 |
| `validate_current_program` | 共享 | 校验当前程序的本地结构与语法 | 可选 `source_ref` | 执行本地结构、操作数及可维护性检查。 |
| `validate_project` | 共享 | 执行本地静态检查或后台原生编译检查 | `native`（默认 `false`）；`native=true` 时可选 `program`；静态检查时可选 `source_ref` | `native=true` 时在隔离工作区对已保存的 `.gxw` 版本运行 GX Works2 编译与公开检查。 |
| `compile_project` | 共享 | 生成 Core 本地编译产物 | 无参数 | 运行 Core 本地管线，生成 JSON、SVG、衍生 ST、CSV 等产物。 |

## 生成与离线编辑

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `search_plc_manual` | 共享 | 检索指定 CPU 的指令与软元件事实 | `query`；可选 `top_k`（1–8）, `debug`；未选中工程时传 `plc_model` | 返回按 CPU 过滤的指令事实、操作数约束与手册依据。 |
| `get_generation_context` | 共享 | 生成或修改程序前获取工程上下文 | `user_requirement`；可选 `workflow`（`direct_generation`/`detailed`）, `execution_mode`（`background`/`live`） | 返回当前 CPU 的生成合同、指令事实、已有规格与 `generation_context_id`。 |
| `create_program_candidate` | 共享 | 提交新建或重写的 Ladder 候选程序 | `ladder`；可选 `program_name`, `generation_context_id` | 经 Core 校验后按当前接入模式保存版本、生成提案或返回候选。 |
| `patch_program` | 共享 | 对已有 Ladder 执行局部补丁修改 | `patch`；可选 `generation_context_id` | 按 `network_id` 或 GXW 原始 `record_index` + `expected` 提交局部修改。 |
| `read_gxw_source` | 共享 | 读取已导入 `.gxw` 中某个源码对象的编辑模型 | 可选 `program`（需已选中保存版本） | 返回该 POU 的离线编辑模型及同工程的源码目录（Program/FB/FUNCTION）。 |
| `edit_gxw_project` | 共享 | 联合编辑 `.gxw` 内的多个源码、声明或注释 | `version_id`, `edits`；可选 `save_policy`（默认 `"draft"`） | 基于指定 `version_id` 在一次离线事务中提交多处修改并生成逐源码差异。 |

## FBD、ST 与 SFC

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `get_fbd_catalog` | 共享 | 查询当前 FBD 工程的能力、定义、端口与参数 schema | 可选 `program` | 返回可用块定义、端口方向及 `create_fbd_candidate` 所需的 `parameter_schema`。 |
| `read_fbd_project` | 共享 | 离线读取 FBD 图形、标签、端口与 SVG | 可选 `program`, `data_base64`, `source_ref` | 返回 FBD 网络结构、节点端子连接及声明信息。 |
| `read_fbd_definition` | 共享 | 读取工程内的结构体或 FB 定义 | 可选 `source_name`, `source_ref` | 省略 `source_name` 时列出定义目录；指定时返回该定义的详细接口与实现。 |
| `read_fbd_libraries` | 共享 | 查看 `.gxw` 中的用户库及库内定义 | 可选 `data_base64`, `library_name`, `include_source` | 只读列出用户库目录及定义详情，供后续 `import_library` 参考。 |
| `create_fbd_candidate` | 共享 | 执行 FBD 图形编辑、定义维护、库导入或原生编译保存 | `operation` 及对应参数（见 `get_fbd_catalog.parameter_schema`） | 支持 `generate`/`edit`、结构体与 FB 定义编写、`import_library`、`compile` 与 `native_save`。 |
| `read_st_project` | 共享 | 读取 ST Program、FB 与 FUNCTION | 可选 `program`, `data_base64`, `source_ref` | 返回 ST 正文、返回类型、局部/全局声明与任务绑定信息。 |
| `create_st_candidate` | 共享 | 创建、导入或离线编辑 ST 源码与声明 | `operation`（如 `import`、`from_candidate`、`create_st_program`、`edit_st_program`、`edit_st_fb`、`edit_st_function`、`edit_st_source`） | 校验 ST 语法与声明绑定；新建普通 ST Program 时需指定工程内实际存在的资源与任务。 |
| `read_sfc_project` | 共享 | 读取已有 SFC 工程结构与子程序 | 可选 `program`, `data_base64`, `source_ref`, `native`（默认 `false`） | 返回 SFC 关系图、步骤、转换、动作与子程序；`native=true` 时补充原生格点信息。 |
| `create_sfc_candidate` | 共享 | 导入或编辑已有 SFC 内部对象 | `operation`（`import`/`edit`）、`child_edits`, `graph_edits`, `declaration_edits` | 在既有 SFC 工程支持范围内修改内部步骤、分支、动作、转换及声明。 |

## 原生工程与编译分析

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `import_current_program_to_gxworks2` | 共享 | 将当前选中的 Ladder 版本导入 GX Works2 | 无参数（需已选中保存版本） | 按服务端审批策略执行导入，返回操作回执。 |
| `execute_native_engineering` | 共享 | 执行后台原生工程批次或驱动持续前台会话 | `plan`（含 `mode`、`session_action`、`session_id`、`controls`、`steps` 等） | 支持在隔离后台或同一个持续打开的 GX Works2 窗口中推进多步工程修改、编译与保存。 |
| `get_native_engineering_session` | 共享 | 查询持续原生工程会话状态与修订号 | 可选 `session_id` | 返回当前会话的活动 `revision`、`saved_revision`、`version_revision` 与待处理步骤。 |
| `pause_native_engineering_session` | 共享 | 请求持续原生会话在当前步骤完成后暂停 | `session_id` | 在当前原生操作完成后安全暂停会话，便于审阅或后续恢复。 |
| `read_native_device_assignments` | 共享 | 原生编译并查询声明或 FB 成员的分配地址 | `queries`（含 `source`, `name`, 可选 `scope="local"`/`"global"`，最多 128 项）；可选 `program`, `compare_saved_code` | 返回编译器通过原生交叉引用分配的实际软元件地址。 |
| `compare_native_compiled_code` | 共享 | 比对已保存 `.gxw` 的代码通道与最新编译结果 | 可选 `program` | 对比已保存代码与新编译输出的三通道一致性。 |
| `locate_native_code_steps` | 共享 | 将原生资源步号定位到源码、网络或调用实例 | `queries`（含 `resource`, `step`，最多 64 项）；可选 `program`, `compare_saved_code` | 返回步号对应的 ST 行、声明项、FBD 网络/节点或调用实例（存在多解时标记为 `unresolved`）。 |

## Simulator2 观察与方案

| 工具 | 范围 | 适用场景 | 参数要点 | 主要产出与说明 |
| --- | --- | --- | --- | --- |
| `list_simulator_targets` | 共享 | 查看当前已连接的 Simulator2 目标 | 无参数 | 返回可用目标列表及 `target_ref`。 |
| `connect_simulator` | 共享 | 连接本机运行中的 GX Simulator2 | 无参数 | 启动本地网关并建立连接（当前路由支持 FX3U），返回 `target_ref`。 |
| `get_simulator_status` | 共享 | 读取模拟器的 RUN/STOP 等运行状态 | `target_ref` | 返回目标模拟器的当前状态信息。 |
| `read_simulator_devices` | 共享 | 只读读取模拟器内的软元件当前值 | `target_ref`, `addresses`（1–64 项） | 返回指定软元件的当前采样值。 |
| `prepare_simulator_scenario` | 共享 | 构建类型化仿真测试方案 | `config.signals`；可选 `initial`, `steps` | 生成仿真测试方案与时间轴配置视图。 |
| `start_simulator_trace` | 共享 | 启动有限时长的只读信号波形采样 | `target_ref`, `config.signals`；可选 `sample_ms`（50–5000）, `duration_ms`（最长 300000） | 周期采样目标信号并返回 `view_ref` 与时间轴查看器地址。 |
| `get_simulator_trace` | 共享 | 查询波形采样进度、样本数据与查看器 URL | `view_ref` | 返回当前采样状态、已采集样本数及时间轴视图信息。 |
| `stop_simulator_trace` | 共享 | 提前结束指定的波形采样任务 | `view_ref` | 停止采样并保留已采集的波形数据供回放。 |

## 易混工具说明

- **`compile_project` 与 `validate_project(native=true)`**：`compile_project` 运行 Python Core 本地管线以生成图形和文本产物；需要调用 GX Works2 原生编译器检查 `.gxw` 时使用 `validate_project(native=true)`。
- **只读发现与检查工具的本地动作**：`read_gxworks2_snapshot` 会导出临时快照文件，`connect_simulator` 会启动本地模拟器网关，`start_simulator_trace` 会启动周期采样，`validate_project(native=true)` 会在隔离目录运行原生编译检查；调用时按用户任务需求选用即可。

来源：`src/agent_runtime/tool_policy.py`、`tool_catalog.py`、`inspection_tools.py`、`program_tools.py`、`src/application/engineering_session.py`。
