# 工具职责索引

这是查找入口的索引，不复制完整参数 schema。实际名称、可用性、权限与字段以当前服务 `tools/list` 和 instructions 为准。安装版落后于源码时，按其支持的合同工作并说明差异；不要给旧工具发送新字段。

## 工程、设计和规格

| 工具 | 用途 |
| --- | --- |
| `list_projects`, `create_project`, `select_project` | 独立 local 会话的工程发现、创建和明确选择；create 指定 CPU/语言及 Direct/Design。 |
| `get_current_project`, `get_project_artifacts` | 当前工程/版本状态、已保存受管理产物。后者属于 local 扩展。 |
| `import_gxw_project` | local 将实际 GXW 导入为源码版本，多程序文件明确 program。 |
| `get_design_context`, `propose_design_plan`, `confirm_design_plan` | local Design 分析、修复草稿、确认方案和用户参数答案。 |
| `get_project_spec`, `update_project_spec`, `confirm_project_spec` | 读取规格、修改草稿、确认指定修订；后两者经过共享提案/保存流程。 |
| `get_operation_status` | 查看实际操作/提案完成状态，不能用于授予权限。 |

`create_project.plc_model` 使用 GX Works2 新建对话框的 CPU 系列名，如 `FX3U`、`FX3U/FX3UC`、`FX3G/FX3GC`、`L02/L02-P`、`Q03UDV`；不传 `FX3U-48M`、`FX3U-48MR/ES` 等完整订货型号。点数、输出类型与环境等级另存为工程参数。完备规格/转写用 `generation_mode="direct"`，开放式方案比较用 `design`。

## 知识、生成和源码

| 工具 | 用途 |
| --- | --- |
| `get_generation_context` | 当前新建/编辑合同、完整生成输入、RAG 与候选输出要求；支持显式 plan_review 时按返回合同处理。 |
| `search_plc_manual` | 型号/语言范围内查询指令、地址、诊断；`aspects` 收窄维度，debug 用于追溯。 |
| `create_program_candidate`, `patch_program` | Ladder 候选与局部修改；不直接编辑 canonical IR。 |
| `get_current_program_info`, `read_network` | 读取当前程序结构及网络，支持范围由实际 schema 决定。 |
| `get_fbd_catalog` | FBD 参数合同、块/端口/任务等定义；按文档分页读取。 |
| `read_fbd_project`, `read_fbd_libraries`, `read_fbd_definition` | FBD 源码、库和完整定义。 |
| `create_fbd_candidate` | FBD 图形与定义操作；新建 generate、ST FB、库导入等操作以 parameter_schema 为准。 |
| `read_st_project`, `create_st_candidate` | ST 正文/声明读取、新建或既有源码操作；当前 public 新建用 generate/st_text。 |
| `read_sfc_project`, `create_sfc_candidate` | SFC 结构读取、新建或既有源码操作；不沿用旧版一律要求基底的结论。 |
| `read_gxw_source`, `edit_gxw_project` | 读取保存版本的实际源码身份，联合修改 ST/FBD 调用者及声明等；编辑绑定 version_id。 |

## 检查、原生工程和发现

| 工具 | 用途 |
| --- | --- |
| `get_diagnostics`, `validate_current_program` | 当前程序的静态诊断/验证；不代表真实 PLC 行为。 |
| `compile_project` | Core 编译及产物构建，不能用来证明 GX Works2 原生编译。 |
| `validate_project` | 保存工程或受支持来源的检查；native 模式检查实际 GXW。 |
| `import_current_program_to_gxworks2` | 按已有执行策略导入当前程序；pending 不是导入成功。 |
| `execute_native_engineering` | 后台或 live 原生工程计划，执行范围服从支持的命令和用户授权。 |
| `get_native_engineering_session`, `pause_native_engineering_session` | live 持续会话状态、revision 和步骤间暂停。 |
| `inspect_gxw_project`, `inspect_gxworks2_csv_files` | 外部 GXW/CSV 只读检查并获得来源引用。 |
| `list_gxworks2_sessions`, `read_gxworks2_snapshot` | 窗口发现及绑定快照；不等同于持续编辑会话。 |
| `read_native_device_assignments` | 实际编译后的声明分配地址。 |
| `compare_native_compiled_code`, `locate_native_code_steps` | 已有原生编译产物的差异与步号定位。 |

## Simulator2

`list_simulator_targets` 发现目标；`connect_simulator` 连接观察通道；`get_simulator_status`、`read_simulator_devices` 查看状态/值；`prepare_simulator_scenario` 准备计划；`start_simulator_trace`、`get_simulator_trace`、`stop_simulator_trace` 管理采样。上述能力不能推导为已下载、RUN 或写入设备。

[tool-names.txt](tool-names.txt) 是本次维护依据源码注册项整理的名称集合，用于维护比对；它不是运行时工具可用性的承诺。宿主的 `gxworks_generate_ladder` 等包装器不属于此集合。
