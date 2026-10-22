# MCP 工具目录

核对范围：公开 `main@278edb6`。共有 42 个共享工具名，独立 local 工程会话增加 5 个名称；`get_operation_status` 是同名重用，不重复计数。这里是选择指南而非完整 JSON Schema；以连接时 `tools/list` 和服务 instructions 为准。

“共享”表示共享注册表存在，不保证每种部署均可执行所有功能。“local”表示本次读取的独立工程会话扩展；其他模式未暴露时不得假设存在。`source_ref` 是注册时追加的可选参数，只用于支持它的来源读取/静态路径。

## 工程选择与结果

| 工具 | 范围 | 什么时候用 | 参数要点 | 返回/操作边界 |
| --- | --- | --- | --- | --- |
| `list_projects` | local | 已有保存工程待选择 | 无参数 | 返回清单及本会话选择；不自动选择。 |
| `create_project` | local | 明确的新工程 | name, plc_model, target_mode；可选 native_project_type, use_labels | 创建并选择真实 CPU/语言工程；不要为了检查快照而创建无关工程。 |
| `select_project` | local | 选择已发现的工程/版本 | project_id；可选 version_id | 固定保存版本不激活版本。 |
| `import_gxw_project` | local | 将 GXW 纳入可编辑版本 | path/data_base64 二选一；可选 filename, program, project_id, version_id, request_id | CPU/语言从文件读取，多程序需选 program；不是只读 inspect。 |
| `get_current_project` | 共享 | 查询选择、CPU、活动/选择版本 | 无参数 | selection_status=none 是正常状态，不表示 IDE 未打开。 |
| `get_project_artifacts` | local | 取得已保存产物位置 | 无参数；已有选择和保存版本 | 只返回已有的受管理产物，不再导出或覆盖。 |
| `get_operation_status` | 共享 | 读提案或任务回执 | proposal_id / execution_job_id 恰一项；正确工程选择 | 不批准、不重新执行。 |

## 来源发现与静态分析

| 工具 | 范围 | 什么时候用 | 参数要点 | 返回/操作边界 |
| --- | --- | --- | --- | --- |
| `inspect_gxw_project` | 共享 | 独立检查 GXW 文件 | path/data_base64 二选一；可选 program | 返回 CPU/程序清单/source_ref，不保存版本。 |
| `list_gxworks2_sessions` | 共享 | 查看已打开 GX 工程候选 | 无参数 | 不自动采用某窗口；与 Agent 工程选择独立。 |
| `read_gxworks2_snapshot` | 共享 | 读取指定 GX 窗口的程序 | session_id；可选 program（默认 MAIN） | 产生临时导出与 source_ref，不保存原工程或版本。 |
| `inspect_gxworks2_csv_files` | 共享 | 已有程序和注释 CSV | program_path/program_data_base64；comment_path/comment_data_base64；plc_model；可选 program_name | 每份文件内容/路径二选一；不打开 IDE。 |
| `get_current_program_info` | 共享 | 读程序摘要、网络清单 | 可选 source_ref | 使用返回的实际网络/源码标识；截断清单不代表完整清单。 |
| `read_network` | 共享 | 读一个完整 Ladder 网络 | network_id；可选 source_ref | 保留原始指令及来源行信息，不仅看绘图。 |
| `get_diagnostics` | 共享 | 确定性静态诊断 | 可选 source_ref | 不读 RUN、实时值；FBD/ST/SFC 有各自检查范围。 |
| `validate_current_program` | 共享 | 本地结构/语法评审 | 可选 source_ref | 不能据此声称 GX 编译或物理运行已通过。 |
| `validate_project` | 共享 | 静态检查或后台原生检查 | native（默认 false）；program 仅 native=true；静态时可用 source_ref | native=true 必须保存 GXW 版本；program 是上下文，不保证仅检查单个 POU。 |
| `compile_project` | 共享 | 临时生成 Core 产物 | 无参数；当前程序 | 不是 native compiler；ST/SFC/有序源码可能返回 not_run/unverified。 |

