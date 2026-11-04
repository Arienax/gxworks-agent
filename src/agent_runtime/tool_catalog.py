# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Public tool declarations grouped by their stable capabilities."""
from __future__ import annotations
from itertools import chain
from agent_runtime.tool_policy import SAFE_TOOL_NAMES
from agent_runtime.tool_registry import ToolDefinition
from agent_runtime.engineering_schema import (
    _engineering_object,
    _engineering_native_schema,
    _engineering_st_schema,
    _engineering_fbd_schema,
    _engineering_declaration_edits,
    _sfc_child_edit_schema,
    _source_instruction_edit_schema,
    _gxw_edit_schema,
)
from agent_runtime.program_tools import (
    _get_operation_status,
    _get_native_engineering_session,
    _pause_native_engineering_session,
    _get_current_project,
    _get_current_program_info,
    _validate_current_program,
    _read_network,
    _get_diagnostics,
    _validate_project,
    _compile_project,
)
from agent_runtime.manual_tools import (
    _search_plc_manual,
)
from agent_runtime.native_tools import (
    _request_native_engineering,
    _request_gxworks2_import,
    _get_fbd_catalog,
    _read_fbd_project,
    _read_fbd_libraries,
    _read_fbd_definition,
    _read_sfc_project,
    _create_sfc_candidate,
    _read_st_project,
    _create_st_candidate,
)


