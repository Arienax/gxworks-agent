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
        "检索当前 PLC 型号的技术语义、约束和原创案例；来源与核验记录仅供显式 debug 查询。",
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
                    "description": "返回条数，1 到 8。",
                    "minimum": 1,
                    "maximum": 8,
                },
                "debug": {
                    "type": "boolean", "default": False,
                    "description": "仅显式调试时设为 true，返回来源、证据和核验记录。普通生成与分析保持 false。",
                },
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
            '默认校验当前程序；native=true 在后台隔离工作区调用本机 GX Works2 原生编译与检查，不启动或操作 IDE 窗口。检查当前版本已绑定的 GXW 快照，分别返回编译、公开检查、底层重检、诊断和当前源码对应状态。仅有源码或 '
            'CSV 不能启动原生检查；简单 Ladder 必须有明确原生 CPU，并完整回读源码词法、与当前候选的指令逐项匹配，覆盖外保留为未验证。program 仅标识请'
            '求的 POU 上下文，实际检查范围以 selection 和 targets 为准。适配器缺失、构建失败或协议不匹配按独立错误码返回，不代表程序编译失败。不会操作 PLC 或写入项目版本。'
        ),
        {"type": "object", "properties": {
            "native": {"type": "boolean", "description": "显式在后台编译并检查已保存 GXW，不打开 IDE；默认 false。"},
            "program": {"type": "string", "minLength": 1, "maxLength": 256,
                        "description": "native=true 时的请求 POU；默认使用版本所选 Program。"},
        }, "additionalProperties": False},
        _validate_project,
    )
    yield ToolDefinition(
        "compile_project",
        "从当前 PLC IR 临时生成并校验 JSON、SVG、ST、程序 CSV 和注释 CSV；不会写入项目版本或操作 GX Works2。",
        _EMPTY_OBJECT_SCHEMA,
        _compile_project,
    )
    yield ToolDefinition(
        "validate_current_program",
        "对当前所选梯形图运行确定性的本地结构校验与评审规则，不调用 AI 修改程序。",
        _EMPTY_OBJECT_SCHEMA,
        _validate_current_program,
    )