## 生成与离线编辑

| 工具 | 范围 | 什么时候用 | 参数要点 | 返回/操作边界 |
| --- | --- | --- | --- | --- |
| `search_plc_manual` | 共享 | 补充当前型号技术事实 | query；可选 top_k（1–8）, debug；无选中工程可传 plc_model | 已有工程不能用另一个 plc_model 覆盖它；普通生成不默认 debug。 |
| `get_generation_context` | 共享 | 生成/常规编辑前取得合同 | user_requirement；可选 workflow=direct_generation/detailed, execution_mode=background/live | 不调用第二个模型；使用返回的生成指导、规格、CPU 和 ID。 |
| `create_program_candidate` | 共享 | 提交 Ladder JSON | ladder；可选 program_name, generation_context_id | 不是 canonical IR；实际保存与审批取决于模式。 |
| `patch_program` | 共享 | 限定范围的 Ladder 修改 | patch；可选 generation_context_id | 网络用当前网络 ID；GXW 指令用原始 record_index + 完整 expected；不原生编译。 |
| `read_gxw_source` | 共享 | 获取既有 POU 离线编辑模型 | 可选 program；当前保存版本 | 返回同工程源码清单，包含程序/FB/FUNCTION；不切换或保存版本。 |
| `edit_gxw_project` | 共享 | 多个既有源码/声明/注释联合编辑 | 明确 version_id、edits；完整参数读实时 schema | edits.source_name 来自读取结果；默认 draft，编译/运行另行处理。 |

## FBD、ST 与 SFC

| 工具 | 范围 | 什么时候用 | 参数要点 | 返回/操作边界 |
| --- | --- | --- | --- | --- |
| `get_fbd_catalog` | 共享 | 生成 FBD 前查能力、端口、类型 | 可选 program | parameter_schema 是 FBD 操作参数入口；目录不是所有型号验证结论。 |
| `read_fbd_project` | 共享 | 读 FBD 图形、标签、端口、SVG | 可选 program, data_base64, source_ref（以实时 schema 为准） | 离线读取，不运行 GX。 |
| `read_fbd_definition` | 共享 | 读项目自建结构与 FB 定义 | 可选 source_name, source_ref（以实时 schema 为准） | 省略 source_name 列出当前定义，不编造接口。 |
| `read_fbd_libraries` | 共享 | 读用户库及其中的定义 | 可选 data_base64, library_name, include_source | 只读，不等同 import_library。 |
| `create_fbd_candidate` | 共享 | FBD 图形、标签、结构/FB、库、编译保存操作 | operation 及操作相关参数见实时 schema 和 get_fbd_catalog.parameter_schema | generate/edit 支持 networks 具名连接；compile 不带源码修改；native_save 仅当前调用显式选择。 |
| `read_st_project` | 共享 | 读 ST Program/FB/FUNCTION | 可选 program, data_base64, source_ref（以实时 schema 为准） | 读取正文、返回类型、声明和任务；不从任意 ST 文本造 GXW。 |
| `create_st_candidate` | 共享 | 普通 ST 创建/编辑/导入 | operation 对应 import/from_candidate/create_st_program/edit_st_program/edit_st_fb/edit_st_function/edit_st_source 等实时合同 | candidate v2 使用 schema_version=2、language=st、program、declaration_edits.upserts、task_binding；新建需实际资源/任务。 |
| `read_sfc_project` | 共享 | 读已有 SFC 图形及子程序 | 可选 program, data_base64, source_ref；native（默认 false） | native=true 为原生格点读取，不是运行或写入。 |
| `create_sfc_candidate` | 共享 | 导入/编辑既有 SFC 内部对象 | operation=import/edit；child_edits, graph_edits, declaration_edits 等实时合同 | 内部 action/zoom/transition 可按支持范围编辑；不创建新的工程级 SFC POU/任务。 |

