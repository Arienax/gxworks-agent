---
name: gxworks-mcp-router
description: >-
  为 GXWorks Agent 的 Mitsubishi PLC 工程任务选择正确 MCP 工具和调用顺序。
  用户要求新建或修改 Ladder/ST/FBD/SFC、读取 GXW/CSV/已打开的 GX Works2 工程、
  原生编译检查、查询声明地址、查看 Simulator2 或持续可见编辑时使用。
  特别处理“编译”歧义、只读快照与已保存版本、离线与前台操作，以及不同 MCP 接入模式。
compatibility: >-
  需要宿主已连接 GXWorks Agent MCP；本 Skill 不安装或启动 MCP。
  原生编译、GX Works2 界面和 Simulator2 功能依赖 MCP 服务器所在的 Windows 环境及其实际支持范围。
metadata:
  source-repository: Arienax/gxworks-agent
  source-ref: main
  source-commit: 278edb699dc40e9658970bb66095edeed8bd47b1
  reviewed-on: '2026-10-22'
  version: '1.0.0'
---

# GXWorks MCP 任务路由

目标：使用现成工程工具完成任务，不让工具名称、未选中工程或一次环境故障把 Agent 引向错误流程。

## 首先识别输入与操作

读取宿主当前暴露的 MCP 工具清单、参数 schema 和服务 instructions。以下名称是服务器的原始工具名；宿主可能添加命名空间前缀。仅调用实际存在的工具，不把参考文档当成工具注册。

用已知上下文确定三件事：用户提供的是保存的 Agent 版本、GXW 文件、CSV、已打开的 GX 会话，还是新需求；用户要只读、编辑、编译、导入还是运行观察；服务是独立 local、Web service 还是固定 context 模式。已给出的 CPU、I/O、路径、程序名及选择不要重复询问。只在存在无法由读取消除的真实歧义时询问。

不必每次都先调 `get_current_project`。任务本身不依赖 Agent 工程时，直接走发现/只读入口；需编辑保存版本时再读取或建立正确的工程选择。

## 按任务选择入口

| 用户意图 / 已有输入 | 先调用 | 后续与边界 |
| --- | --- | --- |
| 解释、检查上传或本机 GXW | `inspect_gxw_project` | `path` 或 `data_base64` 二选一；用返回的 `source_ref` 做只读分析。多程序按实际清单选择。 |
| 修改一个尚未导入的 GXW | local 模式的 `import_gxw_project` | 导入成保存版本后 `read_gxw_source` → `edit_gxw_project`。若工具未暴露，使用当前模式提供的导入流程，不冒充 local。 |
| 看当前 GX Works2 中的程序 | `list_gxworks2_sessions` | 选匹配的发现会话 → `read_gxworks2_snapshot` → 携 `source_ref` 分析；不随意选择另一个 Agent 工程。 |
| 仅有原生导出 CSV | `inspect_gxworks2_csv_files` | 提供程序 CSV、注释 CSV 和明确 `plc_model`；拿 `source_ref`，不启动 IDE。 |
| 新建 Ladder 程序 | 正确选择/创建工程 → `get_generation_context` | 默认新需求用 `workflow=direct_generation`；按返回合同生成 ladder JSON → `create_program_candidate`。 |
| 修改已有 Ladder 的局部网络/指令 | `get_current_program_info` / `read_network`；已有 GXW 也可 `read_gxw_source` | 需要生成上下文时读取 `get_generation_context`；随后 `patch_program`，或多源码事务用 `edit_gxw_project`。 |
| FBD 生成、FB、结构体、标签或库 | `get_fbd_catalog`，按需 `read_fbd_project` / `read_fbd_definition` / `read_fbd_libraries` | 从当前目录取得定义、端口和 schema，再 `create_fbd_candidate`；不凭印象编端口、坐标或型号能力。 |
| ST Program / ST FB / FUNCTION | `read_st_project`，新建时读取 `get_generation_context` | `create_st_candidate`，或已有多源码工程用 `edit_gxw_project`；新 Program 的资源、任务必须来自实际工程。 |
| 已有 SFC 内部编辑 | `read_sfc_project` | `create_sfc_candidate(operation=edit)` 或 `edit_gxw_project`；不据此宣称能新建工程级 SFC POU/任务。 |
| 本地结构、语法、指令检查 | `validate_project` / `validate_current_program` / `get_diagnostics` | 默认静态；不能回答设备是否在 RUN，也不是原生编译通过。 |
| 不打开 IDE 的原生编译检查 | **`validate_project(native=true)`** | 需要当前版本已保存的 GXW；普通源码/CSV/source_ref 不替代保存版本。不会写入新项目版本。 |
| 临时生成 JSON/SVG/ST/CSV 产物 | **`compile_project`** | 这是 Core 本地编译/产物入口，不是 GX Works2 原生编译器；实际产物与执行状态看返回值。 |
| 原生分配地址、保存代码差异或步号定位 | `read_native_device_assignments` / `compare_native_compiled_code` / `locate_native_code_steps` | 绑定当前 GXW；使用实际声明表、POU、资源名与步号，不用运行观察代替来源对应。 |
| 把当前 Ladder 传入 GX Works2 | `import_current_program_to_gxworks2` | 遵守服务的审批与原生执行权限；生成请求不等于已导入。 |
| 展示声明/ST/FBD 在同一 IDE 中连续变化 | `execute_native_engineering` 的 live 方案 | 使用服务返回的工程会话 ID；继续前读取当前修订，按用户任务显式保存。 |
| 读 Simulator2 的 RUN/软元件 | `list_simulator_targets` → `get_simulator_status` / `read_simulator_devices` | 只用返回的 `target_ref`；需要连接时且任务授权连接才调 `connect_simulator`。 |
| 信号时间轴或仿真方案 | `start_simulator_trace` / `prepare_simulator_scenario` | 时间轴是只读采样；方案与 `steps` 标记不执行输入写入或断言。 |
| 查已提交任务的状态 | `get_operation_status` | `proposal_id` 与 `execution_job_id` 二选一；先读回执，不为查询结果而重交写入。 |
| 查 CPU 指令/软元件事实 | `search_plc_manual` | 生成中先用已有上下文，只查剩余不确定事实；无工程时显式提供 `plc_model`。 |