def generation_definitions(generation):
    yield ToolDefinition(
        "get_generation_context",
        "读取与内置 API 共用的生成提示、当前程序、确认规格、PLC 型号和本地手册上下文；可传本次需求改善检索。无版本时也可用，不调用模型，不返回路径或模型配置。",
        {
            "type": "object",
            "properties": {
                "user_requirement": {
                    "type": "string", "maxLength": 64000,
                    "description": "本次生成或修改需求；省略时仍返回当前工程上下文。",
                },
                "workflow": {"type": "string", "enum": ["direct_generation", "detailed"],
                             "description": "新 Ladder 有原始需求时默认 Direct；已确认规格可显式选 Direct，编辑仍沿用原入口。"},
                "execution_mode": {"type": "string", "enum": ["background", "live"],
                    "description": "选择保存版本的后台上下文或持续原生会话当前确认的源码。省略时优先使用已启动的原生会话。"},
            },
            "additionalProperties": False,
        },
        generation.context,
    )
    yield ToolDefinition(
        "create_program_candidate",
        "提交完整或局部 ladder JSON，复用 API 的兼容整理、结构检查和临时编译，返回待确认候选；device_comments 只写最多16字符的软元件用途短名称，不写动作过程；不再次调用模型或导入 GX Works2。",
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
        "在当前保存版本上修改 Network 或来源绑定的 GXW 指令，生成差异及候选。已有 GXW 使用 instruction_edits 的原始 record_index 和完整 expected；P/I 标签用 op=P0 或 I0、args=[]，来源标签 expected 同形。declaration_edits 使用既有表名及 upserts/renames/remove；指令、标签、声明、注释联合离线写回，原文件保留。不会调用原生编译或同步设备。",
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
        _EMPTY_OBJECT_SCHEMA,
        _request_gxworks2_import,
        confirmation_required=True,
    )
    yield ToolDefinition('execute_native_engineering',
    (
        '请求在独立 GX Works2 进程和托管工程副本中执行显式工程方案。持续会话接受原生 FX/Q/L 可编程 CPU 菜单，实际操作按具体 CPU、软件版本和中'
        '英日界面的已验证能力执行；仅参数型号不能执行程序操作。后台批次仍限已验证 FX3U。标签、ST FB、普通 ST Program、FBD 调用与改接、分配、编译'
        '、当前地址、保存和重开共用现有审批；新 Program 需明确已有资源和任务。图形动作使用当前源码节点和连线标识，连接显式给出端口及正交路径。不接受路径、PID'
        ' 或原生驱动参数。'
    ),
    _engineering_native_schema(),
    _request_native_engineering, confirmation_required=True)
    yield ToolDefinition('get_fbd_catalog',
    '读取当前 CPU 的空白 FBD 模板、工程源码引脚和声明表；启用 56 个 Q/L/FX 原生菜单，当前 FBD 复现流程不支持 FX1，按合并菜单身份选择模板。',
    {'type': 'object', 'properties': {'program': {'type': 'string'}}, 'additionalProperties': False},
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
        '在当前工程副本上编写 FBD。generate/edit 的 model 可只填 networks：calls 用 id/type/instance/argum'
        'ents，组合逻辑用 nodes 和 from/to 端口连接。Core 从当前定义生成接口、IN_OUT、分块和布线，不需要坐标；参数 schema 见 ge'
        't_fbd_catalog.parameter_schema。原有 v2 对象编辑仍支持。create_structure 用 source_name/memb'
        'ers 新建类型；create_st_fb 用 source_name/declarations/st_text 编写完整 ST FB；edit_st_fb 省'
        '略 declarations 时保留接口；edit_st_function/edit_st_source 编辑既有 FUNCTION 或明确选中的 ST 正文。已有图形、ST 和声明编辑使用离线事务；'
        '当前调用显式 native_save=true 才执行原生保存，历史版本的选择不授权后续原生调用。edit_global_labels/edit_pou_labels 可用 remove_declarations 删除本表已有行。create_global_labels/edit_global_labels 用表名和 declarations 新'
        '建或更新全局表，结构设备映射用 member_devices；edit_pou_labels 用 POU 名和 declarations 更新局部表。confi'
        'gure_project 用目录 project_configuration 的 source_name 和 options 修改 system_variabl'
        'e_ranges，program_capacity 当前仅支持已验证 FX3U/FX3UC 的 16000/64000。create_fb 新建空 FBD FB'
        ' 后用 edit 编写图形。定义和配置操作需基础版本。import_library 单独导入现有库；create_program 需资源和任务。compile '
        '在当前基础版本上原生编译、完整检查、保存并冷重开比较三个代码通道，不接受源码修改参数；运行验证另行执行。'
    ),
    _engineering_fbd_schema(),
    generation.fbd, confirmation_required=True)
    yield ToolDefinition('read_st_project',
    '读取保存 GXW 中既有 ST Program、FB 或 FUNCTION 的正文、返回类型、局部声明、资源及任务成员；program 使用返回的逻辑 POU 名称。只读，不根据正文生成工程。',
    {'type': 'object', 'properties': {'program': {'type': 'string'}, 'data_base64': {'type': 'string'}},
     'additionalProperties': False}, _read_st_project)
    yield ToolDefinition('create_st_candidate',
    (
        '普通 ST Program 工程候选。from_candidate 接受 candidate v2（schema_version=2、language=st、p'
        'rogram、declaration_edits.upserts、task_binding），绑定当前 GXW 的实际任务、声明及 CPU，检查后冻结原生创建/'
        '编辑、编译、保存与冷重开方案。也兼容 create_st_program 和 import；edit_st_program/edit_st_fb/edit_st_function 或通用 edit_st_source 联合离线写入既有正文和声明表，保留对象身份及 FUNCTION 返回类型。declarations 是完整列表，省略保留。新建对象继续使用隔离原生路径及 Core '
        '回读。语法、语义、原生编译和行为状态分别记录；方案获准后沿共享原生事务交付，失败不激活版本。'
    ),
    _engineering_st_schema(),
    _create_st_candidate, confirmation_required=True)
    yield ToolDefinition('read_sfc_project',
    '读取当前 GXW 或上传工程中 SFC 的 CPU、声明、图形引用和子程序。native=true 在隔离工作区只读取得原生格点；已有对应证据时复用，不推断执行语义。',
    {'type': 'object', 'properties': {'program': {'type': 'string'}, 'data_base64': {'type': 'string'}, 'native': {'type': 'boolean'}},
     'additionalProperties': False}, _read_sfc_project)
    sfc_definition = ToolDefinition('create_sfc_candidate',
    (
        '离线编辑已有 SFC。child_edits 按 kind/name 定位内部 action/zoom/transition，可 create/edit/delete/rename/renumber；'
        '正文用 instructions、来源绑定 instruction_edits 或原有 records_hex，action 用 number/registrations。'
        'graph_edits 的 rebuild 用完整 symbols 增删步骤/条件/分支/汇合/跳转并联合维护内部注册；FX步骤可显式 action_number。'
        'renumber 用 steps/transitions/actions 的 before/after 列表联合编号；swap_steps 保留旧契约。'
        'declaration_edits 按既有局部或全局表名联合增删声明，使用 upserts/renames/remove。'
        '当前源码、CPU和具体对象能力决定可编辑范围；原生编译与运行分别验证，不新建工程级 SFC POU 或任务。'
    ),
    {'type': 'object', 'properties': {'operation': {'type': 'string', 'enum': ['import', 'edit']},
     'data_base64': {'type': 'string'}, 'program': {'type': 'string'}, 'summary': {'type': 'string'},
     'device_comments': {'type': 'object', 'additionalProperties': {'oneOf': [{'type': 'string'}, {'type': 'null'}]}},
     'comment_table': {'type': 'string'},
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
        'save_policy 默认 draft，保留源码诊断并生成可继续编辑的草稿；checked 要求源码检查通过。'
        '返回统一差异和候选，确认后保存新版本；全程离线，编译和实际运行另行验证。',
        _gxw_edit_schema(graph_edits=sfc_definition.parameters['properties']['graph_edits']),
        transaction_candidate, confirmation_required=True)


def default_tool_definitions(generation):
    definitions = chain(project_definitions(generation), program_definitions(generation),
                        generation_definitions(generation), native_definitions(generation))
    return sorted(definitions, key=lambda tool: SAFE_TOOL_NAMES.index(tool.name))
