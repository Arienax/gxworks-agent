---
name: gxworks-mcp-router
description: >-
  为 GXWorks Agent 的三菱 PLC 工程任务选择 MCP 工具和工作流。
  用于 Ladder/ST/FBD/SFC 生成与修改、GXW/CSV/已打开工程读取、
  当前型号 RAG 查询、GX Works2 后台编译保存、原生地址查询和 Simulator2 观察。
metadata:
  compatibility: >-
    需要已连接 GXWorks Agent MCP；原生工程能力依赖服务端 Windows 与 Mitsubishi 安装。
    以实际连接服务的工具 schema、instructions 和 output_contract 为准。
  source-repository: Arienax/gxworks-agent
  source-ref: local-working-tree
  source-base-commit: 9ff788da877b375afd9e98f9b3b1ef18be863b3f
  reviewed-on: '2026-11-09'
  version: '2.0.0'
---

# GXWorks MCP 任务路由

先确定输入是新需求、Agent 保存版本、外部 GXW/CSV，还是已打开的 GX Works2 工程，再选择对应入口。用户指定的工作目录、CPU、语言、程序名和版本直接沿用。当前连接的 MCP 可能来自另一份安装；更新本技能不会重启或切换服务，不能把某份源码的能力当成当前连接已支持的能力。

## 入口选择

| 用户目标 | 入口与下一步 |
| --- | --- |
| 新建完整需求、直接生成或代码移植 | `create_project(generation_mode="direct", plc_model=实际CPU, target_mode=目标语言)` → `get_generation_context` → 按返回的 `output_contract` 提交候选。 |
| 开放式需求，需要比较控制方案 | `create_project(generation_mode="design")` → `get_design_context` → `propose_design_plan` → 用户选定方案并回答必要参数 → `confirm_design_plan` → 生成。 |
| 查看/修改已有 Agent 工程 | `list_projects` → `select_project`，需要指定版本时传 `version_id`；读取该版本实际源码，再使用返回的编辑合同。选择版本不等于激活版本。 |
| 修改或确认需求规格 | `get_project_spec` → `update_project_spec`；用户明确确认展示的草稿后才用 `confirm_project_spec`。普通 Direct 生成不额外插入规格确认流程。 |
| 只读分析外部文件/已打开工程 | `.gxw` 用 `inspect_gxw_project`；CSV 用 `inspect_gxworks2_csv_files`；窗口用 `list_gxworks2_sessions` → `read_gxworks2_snapshot`。不为只读分析创建无关工程。 |
| 导入外部 GXW 后编辑 | `import_gxw_project` 创建源码版本；多程序文件明确选择 `program`，按文件实际 CPU/语言处理。 |
| 查询指令、软元件或诊断码 | `search_plc_manual`，带实际 CPU、目标 `language` 和所需 `aspects`，可一次补查多个实际用到的指令。 |
| 原生编译/保存/冷重开 | 已保存 GXW 的检查用 `validate_project(native=true)`；需要执行编译、保存、重开等步骤时用 `execute_native_engineering` 的后台计划。 |
| 持续可见编辑 | 用户要求时用 `execute_native_engineering` 的 live 会话；复用返回的会话和工程，按修订继续。 |
| 模拟器设备/波形观察 | `list_simulator_targets` → 连接或选择实际目标 → 读取设备或启动 trace。连接和观察不包含下载、RUN 或写 PLC。 |

工具名可能带宿主前缀。参数以运行时 schema 为准，不照搬不同版本示例。创建工程使用工具支持的 CPU 名称（例如 `FX3U`），完整订货型号的点数/输出类型另外保留；不猜型号支持的语言或工程类型。

用户已给出的 CPU 型号、I/O 分配、文件路径与程序名直接复用；仅在读取工程后仍缺少关键现场工艺条件或存在真实多选歧义时向用户确认。`plc_model` 类参数一律传 CPU 系列名（新建对话框名称，如 `FX3U`、`FX3U/FX3UC`、`Q03UDV`），不带点数或规格后缀；用户给的完整订货型号（如 `FX3U-48MR/ES`）只用于读取其点数与规格，创建工程时归一到系列名。检查外部文件或已打开的 GX Works2 窗口时可直接调用发现与快照工具，编辑并保存版本时再选择或创建 Agent 工程。

宿主若提供 `gxworks_generate_ladder`，可按该包装器合同执行新建 Direct 或已确认的 Design。没有包装器时使用上述 MCP 工具链。用户指定本地 ModelScope 等模型配置时，走指定安装的内置模型运行入口；MCP 上下文/候选工具本身不会自动调用 Web 保存的模型配置。详见[工作流](references/workflows.md)。

## 读取新版 RAG 与生成上下文

将相关完整用户需求传给 `get_generation_context.user_requirement`；不要用自己的算法摘要替换用户的时序、I/O 和约束。保留已有确认规格和正在编辑的版本身份。

资料工具默认返回 `context_schema_version=4`，直接读取 `data.document`。已知宿主工具输出容量时，由启动配置 `--read-budget-tokens` 或 `GXWORKS_MCP_READ_BUDGET_TOKENS` 设置交付上限，宿主负责读取和组装。前文资料标记 `$conversation_ref` 时，可用 `gxworks_read_context_unit(ids=[...])` 批量恢复。未配置容量的客户端沿用 `delivery.next` 正文续读，显式 `read_format="paged"` 可请求版本 3 兼容格式。

新 RAG 按实际 CPU、完整指令形式、语言和工程条件组合语义；共享族规则、D/P 修饰、具体操作数条件都要沿用当前返回结果。默认模型上下文会去掉来源维护信息，缺少来源字段不等于缺少技术规则；需要追溯原文时再显式查询调试信息。

