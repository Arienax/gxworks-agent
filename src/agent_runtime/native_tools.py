"""Saved native project queries and approved engineering intents."""
from __future__ import annotations
import copy
import json
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from agent_runtime.tool_errors import ToolPreconditionError, PROJECT_ACTIONS, SOURCE_ACTIONS
from agent_runtime.tool_context import (
    ToolContext,
    _confirmed_spec,
)


def _request_native_engineering(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.native_engineering import normalize_native_plan, validate_native_baseline
    if not isinstance(context.version, Mapping) or context.version.get('target_mode') not in {'fbd', 'st'}:
        raise ValueError('可见原生工程执行需要已选择的结构化 FBD 或 ST 保存版本。')
    baseline = context.native_snapshot or context.fbd_baseline
    if not baseline:
        raise ValueError('可见原生工程执行需要当前版本保存的 GXW 工程。')
    selected_cpu = context.version.get('plc_model') or context.project.get('plc_model')
    if not isinstance(selected_cpu, str) or not selected_cpu.strip():
        raise ToolPreconditionError('PLC_MODEL_REQUIRED', 'Native engineering requires the selected saved project CPU model.')
    try:
        if arguments.get('plan', {}).get('mode') == 'live':
            from application.live_engineering import normalize_live_plan
            plan = normalize_live_plan(arguments['plan'], plc_model=selected_cpu)
        else:
            plan = normalize_native_plan(arguments.get('plan'), plc_model=selected_cpu)
    except ValueError as error:
        raise ToolPreconditionError('INVALID_ARGUMENTS', str(error)) from error
    validate_native_baseline(baseline, plc_model=selected_cpu, live=plan.get('mode') == 'live')
    if context.version['target_mode'] == 'st':
        from gxw.fb_authoring import st_program_snapshot
        from gxw.visible_engineering import normalized_st
        selected = [row for row in st_program_snapshot(baseline)['programs']
                    if row['program'] == context.version.get('program_name')]
        if (len(selected) != 1 or len(selected[0]['task_bindings']) != 1
                or not isinstance(context.st_text, str)
                or normalized_st(selected[0]['st_text']) != normalized_st(context.st_text)):
            raise ValueError('ST 版本正文、当前 Program 及唯一任务绑定不一致。')
    return {'requires_confirmation': True,
        'message': ('工程步骤已冻结，获准后沿同一原生会话执行；保存步骤单独交付版本。'
                    if plan.get('mode') == 'live' else
                    '工程方案已冻结。获准后会在独立 GX Works2 工程副本中执行，核对所请求的验证层后保存新版本。'),
        'pending_action': {'type': 'execute_native_engineering', 'project_id': context.project_id,
                           'version_id': context.version_id, 'plan': plan}}


def _request_gxworks2_import(
    context: ToolContext, _arguments: Mapping[str, Any]
) -> Dict[str, Any]:
    version = context.version
    if not isinstance(version, Mapping):
        raise ValueError("当前没有选中的已生成版本。")
    if str(version.get("target_mode") or "") != "ladder":
        raise ValueError("只有梯形图版本可导入 GX Works2。")
    artifacts = version.get("artifacts") or {}
    missing = [
        label
        for key, label in (
            ("program_csv", "程序 CSV"),
            ("comment_csv", "软元件注释 CSV"),
        )
        if not isinstance(artifacts, Mapping) or not artifacts.get(key)
    ]
    if missing:
        raise ValueError("当前版本缺少" + "、".join(missing) + "。")
    return {
        "requires_confirmation": True,
        "message": "需要用户确认后，才能调用现有的一键导入服务。",
        "pending_action": {
            "type": "import_current_program_to_gxworks2",
            "project_id": context.project_id,
            "project_name": str(context.project.get("name") or "未命名项目"),
            "version_id": context.version_id,
            "revision": version.get("revision"),
            "program_name": str(version.get("program_name") or "MAIN"),
            "plc_model": context.plc_model,
        },
    }


def _get_fbd_catalog(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import _target_baseline, catalog_snapshot

    raw = _target_baseline(context.fbd_baseline, context.plc_model)
    return catalog_snapshot(raw, arguments.get('program') or (context.version or {}).get('program_name')) | {
        'limitations': ['Fresh projects use CPU-matched blank templates for 56 enabled Q/L/FX menu identities; FX1 is unsupported by the current FBD reproduction workflow; parameter-only targets have no FBD Program.',
                        'Callable ports are bound to this project CPU and source declarations.',
                        'GX Works2 compilation is a separate validation step.']}


def _read_fbd_project(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import decode_upload, inspect_upload, read_snapshot

    uploaded = arguments.get('data_base64')
    raw = decode_upload(uploaded) if uploaded is not None else context.fbd_baseline
    if raw is None:
        raise ValueError('当前没有绑定的 FBD 工程；请提供 GXW 文件的 data_base64。')
    if uploaded is not None and arguments.get('program') is None:
        names = inspect_upload(uploaded)['programs']
        if len(names) != 1:
            return {'available': False, 'programs': names, 'message': '请指定要读取的 Program.pou。'}
    return {'available': True, 'version_id': context.version_id or None,
            **read_snapshot(raw, arguments.get('program') or ((context.version or {}).get('program_name') if uploaded is None else None))}


def _read_fbd_libraries(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import decode_upload, libraries_snapshot
    uploaded = arguments.get('data_base64')
    raw = decode_upload(uploaded) if uploaded is not None else context.fbd_baseline
    if raw is None:
        raise ValueError('请提供来源 GXW 的 data_base64 或选择已保存的 FBD 工程。')
    return libraries_snapshot(raw, arguments.get('library_name'), include_source=arguments.get('include_source') is True)


def _read_fbd_definition(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from gxw.fb_authoring import definition_snapshot
    if context.fbd_baseline is None:
        raise ValueError('请选择已保存的 FBD 工程版本。')
    return {'version_id': context.version_id, **definition_snapshot(context.fbd_baseline, arguments.get('source_name'))}


def _create_fbd_candidate(context: ToolContext, arguments: Mapping[str, Any], *, generation_handoff=None) -> Dict[str, Any]:
    from pathlib import Path
    import tempfile
    from application.fbd import (AUTHORING_OPERATIONS, authoring_command, decode_upload, pack_candidate,
                                 prepare_candidate, version_requires_native_save)

    if context.project.get('target_mode') != 'fbd' and (context.version or {}).get('target_mode') != 'fbd':
        raise ValueError('FBD 候选需要绑定 FBD 目标工程。')
    operation = arguments['operation']
    if arguments.get('native_save') is not None and (operation != 'import' or type(arguments['native_save']) is not bool):
        raise ValueError('native_save 只用于 FBD 导入时选择后续原生保存。')
    if operation not in ('create_fb', 'create_program', *AUTHORING_OPERATIONS) and 'source_name' in arguments:
        raise ValueError('source_name 只用于新建 FBD POU。')
    if operation != 'import_library' and 'library_name' in arguments:
        raise ValueError('library_name 只用于导入用户库。')
    if operation != 'create_program' and any(key in arguments for key in ('resource', 'task', 'scope_mode')):
        raise ValueError('资源和任务选择只用于新建普通 FBD Program。')
    if operation not in AUTHORING_OPERATIONS and any(key in arguments for key in ('members', 'declarations', 'st_text', 'options')):
        raise ValueError('members、declarations、st_text 和 options 只用于编写项目定义或配置。')
    if operation in AUTHORING_OPERATIONS:
        if not context.version_id or context.fbd_baseline is None:
            raise ValueError('编写项目定义需要已保存的 FBD 基础版本。')
        kwargs = {'baseline': context.fbd_baseline, 'authoring': authoring_command(arguments),
                  'program_name': context.version.get('program_name')}
    elif operation == 'compile':
        if not context.version_id or context.fbd_baseline is None or any(key in arguments for key in ('model','data_base64','program')):
            raise ValueError('编译保存需要当前 FBD 基础版本，不接受源码修改。')
        kwargs = {'baseline':context.fbd_baseline,'compile_save':True,'program_name':context.version.get('program_name')}
    elif operation == 'generate':
        if context.version is not None or context.fbd_baseline is not None:
            raise ValueError('已有版本的 FBD 工程请使用 edit 并绑定基础版本。')
        if 'data_base64' in arguments or 'program' in arguments:
            raise ValueError('generate 只接受 model 和 summary。')
        kwargs = {'model': arguments.get('model')}
    elif operation == 'edit':
        if not context.version_id or context.fbd_baseline is None:
            raise ValueError('edit 需要绑定已保存的 FBD 基础版本。')
        if 'data_base64' in arguments or 'program' in arguments:
            raise ValueError('edit 的 Program.pou 由 model.program 指定。')
        kwargs = {'model': arguments.get('model'), 'baseline': context.fbd_baseline,
                  'native_save': version_requires_native_save(context.version)}
    elif operation == 'import_library':
        if not context.version_id or context.fbd_baseline is None:
            raise ValueError('库导入需要绑定已保存的 FBD 基础版本。')
        if not isinstance(arguments.get('library_name'), str) or any(key in arguments for key in ('model', 'program', 'source_name')):
            raise ValueError('库导入只接受来源 GXW 的 data_base64、library_name 和 summary。')
        kwargs = {'baseline': context.fbd_baseline, 'library_source': decode_upload(arguments.get('data_base64')),
                  'library_name': arguments['library_name'], 'program_name': context.version.get('program_name')}
    elif operation in ('create_fb', 'create_program'):
        if not context.version_id or context.fbd_baseline is None:
            raise ValueError('新建 FBD POU 需要绑定已保存的 FBD 基础版本。')
        if any(key in arguments for key in ('model', 'data_base64', 'program')):
            raise ValueError('新建 FBD POU 只接受名称、目标任务和 summary。')
        if not isinstance(arguments.get('source_name'), str):
            raise ValueError('新建 FBD POU 需要 source_name。')
        kwargs = {'baseline': context.fbd_baseline, 'source_name': arguments['source_name']}
        if operation == 'create_program':
            kwargs.update(pou_kind='program', **{key: arguments.get(key) for key in ('resource', 'task', 'scope_mode')})
    elif operation == 'import':
        if 'model' in arguments:
            raise ValueError('import 使用原始 GXW，不接受 model。')
        kwargs = {'imported': decode_upload(arguments.get('data_base64')),
                  'program_name': arguments.get('program'), 'native_save': arguments.get('native_save') is True}
    else:
        raise ValueError('Unsupported FBD operation')
    with tempfile.TemporaryDirectory(prefix='gxw-fbd-candidate-') as directory:
        payload = prepare_candidate(directory, **kwargs, plc_model=context.plc_model,
                                    summary=arguments.get('summary') or 'Agent 提出的 FBD 候选')
        if generation_handoff is not None:
            payload['metadata']['generation_handoff'] = copy.deepcopy(generation_handoff)
        elif operation in {'generate', 'edit'}:
            payload['metadata']['generation_handoff'] = {'stage': operation,
                'external_model_use': 'not_observed', 'trace_gaps': ['generation_context_not_observed']}
        graph = json.loads((Path(directory) / 'fbd.json').read_bytes())
        svg = (Path(directory) / 'fbd.svg').read_text(encoding='utf-8')
        authored_definition = json.loads((Path(directory) / 'definition.json').read_bytes()) if 'definition' in payload['artifacts'] else None
        snapshot = pack_candidate(payload, directory)
    action = {'type': 'accept_fbd_candidate', 'project_id': context.project_id,
              'base_version_id': context.version_id or None, 'target_mode': 'fbd',
              'summary': payload['metadata']['summary'], 'validation': payload['metadata']['validation'],
              '_confirmed_spec': copy.deepcopy(_confirmed_spec(context)), '_fbd_candidate': snapshot}
    return {'requires_confirmation': True, 'model': graph, 'svg': svg,
            **({'definition': authored_definition} if authored_definition is not None else {}),
            **({'library_import': copy.deepcopy(payload['metadata']['library_import'])}
               if 'library_import' in payload['metadata'] else {}),
            'generation_handoff': copy.deepcopy(payload['metadata'].get('generation_handoff')),
            'maintainability_review': copy.deepcopy(payload['metadata'].get('maintainability_review')),
            'verification': {'structural_checks_passed': True,
                             'native_verified': operation=='compile' and payload['metadata']['validation'].get('gui_configuration') == 'matched',
                             'gx_compile': payload['metadata']['validation']['gx_compile'],
                             **({key: payload['metadata']['validation'][key] for key in
                                 ('gui_configuration', 'saved_code', 'public_check', 'cold_reopen')
                                 if key in payload['metadata']['validation']} if operation == 'compile' else {}),
                             **({key: copy.deepcopy(payload['metadata']['validation'][key])
                                 for key in ('library_definitions', 'native_initial_value_changes')}
                                if 'library_import' in payload['metadata'] else {})},
            'message': ('FBD 编译保存候选已完成独立冷读和代码对应检查，等待工程确认。'
                        + ('GUI 自动分配配置尚未完整验证，不能视为可见原生会话通过。'
                           if payload['metadata']['validation'].get('gui_configuration') != 'matched' else ''))
                        if operation=='compile' else 'FBD 候选已完成结构、声明与预览校验，等待工程确认；GX Works2 编译尚未执行。',
            'pending_action': action}


def _read_sfc_project(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import decode_upload
    from application.sfc import read_snapshot

    uploaded = arguments.get('data_base64')
    raw = decode_upload(uploaded) if uploaded is not None else context.native_snapshot
    if raw is None:
        raise ValueError('请选择带 GXW 快照的版本或提供已有 SFC 工程。')
    program = arguments.get('program') or ((context.version or {}).get('program_name') if uploaded is None else None)
    graph = context.sfc_graph if uploaded is None else None
    if arguments.get('native') and read_snapshot(raw, program, native_graph=graph)['model']['graph']['position_resolution'] != 'native-source-bound':
        from application.fbd import _native_workspace
        with _native_workspace() as workspace:
            graph = workspace.read_sfc_graph(raw, program, timeout=None)
    return {'available': True, 'version_id': context.version_id or None,
            **read_snapshot(raw, program, native_graph=graph)}


def _create_sfc_candidate(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from pathlib import Path
    import tempfile
    from application.fbd import decode_upload, pack_candidate
    from application.sfc import prepare_candidate

    operation = arguments['operation']
    if operation == 'import':
        if 'child_edits' in arguments or 'graph_edits' in arguments:
            raise ValueError('SFC 导入不接受子程序编辑。')
        keywords = {'imported': decode_upload(arguments.get('data_base64', ''))}
    elif operation == 'edit':
        if not context.version_id or context.native_snapshot is None or 'data_base64' in arguments:
            raise ValueError('SFC 编辑需要当前版本的 GXW 快照，不接受导入字节。')
        keywords = {'baseline': context.native_snapshot, 'child_edits': arguments.get('child_edits'),
                    'graph_edits': arguments.get('graph_edits'), 'native_graph': context.sfc_graph}
    else:
        raise ValueError('SFC 目前支持已有工程的导入及子程序编辑。')
    with tempfile.TemporaryDirectory(prefix='gxw-sfc-candidate-') as directory:
        payload = prepare_candidate(directory, **keywords,
            program_name=arguments.get('program') or ((context.version or {}).get('program_name') if operation == 'edit' else None),
            summary=arguments.get('summary') or 'Agent 提出的 SFC 子程序候选')
        model = json.loads((Path(directory) / 'sfc.json').read_bytes())
        snapshot = pack_candidate(payload, directory)
    return {'requires_confirmation': True, 'model': model,
            'verification': {'structural_checks_passed': True, 'gx_compile': 'not_run',
                             'native_save': payload['metadata']['validation']['native_save']},
            'pending_action': {'type': 'accept_sfc_candidate', 'project_id': context.project_id,
                'base_version_id': context.version_id or None, 'target_mode': 'sfc',
                'summary': payload['metadata']['summary'], 'validation': payload['metadata']['validation'],
                '_confirmed_spec': copy.deepcopy(_confirmed_spec(context)), '_sfc_candidate': snapshot}}


def _read_st_project(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from application.fbd import decode_upload
    from gxw.fb_authoring import st_program_snapshot
    raw = decode_upload(arguments['data_base64']) if 'data_base64' in arguments else context.native_snapshot
    if raw is None:
        raise ValueError('请选择带 GXW 快照的版本或提供已有 ST 工程。')
    snapshot = st_program_snapshot(raw)
    if 'program' in arguments:
        snapshot['programs'] = [row for row in snapshot['programs'] if row['program'] == arguments['program']]
        if len(snapshot['programs']) != 1:
            raise ValueError('没有唯一匹配的普通 ST Program。')
    return {'available': True, 'version_id': context.version_id or None, **snapshot}


def _create_st_candidate(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    import tempfile
    from application.fbd import _native_candidate, decode_upload, pack_candidate
    from application.native_st import prepare_candidate
    operation = arguments['operation']
    if operation == 'from_candidate':
        from application.native_st import candidate_native_plan
        baseline = context.native_snapshot or context.fbd_baseline
        if baseline is None:
            raise ValueError('ST candidate engineering requires a current saved GXW baseline')
        prepared = candidate_native_plan(arguments['candidate'], baseline=baseline, plc_model=context.plc_model,
                                         program_name=(context.version or {}).get('program_name'), confirmed_spec=_confirmed_spec(context))
        result = _request_native_engineering(context, {'plan': prepared['plan']})
        result['inspection'] = prepared['inspection']
        result['pending_action'].update(st_program_name=prepared['candidate']['program']['name'], validation=prepared['inspection'])
        return result
    authored = None
    if operation == 'import':
        raw, selected = decode_upload(arguments['data_base64']), arguments['program']
    else:
        from gxw.st_program import background_st_program_plan
        baseline = context.native_snapshot or context.fbd_baseline
        if baseline is None:
            raise ValueError('普通 ST Program 后台创建和编辑需要当前版本的完整 GXW 工程。')
        command = {key: value for key, value in arguments.items() if key != 'summary'}
        background_st_program_plan(baseline, command)
        authored = _native_candidate('author', baseline, command)
        raw = authored.data
        selected = authored.report['definition_after']['program']
    with tempfile.TemporaryDirectory(prefix='gxw-st-candidate-') as directory:
        payload = prepare_candidate(directory, imported=raw, program_name=selected,
            plc_model=context.plc_model, summary=arguments.get('summary') or '普通 ST Program 工程候选')
        if authored is not None:
            payload['metadata']['validation'].update(native_save='passed', definition_readback='matched')
            payload['metadata']['validation']['messages'] = [
                '普通 ST Program 已在隔离工作区写入并核对源码、声明和任务；编译及活动 IDE 编辑另行验证。']
        snapshot = pack_candidate(payload, directory)
    return {'requires_confirmation': True, 'verification': payload['metadata']['validation'],
        'pending_action': {'type': 'accept_st_candidate', 'project_id': context.project_id,
            'base_version_id': context.version_id or None, 'target_mode': 'st',
            'summary': payload['metadata']['summary'], 'validation': payload['metadata']['validation'],
            '_confirmed_spec': copy.deepcopy(_confirmed_spec(context)), '_st_candidate': snapshot}}