## 标识不能混用

- `project_id` / `version_id`：Agent 工程及保存版本。`select_project(version_id=...)` 固定选择，不等于激活版本。
- `source_ref`：只读分析快照。只传给其实际 schema 接受此参数的读取/静态工具；不能用它编辑版本或启动原生编译。
- `list_gxworks2_sessions` 返回的 `session_id`：发现的 GX 窗口身份，用于 `read_gxworks2_snapshot`。
- 持续工程会话的 `session_id`：由 `execute_native_engineering` 返回，用于继续、暂停、查询当前修订。与发现会话不是同一种 ID。
- `target_ref` / `view_ref`：Simulator2 目标 / 采样视图。停止视图不等于停止模拟器。
- `generation_context_id`：复制本次上下文返回值，不自造。`program`、`source_name`、`network_id`、声明表名同样从读取结果取得。

## 默认工作方式

**普通新需求。** 复用明确的 CPU/I/O，取生成上下文，按合同生成候选；不强行转 Design，不再次请求模型生成，不手工拼接 GXW/CSV 绕过工具。缺少真实工艺事实时才提问；工具可读取的工程事实先读取。

**已有文件。** 只读走发现快照；编辑走保存版本。已有源码优先离线编辑，不为普通文件修改启动 IDE。源文件中的未知记录不等于不存在；遵守工具返回的可编辑范围。

**原生编译。** `validate_project(native=true)` 是无 IDE 的检查入口。不要把 Python 内部 `NativeWorkspaceSourceSave.compile_project()` 当作同名 MCP 工具。需要编译并保存产物时，只使用当前语言/模式确实提供的保存操作；例如受支持的 FBD `operation=compile` 或已绑定的原生工程方案，不把只读检查说成编译交付。

**可见编辑。** 仅用户要求可见过程或当前任务确需原生工程操作时选择 live。`plan.mode=live`、`session_action=start/continue` 等参数服从实际 schema；从 `get_native_engineering_session` 获取当前 `controls.expected_revision`。不在每个微小编辑后自动保存、关闭、重开；重开是明确的独立步骤。

## 结果与恢复

分别报告“候选生成、版本保存、原生编译、公开检查、源码/代码对应、运行观察”。`ok=true`、存在文件、返回 ID、没有弹窗，均不自动代表编译通过或执行正确。

local 模式可以经共享事务保存版本；Web service 通常形成提案；固定 context 模式可能只返回候选。以当前模式 instructions 与实际回执为准。`confirmation_required`、`pending`、`execution_consent_required` 不能写成已保存或已导入；工具调用许可不替代工程审批。

失败时先看 `error.code`、结构化 details、`next_actions` 和已完成阶段。不按中文/英文某个词决定工具是否永久不可用。不重复提交未知结果的写入；先查 `get_operation_status` 或工程会话。组件版本/环境故障不是 PLC 程序被编译器拒绝，不能因此自动改用鼠标键盘或声称“无法无界面编译”。旧服务可能只给 `TOOL_FAILED`，如实保留信息不足。

## 按需读取参考

- 选择具体工具、确认参数要点：[工具目录](references/tool-catalog.md)。
- 需要多步流程或参数示例：[调用流程](references/workflows.md)。
- 接入模式、错误、结果解释：[结果与恢复](references/result-handling.md)。
- 源码版本与后续更新位置：[来源说明](references/sources.md)。

当前连接的工具 schema/instructions 优先于静态示例。只读接入公开源码不意味着所有 PR 功能已安装；发现版本差异时重新核对相关注册和处理函数。