## 原生工程与编译分析

| 工具 | 范围 | 什么时候用 | 参数要点 | 返回/操作边界 |
| --- | --- | --- | --- | --- |
| `import_current_program_to_gxworks2` | 共享 | 向 IDE 导入当前所选 Ladder 版本 | 无参数；当前保存版本 | 只生成/执行授权范围内的导入流程；以回执确认完成。 |
| `execute_native_engineering` | 共享 | 显式后台工程批次/持续前台工程方案 | plan；按实时 schema；live 使用 mode/session_action/session_id/controls | 不接收任意 PID、路径或低层驱动参数；按现有审批执行。 |
| `get_native_engineering_session` | 共享 | 读持续工程会话与当前修订 | 可选 session_id；正确工程选择 | 省略 ID 仅查询该工程唯一活动会话，不接管发现的任意 GX 窗口。 |
| `pause_native_engineering_session` | 共享 | 请求当前步骤结束后暂停 | session_id | 不强制中断正在进行的原生修改。 |
| `read_native_device_assignments` | 共享 | 编译并查声明/FB 成员设备分配 | queries: source,name,可选 scope=local/global；可选 program, compare_saved_code | 最多128项；source 是实际局部 POU 或全局声明表名。 |
| `compare_native_compiled_code` | 共享 | 保存代码与本次编译三通道比较 | 可选 program；保存的 GXW | 相同不证明模拟器/PLC 正在执行该版本。 |
| `locate_native_code_steps` | 共享 | 原生资源步号映射源码/实例 | queries: resource,step；可选 program,compare_saved_code | 最多64项；步号需非负；不读取设备，非唯一映射保持未解析。 |

## Simulator2 观察与方案

| 工具 | 范围 | 什么时候用 | 参数要点 | 返回/操作边界 |
| --- | --- | --- | --- | --- |
| `list_simulator_targets` | 共享 | 发现已连接目标 | 无参数 | 不启动、下载、停止或连接程序。 |
| `connect_simulator` | 共享 | 明确连接现有 Simulator2 | 无参数 | 可启动专用网关；当前代码注明路由仅 FX3U；不是启动/下载 PLC 程序。 |
| `get_simulator_status` | 共享 | 读模拟器 RUN 监控 | target_ref | 不表示真实 PLC 状态；程序对应关系仍需另证。 |
| `read_simulator_devices` | 共享 | 读模拟器设备值 | target_ref,addresses（1–64项） | 不 force、不 reset、不访问物理 PLC。 |
| `prepare_simulator_scenario` | 共享 | 生成类型化仿真方案 | config.signals；可选 initial,steps 等 | 返回 suite/视图；不执行刺激写入。 |
| `start_simulator_trace` | 共享 | 有限时长信号采样 | target_ref,config.signals；可选 sample_ms,duration_ms | 采样50–5000ms，时长最多300000ms；steps 仅显示计划标记。 |
| `get_simulator_trace` | 共享 | 读取采样状态与视图 | view_ref | 取实际状态、数值、样本数、URL，不重建页面。 |
| `stop_simulator_trace` | 共享 | 停止指定采样视图 | view_ref | 保留回放，不停止模拟器或 PLC 程序。 |

## 两个不能靠名称判断的区别

`compile_project` 是 MCP 中的 Core 产物入口；`NativeWorkspaceSourceSave.compile_project()` 是 Python 内部方法，不是客户端可调用的同名原生 MCP 能力。只需原生检查时选 `validate_project(native=true)`。

`READ_ONLY_TOOL_NAMES` 是项目注册分类，不等于绝对没有本地副作用：快照可生成临时导出，连接工具可启动网关，trace 可启动采样，原生检查会运行隔离工作区。依据具体 handler 和用户任务决定，而不是仅看工具集合名称。

来源：`src/agent_runtime/tool_policy.py`、`tool_catalog.py`、`inspection_tools.py`、`program_tools.py`、`src/application/engineering_session.py`。