_EMPTY_OBJECT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def project_definitions(generation):
    yield ToolDefinition(
        "get_current_project",
        "查询 Agent 工程选择状态；未选中时正常返回 selection_status=none 和发现入口，不代表 GX Works2 没有打开工程。",
        _EMPTY_OBJECT_SCHEMA,
        _get_current_project,
    )
    yield ToolDefinition('get_operation_status',
    '查询当前选定工程中真实的提案或执行任务回执。提供 proposal_id 或 execution_job_id 中的一项；返回当前状态、已有执行结果及其保存版本标识，不批准或重试操作。',
    {'type': 'object', 'properties': {
        'proposal_id': {'type': 'string', 'minLength': 1, 'maxLength': 128,
                        'pattern': r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'},
        'execution_job_id': {'type': 'string', 'minLength': 1, 'maxLength': 128,
                             'pattern': r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'}},
        'oneOf': [{'required': ['proposal_id']}, {'required': ['execution_job_id']}],
        'additionalProperties': False}, _get_operation_status)
    yield ToolDefinition('get_native_engineering_session',
    '读取选定工程的持续原生会话、当前确认修订、未保存状态和真实执行回执；省略 session_id 时查询该工程唯一的活动会话，不操作原生页面。',
    _engineering_object({'session_id': {'type': 'string', 'pattern': r'^[a-f0-9]{32}$'}}),
    _get_native_engineering_session)
    yield ToolDefinition('pause_native_engineering_session',
    '请求持续原生会话在当前操作完成并核对后暂停；不打断已发出的原生修改。恢复和单步通过既有 execute_native_engineering 的已审批 continue 请求完成。',
    _engineering_object({'session_id': {'type': 'string', 'pattern': r'^[a-f0-9]{32}$'}}, ['session_id']),
    _pause_native_engineering_session)


def program_definitions(generation):
    yield ToolDefinition(
        "get_current_program_info",
        "读取保存版本或 source_ref 分析快照的程序摘要、Network、覆盖和来源；不读取实时值，不把快照认作可编辑版本。",
        _EMPTY_OBJECT_SCHEMA,
        _get_current_program_info,
    )
    yield ToolDefinition(
        "read_network",
        "按稳定 Network ID 读取当前程序中的一个完整网络。",
        {
            "type": "object",
            "properties": {
                "network_id": {
                    "type": "string",
                    "description": "例如 N0001。",
                    "minLength": 2,
                    "maxLength": 32,
                }
            },
            "required": ["network_id"],
            "additionalProperties": False,
        },
        _read_network,
    )
    yield ToolDefinition(
        "search_plc_manual",
        "检索当前 PLC 型号的指令、语言及软元件资料，用返回的操作数、用途、条件和示例回答问题。coverage 定位目标与待补方面，调用字段说明当前 Core 支持；地址规则提供编号、分配与参数条件。",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要检索的 PLC 指令、软元件、错误码或问题。",
                    "minLength": 1,
                    "maxLength": 500,
                },
                "top_k": {
                    "type": "integer",
                    "description": "补充相关资料条数，1 到 8；明确指令、地址、错误码及依赖额外完整返回。",
                    "minimum": 1,
                    "maximum": 8,
                },
                "debug": {
                    "type": "boolean", "default": False,
                    "description": "仅显式调试时设为 true，返回来源、证据和核验记录。普通生成与分析保持 false。",
                },
                "language": {'type': 'string', 'enum': ['ladder', 'st', 'fbd'],
                    'description': '目标语言；省略时从问题识别，不以语言替代行为/时序问题。'},
                "aspects": {'type': 'array', 'items': {'type': 'string', 'enum': [
                    'arity', 'order', 'roles', 'purpose', 'unit', 'encoding', 'devices', 'types',
                    'effects', 'execution', 'constraints', 'st_call', 'fbd_call', 'operand_range', 'operand_condition',
                    'timing_state', 'same_scan', 'first_execution', 'disabled', 'jump']},
                    'description': '明确要回答的知识维度；省略时自动识别。'},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        _search_plc_manual,
    )
    yield ToolDefinition(
        "get_diagnostics",
        "执行确定性程序静态检查；不读取 RUN 或实时软元件。运行问题需另调用 get_simulator_status/read_simulator_devices。",
        _EMPTY_OBJECT_SCHEMA,
        _get_diagnostics,
    )
    yield ToolDefinition(
        "validate_project",
        (
            '默认运行当前语言的本地检查；native=true 在隔离后台工作区编译和检查当前保存版本的 Ladder/ST/FBD/SFC GXW。'
            '返回 compilation、public_check、诊断及源码对应状态；program 指定请求的 POU，实际范围见 selection/targets。'
            '本工具用于检查，保留当前工程版本。候选确认保存发现可用 GX Works2 后会自动编译；需单独编译保存为新版本时用 execute_native_engineering 的 background 模式。'
        ),
        {"type": "object", "properties": {
            "native": {"type": "boolean", "description": "显式启动已保存 GXW 的离线原生检查；默认 false。"},
            "program": {"type": "string", "minLength": 1, "maxLength": 256,
                        "description": "native=true 时的请求 POU；默认使用版本所选 Program。"},
        }, "additionalProperties": False},
        _validate_project,
    )
    yield ToolDefinition(
        "compile_project",
        "运行 Core 本地管线：Ladder IR 可生成 JSON、SVG、衍生 ST 和 CSV 等临时产物，其他源码形式按当前实现返回静态检查或未编译状态。以 compiled/status/validation 为准；此工具不保存版本。GXW 交付和自动原生编译由候选确认保存流程完成。",
        _EMPTY_OBJECT_SCHEMA,
        _compile_project,
    )
    yield ToolDefinition(
        "validate_current_program",
        "按当前语言运行本地检查：Ladder 校验网络或有序指令；ST 结合声明、类型与调用上下文检查正文；FBD/SFC 返回结构、接口及 issues。返回静态检查与语义覆盖状态，原生编译结果由保存流程或 validate_project(native=true) 提供。",
        _EMPTY_OBJECT_SCHEMA,
        _validate_current_program,
    )


def generation_definitions(generation):
    yield ToolDefinition(
        "get_generation_context",
        (
            "读取 Ladder/ST/FBD/SFC 的生成或离线编辑上下文，按 output_contract 提交候选或联合事务。无 GXW 的 ST/SFC 返回 operation=generate；FBD 返回语言、地址规则、完整接口及关联源码。"
            "候选确认保存优先交付 GXW；可用 GX Works2 自动编译、保存及冷重开。无安装时保留可生成的离线 GXW 并标注未原生编译；无法生成 GXW 时才按语言降级。"
            "编译失败仍保留 GXW 与诊断，失败版本不激活。结果的 delivery 含 preferred_format/actual_format/primary_artifact/degraded/reason，validation 返回验证状态。上下文读取只读。"
        ),
        {
            "type": "object",
            "properties": {
                "user_requirement": {
                    "type": "string", "maxLength": 64000,
                    "description": "逐字传入用户消息，可按时间顺序换行拼接多轮原文。不得加入模型提出的方案、容量、地址或检索词。省略时只读取工程上下文。",
                },
                "workflow": {"type": "string", "enum": ["direct_generation", "detailed"],
                             "description": "新 Ladder 有原始需求时默认 Direct；已确认规格可显式选 Direct，编辑仍沿用原入口。"},
                "execution_mode": {"type": "string", "enum": ["background", "live"],
                    "description": "选择保存版本的后台上下文或持续原生会话当前确认的源码。省略时优先使用已启动的原生会话。"},
                "catalog_scope": {"type": "string", "enum": ["task", "full"], "default": "task",
                    "description": "FBD 目录范围：task 按本次需求及当前源码依赖选择完整接口，full 请求全目录；最终保留项以 generation_input.catalog 为准。缺项在 missing_templates 报告。"},
                "templates": {"type": "array", "items": {"type": "string", "minLength": 1},
                    "description": "FBD 只取这些模板及其完整依赖闭包；省略表示按 catalog_scope 取。"},
            },
            "additionalProperties": False,
        },
        generation.context,
    )
    yield ToolDefinition(
        "create_program_candidate",
        "提交完整或局部 ladder JSON，经共享解析、结构检查和本地编译生成待确认候选。确认保存将指令、声明与注释装配为 GXW，发现可用 GX Works2 后自动编译、保存及冷重开；无法生成 GXW 时返回程序与注释 CSV 及降级原因。device_comments 填最多16字符的软元件用途短名称。",
        {
            "type": "object",
            "properties": {
                "program_name": {
                    "type": "string", "default": "MAIN",
                    "minLength": 1, "maxLength": 64,
                },
                "ladder": {
                    "type": "object",
                    "description": "Full or partial ladder JSON from get_generation_context; the shared API parser validates and normalizes compatible encodings.",
                },
                "generation_context_id": {
                    "type": "string", "maxLength": 128,
                    "description": "Copy the ID returned by get_generation_context to preserve source lineage. Optional; absence or expiry is recorded as a trace gap, not a validation failure.",
                },
            },
            "required": ["ladder"],
            "additionalProperties": False,
        },
        generation.candidate,
        confirmation_required=True,
    )
    yield ToolDefinition(
        "patch_program",
        "在当前保存版本上修改 Network 或来源绑定的 GXW 指令，生成差异及候选。已有 GXW 使用 instruction_edits 的原始 record_index 和完整 expected；P/I 标签用 op=P0 或 I0、args=[]，来源标签 expected 同形。declaration_edits 使用既有表名及 upserts/renames/remove；指令、标签、声明、注释联合离线写回。确认后保存新版本，发现可用 GX Works2 后自动编译、保存及冷重开，原文件保留。",
        {
            "type": "object",
            "properties": {
                "generation_context_id": {
                    "type": "string", "maxLength": 128,
                    "description": "Optional context receipt from get_generation_context; preserves edit discovery evidence without adding a model call.",
                },
                "patch": {
                    "type": "object",
                    "description": "基于当前 revision/sha256 的 Network 补丁；有序 GXW 源码改用 instruction_edits，保留真实执行顺序。",
                    "properties": {
                        "base_revision": {"type": "integer"},
                        "base_ir_sha256": {"type": "string"},
                        "target_revision": {"type": "integer"},
                        "operations": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "operation": {
                                        "type": "string",
                                        "enum": [
                                            "add_network",
                                            "modify_network",
                                            "replace_network",
                                            "delete_network",
                                        ],
                                    },
                                    "network": {"type": "string"},
                                    "after": {"type": "string"},
                                    "ladder": {
                                        "type": "object",
                                        "description": "新增或替换 Network 的完整 ladder rung 对象。",
                                    },
                                },
                                "required": ["operation", "network"],
                            },
                        },
                        "device_comments": {
                            "type": "object",
                            "description": "软元件地址到注释；null 表示删除注释。",
                        },
                        'comment_table': {'type': 'string', 'maxLength': 512},
                        'declaration_edits': _engineering_declaration_edits(),
                        'instruction_edits': {'type': 'array', 'maxItems': 512, 'items': _source_instruction_edit_schema()},
                        'save_policy': {'enum': ['draft', 'checked']},
                    },
                    'anyOf': [{'required': ['operations']}, {'required': ['instruction_edits']},
                              {'required': ['device_comments']}, {'required': ['declaration_edits']}],
                }
            },
            "required": ["patch"],
            "additionalProperties": False,
        },
        generation.patch,
        confirmation_required=True,
    )