多个任务并行时，Design 提交按 `analysis_id` 或 `repair_id` 绑定，确认按用户选中的 `draft_id`。生成按本次 `generation_context_id` 或 `implementation_review.context_document` 绑定。若提供 `manual_documents`，只列实际使用且已完整交付的手册文档。召回族用于选型建议，按实际采用的指令补查缺项。Design 确认后实施已选方案，记录新增的行为取舍即可。FRAG 只使用该上下文实际交付的模板。

检索覆盖、调用准入、Core 可执行语义、GX Works2 编译和 PLC 实际行为是不同结果。可选行为验证未知时说明边界，不擅自阻断正常生成或删改明确需求；也不添加步数上限。分页、错误码与回执判读见[结果处理](references/result-handling.md)。

保存后修正生成的梯形图时，读取当前版本的编辑上下文，只传本次修改；沿用 compact 局部梯级表达和原 `rung_id`，未修改部分由服务端保留。不要复用保存前的生成回执，也不需要重新选型或重查全部手册。手册按 CPU、语言、资料版本复用；程序保存不会使其失效。原生导入的有序指令源码仍沿用来源绑定的补丁合同。


Codex 的 `functions.exec` 应逐个 `text(block.text)` 输出 MCP 文本，输出预算覆盖宿主配置的返回容量。已配置宿主容量时，收到 `HOST_CONTEXT_TOO_LARGE` 可按具体技术缺项查询，或由宿主调整容量。兼容客户端若收到 `delivery.next`，按顺序续读；遇到宿主截断时，先完整重读当前页。工程接口或事实库规模不会被交付容量裁减。

模板、示例的适用限制只约束该具体构造，不能用于淘汰整个指令族。资料规则与现场是否满足前提分开记录；周期、容量等实现选择不得冒充用户条件。取舍依赖的未确认条件写入 `applicability_conditions` 并保留 conditional，不能借引用一条真实规则升级成 known/excluded。

## 候选保存与交付

默认优先交付 GXW。当前共享保存流程在环境和目标支持时创建对应语言工程并尝试原生编译、保存、冷重开；是否执行及是否通过均读取实际 `delivery`、`validation` 和保存回执。已有源码保持不变等路径不保证再次执行原生流程。

- `local`：已授权的生成/编辑经共享事务保存；收到 `version_id` 后用 `get_project_artifacts` 取实际路径。保存的草稿可能未激活。
- `service`：按服务已有策略形成提案；pending 提案尚未保存，只有保存回执才能证明落盘。
- `context`：绑定单工程的工具可能只返回 `confirmation_required`，本模式不能自行批准并提交。

模式和权限以服务 instructions 为准。`compile_project` 是 Core 编译/产物构建，不代表 GX Works2 原生编译或保存。梯形图派生 ST 导出保持默认关闭和界面隐藏；不要把它当作应恢复的功能。用户实际编写的 ST 源码生成是独立入口。

无法生成 GXW 时，Ladder 可返回程序/注释 CSV，ST 可返回 UTF-8 代码 TXT 与全局标签 CSV，FBD/SFC 返回结构化候选和可用预览，并说明实际原因。原生编译失败可能保留供诊断的 GXW，不能称为编译通过，也不要擅自降为 CSV 掩盖错误。以本次合同和返回产物为准。

## 身份与状态

`project_id/version_id` 属于 Agent 保存工程；`source_ref` 是只读来源快照；窗口发现的 `session_id` 与持续原生工程会话的 `session_id` 用途不同；`target_ref/view_ref` 属于模拟器目标和采样视图。不要互换或猜值。`program/source_name/network_id`、资源、任务、端口和声明身份均从实际读取结果取得。

原生工程路径不明或返回 `PROJECT_PATH_UNKNOWN` / `UNBOUND` 时先解决绑定，不能按窗口标题猜工程。保存、编译、界面显示和 Simulator2 行为分别报告；普通生成不自动扩大为模拟器或真实设备写入。

<!-- mitsubishi-programming-system:start -->
## 三菱编程通则

助记符、D/P 修饰及合法组合均采用工具解析结果（如 WAND→DAND）；D/P 通则只解释已解析的修饰。实际调用使用工具给出的完整助记符。

D 表示对应形式的双字数值修饰。各参数的类型、位宽、设备占用及输出布局逐项采用有效参数表；32 位值在 16 位字设备中占连续两字，低字在前、高字在后；原生 32 位软元件每值占一个点。型号范围采用该形式的适用条件。

P 仅规定调用触发：执行条件 OFF→ON 时调用一次，持续 ON 保持单次调用，经过 OFF 后再次上升可再次调用。调用后的持续运行、完成、停止、再次使能，以及条件 OFF 时的保持、清除和硬件状态，均采用该指令的具体定义。

标志位按对应指令定义使用；共享标志位的结果紧随该指令读取，需要跨后续指令保留时复制到普通软元件。

检索时传入用户指定的 language。普通任务使用一种语言的参数表；跨语言改写同时读取来源语言与目标语言，并采用目标语言的调用形式。
<!-- mitsubishi-programming-system:end -->

## 按需参考

- [工作流](references/workflows.md)：Direct/Design、规格编辑、四语言源码操作、后台/live、模型用量统计。
- [结果处理](references/result-handling.md)：分页还原、RAG 语义、保存状态、失败恢复与验证边界。
- [工具目录](references/tool-catalog.md)：按职责找工具；复杂参数读取当前工具 schema。
- [来源与维护](references/sources.md)：当前代码入口及运行时读取的编程通则区块。