def native_definitions(generation):
    yield ToolDefinition(
        "import_current_program_to_gxworks2",
        "请求把当前所选梯形图版本导入已打开的 GX Works2 工程。该工具只生成确认请求；用户明确确认后才会由既有高层导入服务执行。",
        {"type": "object", "properties": {
            "project_path": {"type": "string", "minLength": 1, "maxLength": 4096,
                "description": "当前 GX Works2 打开的 .gxw 完整绝对路径。标题被截断或只有文件名时必须提供，并核对该进程的文件占用；不会打开或切换工程。"},
        }, "additionalProperties": False},
        _request_gxworks2_import,
        confirmation_required=True,
    )
    yield ToolDefinition('execute_native_engineering',
    (
        '对已保存的 Ladder/ST/FBD/SFC GXW，优先使用 plan.mode="background"，commands 依次为 compile、save、reopen。'
        '在隔离后台原生工作区编译、保存并重开核对，成功后交付同语言的新版本；不打开可见编辑会话，不写 PLC。'
        '需要可见编辑时才使用旧批次或 mode="live"，两者仅支持 FBD/ST。持续会话接受原生 FX/Q/L 可编程 CPU 菜单，实际操作按具体 CPU、软件版本和中'
        '英日界面的已验证能力执行；仅参数型号不能执行程序操作。旧可见批次仍限已验证 FX3U。标签、ST FB、普通 ST Program、FBD 调用与改接、分配、编译'
        '、当前地址、保存和重开共用现有审批；新 Program 需明确已有资源和任务。图形动作使用当前源码节点和连线标识，连接显式给出端口及正交路径。不接受路径、PID'
        ' 或原生驱动参数。'
    ),
    _engineering_native_schema(),
    _request_native_engineering, confirmation_required=True)
    yield ToolDefinition('get_fbd_catalog',
    '读取当前 CPU 与工程的 FBD 对象目录、源码端口、声明和 parameter_schema；默认 full，task 或 templates 可选取接口及依赖闭包，缺项见 missing_templates。生成前通过 get_generation_context 取得语言、地址规则及可用路径；目录中的模板表示可调用对象及其接口。',
    {'type': 'object', 'properties': {'program': {'type': 'string'},
        'user_requirement': {'type': 'string', 'description': '用于 task 目录裁剪的用户需求。'},
        'catalog_scope': {'type': 'string', 'enum': ['task', 'full'], 'default': 'full',
            'description': 'task 只返回当前程序依赖闭包，full 返回完整模板表。'},
        'templates': {'type': 'array', 'items': {'type': 'string', 'minLength': 1},
            'description': '只取这些模板及其完整依赖闭包；省略表示按 catalog_scope 取。'}},
     'additionalProperties': False},
    _get_fbd_catalog)
    yield ToolDefinition('read_fbd_project',
    '读取当前已保存 GXW 或上传 GXW 的 FBD 图形、标签、具名引脚和 SVG；不运行 GX Works2。',
    {'type': 'object', 'properties': {'program': {'type': 'string'}, 'data_base64': {'type': 'string'}},
     'additionalProperties': False}, _read_fbd_project)
    yield ToolDefinition('read_fbd_libraries',
    '只读列出当前或上传 GXW 的用户库、FB/FUNCTION 定义、IN_OUT 两侧形参、结构和库全局表；include_source=true 返回已支持的 ST 实现。不会导入来源工程。',
    {'type': 'object', 'properties': {'data_base64': {'type': 'string'},
     'library_name': {'type': 'string', 'minLength': 1, 'maxLength': 255}, 'include_source': {'type': 'boolean'}},
     'additionalProperties': False}, _read_fbd_libraries)
    yield ToolDefinition('read_fbd_definition',
    '读取当前版本自主创建的项目结构和 FB 定义；返回声明、具名接口及 ST 正文或 FBD 图形。source_name 省略时列出全部项目定义。',
    {'type': 'object', 'properties': {'source_name': {'type': 'string', 'minLength': 1, 'maxLength': 255}},
     'additionalProperties': False}, _read_fbd_definition)
    yield ToolDefinition('create_fbd_candidate',
    (
        '复杂工程用职责清晰的 ST 功能块组装，独立状态用独立实例；缺少实现时先 create_st_fb，再读取接口并连接，避免将完整工序展开为大量逻辑门。'
        '尚无保存版本的 FBD 工程可先 create_st_fb，后台工厂按所选 CPU 无基底新建并写入首个定义；后续定义及主图使用该实际版本。'
        '先读取 get_generation_context，按其中 FBD 语言、地址和接口规则编写工程副本。ST 接口/正文与受影响 FBD 调用者联合修改用 edit_gxw_project。generate/edit 的 model 可只填 networks：calls 用 id/type/instance/argum'
        'ents，组合逻辑用 nodes 和 from/to 端口连接。Core 从当前定义生成接口、IN_OUT、分块和布线，不需要坐标；参数 schema 见 ge'
        't_fbd_catalog.parameter_schema。原有 v2 对象编辑仍支持。create_structure 用 source_name/memb'
        'ers 新建类型；create_st_fb 用 source_name/declarations/st_text 编写完整 ST FB；edit_st_fb 省'
        '略 declarations 时保留接口；edit_st_function/edit_st_source 编辑既有 FUNCTION 或明确选中的 ST 正文。已有图形、ST 和声明编辑使用离线事务；'
        '确认保存后优先交付 GXW，发现 GX Works2 自动编译、保存及冷重开；native_save=true 也可要求准备阶段原生保存。edit_global_labels/edit_pou_labels 可用 remove_declarations 删除本表已有行。create_global_labels/edit_global_labels 用表名和 declarations 新'
        '建或更新全局表，结构设备映射用 member_devices；edit_pou_labels 用 POU 名和 declarations 更新局部表。confi'
        'gure_project 用目录 project_configuration 的 source_name 和 options 修改 system_variabl'
        'e_ranges，program_capacity 当前仅支持已验证 FX3U/FX3UC 的 16000/64000。create_fb 新建空 FBD FB'
        ' 后用 edit 编写图形。除首个 create_st_fb 外，定义和配置操作需基础版本。import_library 转移已覆盖的 ST 库实现，不能转移 FBD 库正文；create_program 需资源和任务。compile '
        '在当前基础版本上原生编译、完整检查、保存并冷重开比较三个代码通道，不接受源码修改参数。无法生成 GXW 时交付结构化候选及已有预览；编译失败保留 GXW 与诊断，失败版本不激活。'
    ),
    _engineering_fbd_schema(),
    generation.fbd, confirmation_required=True)
    yield ToolDefinition('read_st_project',
    '读取保存 GXW 中既有 ST Program、FB 或 FUNCTION 的正文、返回类型、局部声明、资源及任务成员；program 使用返回的逻辑 POU 名称。只读，不根据正文生成工程。',
    {'type': 'object', 'properties': {'program': {'type': 'string'}, 'data_base64': {'type': 'string'}},
     'additionalProperties': False}, _read_st_project)
    yield ToolDefinition('create_st_candidate',
    (
        'ST 候选：新工程用 generate 提交 st_text 与 declarations；create_st_program 向已有工程添加 Program，使用该工程实际 resource/task。'
        'edit_st_program/edit_st_fb/edit_st_function/edit_st_source 联合离线写入正文与声明，保留源码身份及 FUNCTION 返回类型；declarations 给出完整列表则替换，省略则保留。'
        'import 导入 GXW；from_candidate 接受 v2 的 program、declaration_edits.upserts 和 task_binding。'
        '确认保存优先交付 GXW，新工程由 GX Works2 1.635.0.1 与 v4 后台组件按实际 CPU 无基底创建，正文和声明绑定实际 Program/任务，自动编译、保存及冷重开。'
        '无法生成 GXW 时返回代码 TXT、全局标签 CSV 与原因；编译失败保留 GXW 和诊断，失败版本不激活。'
    ),
    _engineering_st_schema(),
    _create_st_candidate, confirmation_required=True)
    yield ToolDefinition('read_sfc_project',
    '读取当前 GXW 或上传工程中 SFC 的 CPU、声明、图形引用和子程序。native=true 在隔离工作区只读取得原生格点；已有对应证据时复用，不推断执行语义。',
    {'type': 'object', 'properties': {'program': {'type': 'string'}, 'data_base64': {'type': 'string'}, 'native': {'type': 'boolean'}},
     'additionalProperties': False}, _read_sfc_project)
    sfc_definition = ToolDefinition('create_sfc_candidate',
    (
        'generate 新建 SFC：graph_edits 中的 rebuild 提交完整 symbols，child_edits 写子程序与注册；block 默认 Block/0/SFC，用户可指定 data_name/title/number。'
        '确认保存由 GX Works2 1.635.0.1 与 v4 后台组件按实际 CPU 无基底创建、编译、保存及冷重开。无法生成 GXW 时返回结构化候选及已有预览；编译失败保留 GXW 与诊断。'
        'import 导入 GXW；edit 离线编辑既有 SFC。child_edits 按 kind/name 定位内部 action/zoom/transition，可 create/edit/delete/rename/renumber；'
        'zoom/transition 正文用 instructions、来源绑定 instruction_edits 或原有 records_hex，action 用 number/registrations 绑定 zoom。'
        'graph_edits 的 rebuild 用完整 symbols 增删步骤/条件/分支/汇合/跳转并联合维护内部注册；FX步骤可显式 action_number。'
        'renumber 用 steps/transitions/actions 的 before/after 列表联合编号；swap_steps 保留旧契约。'
        'declaration_edits 按既有局部或全局表名联合增删声明，使用 upserts/renames/remove。'
        'edit 作用于既有 POU 的内部对象、声明和注册，具体范围见当前源码与 CPU 的对象能力。'
    ),
    {'type': 'object', 'properties': {'operation': {'type': 'string', 'enum': ['import', 'edit', 'generate']},
     'data_base64': {'type': 'string'}, 'program': {'type': 'string'}, 'summary': {'type': 'string'},
     'device_comments': {'type': 'object', 'additionalProperties': {'oneOf': [{'type': 'string'}, {'type': 'null'}]}},
     'comment_table': {'type': 'string'},
     'block': _engineering_object({'data_name': {'type': 'string', 'minLength': 1, 'maxLength': 32}, 'title': {'type': 'string', 'maxLength': 128}, 'number': {'type': 'integer', 'minimum': 0, 'maximum': 319}}),
     'save_policy': {'enum': ['draft', 'checked']},
     'declaration_edits': _engineering_declaration_edits(),
     'child_edits': {'type': 'array', 'minItems': 1, 'maxItems': 8192, 'items': _sfc_child_edit_schema()},
     'graph_edits': {'type': 'array', 'minItems': 1, 'maxItems': 8192, 'items': {'oneOf': [{'type': 'object',
         'properties': {'step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 65535},
                        'action_number': {'type': 'integer', 'minimum': 0, 'maximum': 65535}},
         'required': ['step_reference', 'action_number'], 'additionalProperties': False},
         {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['jump']},
             'x': {'type': 'integer', 'minimum': 0, 'maximum': 65535},
             'y': {'type': 'integer', 'minimum': 0, 'maximum': 65535},
             'source_offset': {'type': 'integer', 'minimum': 0},
             'before_step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 65535},
             'target_step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 65535}},
          'required': ['kind', 'before_step_reference', 'target_step_reference'],
          'anyOf': [{'required': ['source_offset']}, {'required': ['x', 'y']}],
          'additionalProperties': False},
         {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['rebuild']},
             'symbols': {'type': 'array', 'minItems': 1, 'maxItems': 8192, 'items': {'type': 'object',
                 'properties': {'kind': {'type': 'string', 'enum': ['initial_step', 'step', 'transition', 'jump', 'end', 'line',
                     'parallel_branch', 'parallel_coupling', 'selective_branch', 'selective_coupling']},
                     **{key: {'type': 'integer', 'minimum': 0, 'maximum': 32767}
                        for key in ('x', 'y', 'number', 'start', 'end', 'action_number')}},
                 'required': ['kind'], 'additionalProperties': False}}},
          'required': ['kind', 'symbols'], 'additionalProperties': False},
         {'type': 'object', 'properties': {'kind': {'type': 'string', 'enum': ['swap_steps']},
             'left_step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 32767},
             'right_step_reference': {'type': 'integer', 'minimum': 0, 'maximum': 32767}},
          'required': ['kind', 'left_step_reference', 'right_step_reference'], 'additionalProperties': False},
         _engineering_object({'kind': {'enum': ['renumber']}, **{key: {'type': 'array', 'minItems': 1,
             'maxItems': 8192, 'items': _engineering_object({field: {'type': 'integer', 'minimum': 0, 'maximum': 32767}
                 for field in ('before', 'after')}, ['before', 'after'])}
             for key in ('steps', 'transitions', 'actions')}}, ['kind'])]}}},
     'required': ['operation'], 'additionalProperties': False}, _create_sfc_candidate, confirmation_required=True)
    yield sfc_definition
    from application.gxw_files import transaction_candidate, read_source_tool
    yield ToolDefinition('read_gxw_source',
        '读取当前保存 GXW 中指定既有程序、FB或FUNCTION的离线编辑模型和声明；program使用逻辑源码身份。'
        '返回同一工程全部源码清单，不切换或保存版本，不调用原生组件。',
        _engineering_object({'program': {'type': 'string', 'minLength': 1, 'maxLength': 512}}),
        read_source_tool)
    yield ToolDefinition('edit_gxw_project',
        '在明确 version_id 的 GXW 上联合编辑多个既有 Ladder/ST/FBD/SFC 源码、接口、声明和注释。'
        'edits 的 source_name 使用检查返回的逻辑身份，command 复用该语言的已有编辑契约；'
        '同时修改 ST 接口/正文和 FBD 调用者时在一次事务提交；从 get_generation_context.output_contract 取得版本和可编辑源码。'
        'save_policy 默认 draft，保留诊断；checked 拒绝新增已知源码错误，两种策略均拒绝新增非法 FBD 字面量。旧错误保留不表示编译通过。'
        '返回联合离线写回的差异和候选；确认后保存新版本，发现可用 GX Works2 后自动编译、保存及冷重开。编译失败保留可下载 GXW 与诊断，当前有效版本保持不变。',
        _gxw_edit_schema(graph_edits=sfc_definition.parameters['properties']['graph_edits']),
        transaction_candidate, confirmation_required=True)


def default_tool_definitions(generation):
    definitions = chain(project_definitions(generation), program_definitions(generation),
                        generation_definitions(generation), native_definitions(generation))
    return sorted(definitions, key=lambda tool: SAFE_TOOL_NAMES.index(tool.name))
