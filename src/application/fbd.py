"""Managed FBD candidates. GXW engineering stays in gxw; approval stays shared."""
from __future__ import annotations

import base64
import copy
from contextlib import contextmanager, nullcontext
from difflib import unified_diff
import hashlib
import json
from pathlib import Path
import tempfile
import time

from gxw.object_model import (export_object_model,
                              prepare_object_project, read_project_context)
from gxw.project_writer import write_prepared_project
from gxw.render import render_structured_svg
from .workspace import ConflictError, atomic_json, canonical_hash, contained, read_json, record_id


class FBDValidationError(ValueError):
    """A source/model validation problem safe to explain in the operator UI."""


AUTHORING_OPERATIONS = frozenset({'create_structure', 'create_st_fb', 'edit_st_fb', 'edit_global_labels', 'create_global_labels', 'edit_pou_labels', 'configure_project', 'remove_empty_program'})


def authoring_command(arguments):
    operation = arguments['operation']
    if operation not in AUTHORING_OPERATIONS or not isinstance(arguments.get('source_name'), str):
        raise ValueError('编写项目定义需要操作和 source_name。')
    if operation != 'configure_project' and arguments.get('options') is not None:
        raise ValueError('options 只用于工程配置。')
    if any(arguments.get(key) is not None for key in ('model', 'data_base64', 'program', 'native_save',
            'library_name', 'resource', 'task', 'scope_mode')):
        raise ValueError('项目定义操作不接受工程导入、图形模型或任务选择。')
    if operation=='remove_empty_program':
        if any(arguments.get(key) is not None for key in ('st_text','members','declarations','options')):
            raise ValueError('移除空白程序只接受 source_name。')
        keys=()
    elif operation=='configure_project':
        if not isinstance(arguments.get('options'),dict) or any(arguments.get(k) is not None for k in ('st_text','members','declarations')):
            raise ValueError('工程配置需要 options，不接受 FB 正文或声明。')
        keys=('options',)
    elif operation == 'create_structure':
        if not isinstance(arguments.get('members'), list) or any(arguments.get(key) is not None for key in ('st_text', 'declarations')):
            raise ValueError('新建结构需要 members，不接受 FB 正文或声明。')
        keys = ('members',)
    elif operation in ('edit_global_labels','create_global_labels','edit_pou_labels'):
        if not isinstance(arguments.get('declarations'),list) or any(arguments.get(k) is not None for k in ('st_text','members')):
            raise ValueError('全局声明编辑需要 declarations，不接受 ST 正文或结构定义。')
        keys=('declarations',)
    else:
        if not isinstance(arguments.get('st_text'), str) or arguments.get('members') is not None:
            raise ValueError('编写 ST FB 需要 st_text，不接受结构成员。')
        if operation == 'create_st_fb' and not isinstance(arguments.get('declarations'), list):
            raise ValueError('新建 ST FB 需要显式 declarations。')
        keys = ('st_text', 'declarations')
    return {'operation': operation, 'source_name': arguments['source_name'],
            **{key: arguments[key] for key in keys if arguments.get(key) is not None}}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def decode_upload(value):
    if not isinstance(value, str) or len(value) > 4 * ((30 * 1024 * 1024 + 2) // 3):
        raise ValueError("GXW upload must contain 1 byte to 30 MiB")
    data = base64.b64decode(value, validate=True)
    if not data or len(data) > 30 * 1024 * 1024:
        raise ValueError("GXW upload must contain 1 byte to 30 MiB")
    return data


def inspect_upload(data_base64):
    from gxw.container_writer import validate_cfb_streams
    from gxw.project_metadata import logical_mapping
    streams = validate_cfb_streams(decode_upload(data_base64))
    mapping = logical_mapping(streams["projectdatalist.xml"])
    payloads = validate_cfb_streams(streams["_hdb"])
    return {"programs": sorted(k for k, v in mapping.items() if k.endswith(".pou") and v in payloads)}


def read_snapshot(raw, program_name=None):
    """One shared source projection for Web and tool clients; no native calls."""
    context = read_project_context(raw, program_name)
    return {'programs': context.programs, 'model': context.object_model(),
            'svg': context.svg(), 'gx_compile': 'not_run'}


def catalog_snapshot(raw, program_name=None):
    from gxw.editor import editor_catalog
    return editor_catalog(read_project_context(raw, program_name))


def libraries_snapshot(raw, library_name=None, *, include_source=False):
    from gxw.library_transfer import library_inventory
    return library_inventory(raw, library_name, include_source=include_source)


def editor_snapshot(raw, program_name=None):
    """Read a candidate's own source context for its read-only editor view."""
    from gxw.editor import presentation
    context = read_project_context(raw, program_name)
    model = context.object_model()
    templates = context.catalog()
    return {'model': model, 'catalog': templates, 'programs': context.programs,
            'task_scopes': context.task_scopes(),
            'presentation': presentation(model, templates=templates, context=context),
            'source_svg': context.svg(), 'gx_compile': 'not_run'}


def pack_candidate(payload, root):
    """Keep prepared artifacts private when a runtime has no managed staging."""
    return {key: copy.deepcopy(payload[key]) for key in ('target_mode', 'plc_model', 'artifacts', 'metadata')} | {
        'files': {name: base64.b64encode(data).decode('ascii') for name, data in staged_bytes(payload, root).items()}}


def unpack_candidate(snapshot, output):
    """Restore the already prepared candidate into the existing proposal flow."""
    files = {name: base64.b64decode(data, validate=True) for name, data in snapshot['files'].items()}
    if set(files) != set(snapshot['artifacts']):
        raise ValueError('FBD candidate artifact manifest differs from its files')
    if snapshot['target_mode'] == 'sfc':
        from .sfc import validate_candidate as validate_sfc_candidate
        validate_sfc_candidate(files)
    elif snapshot['target_mode'] == 'st':
        from .native_st import validate_candidate as validate_st_candidate
        validate_st_candidate(files, plc_model=snapshot['plc_model'],
            program_name=snapshot['metadata'].get('program_name'), metadata=snapshot['metadata'])
    else:
        validate_candidate(files)
    output = Path(output)
    for name, data in files.items():
        path = contained(output / snapshot['artifacts'][name]['path'], output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {key: copy.deepcopy(snapshot[key]) for key in ('target_mode', 'plc_model', 'artifacts', 'metadata')} | {
        'staging_dir': str(output)}


def staged_bytes(payload, root):
    directory = contained(Path(payload["staging_dir"]), Path(root))
    result = {}
    for name, entry in payload["artifacts"].items():
        data = contained(directory / entry["path"], directory).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ConflictError("Staged FBD artifact changed")
        result[name] = data
    return result


def validate_candidate(artifacts):
    """The reviewed graph/SVG must describe the exact GXW being accepted."""
    if not {"gxw", "fbd", "svg", "write_report"}.issubset(artifacts):
        raise ValueError("FBD candidate is missing required artifacts")
    model = json.loads(artifacts["fbd"])
    context = read_project_context(artifacts['gxw'], model.get('program'))
    sources = context.sources if model.get('schema_version') == 2 else None
    if export_object_model(context.program, context.declarations, sources=sources) != model:
        raise ValueError("FBD projection does not match the GXW project")
    if artifacts['svg'] != render_structured_svg(context.program, sources=sources).encode('utf-8'):
        raise ValueError("FBD preview does not match the GXW project")


@contextmanager
def _native_workspace():
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from .execution import DesktopResourceLock, ExecutionUnavailableError
    try:
        with DesktopResourceLock():
            yield NativeWorkspaceSourceSave()
    except ExecutionUnavailableError as error:
        raise ConflictError('GX 原生资源正被其他操作占用，本次未生成候选工程。') from error


def _native_candidate(operation, *arguments, **keywords):
    with _native_workspace() as workspace:
        return getattr(workspace, operation)(*arguments, **keywords)


def _native_program_candidate(baseline, name, *, resource, task, scope_mode):
    from gxw.native_write import native_program_creation_plan
    from gxw.project_writer import ProjectWriteResult
    plan = native_program_creation_plan(baseline, name, resource=resource, task=task, scope_mode=scope_mode)
    raw, stages = baseline, []
    with _native_workspace() as workspace:
        if plan['create_resource']:
            result = workspace.create_resource(raw, resource, timeout=None)
            raw = result.data; stages.append(result.report)
        if plan['create_task']:
            result = workspace.create_task(raw, task, resource=resource, timeout=None)
            raw = result.data; stages.append(result.report)
        created = workspace.create(raw, name, pou_kind='program', timeout=None)
        stages.append(created.report)
        program = created.report['created_program']
        bound = workspace.bind_program(created.data, program, resource=resource, task=task, timeout=None)
        stages.append(bound.report)
    report = {**bound.report, 'operation': 'native_program_creation',
        'created_program': bound.saved_object_name(program), 'scope_mode': scope_mode, 'stages': stages}
    return ProjectWriteResult(bound.data, report)


def version_requires_native_save(version):
    """Native export can omit Project.gd2; retain the verified save backend."""
    validation = (version or {}).get('validation') or {}
    return validation.get('native_source_save_required') is True or validation.get('native_save') == 'passed'


def _write_candidate(model, baseline=None, *, native_save=False):
    prepared = prepare_object_project(model, baseline=baseline)
    changed = any(old != new for _, old, new in prepared.replacements.values())
    parameter_input = isinstance(model, dict) and 'networks' in model
    if not prepared.requires_native_save and not (native_save and changed) and not parameter_input:
        return write_prepared_project(prepared)
    return _native_candidate('save', prepared)


def _target_baseline(baseline, plc_model):
    """Check CPU identity without choosing a POU or invoking native writes."""
    from gxw.container_writer import validate_cfb_streams
    from gxw.project_metadata import logical_mapping, read_project_text_context
    from gxworks2.project_profiles import native_project_profile
    from gxw.models import GXWFormatError
    from gxw.project_templates import require_fbd_generation_support
    try:
        require_fbd_generation_support(plc_model)
    except GXWFormatError as error:
        raise FBDValidationError(str(error)) from error
    if baseline is None:
        from gxw.project_templates import blank_fbd_baseline
        try:
            baseline = blank_fbd_baseline(plc_model)
        except GXWFormatError as error:
            raise FBDValidationError(str(error)) from error
    outer = validate_cfb_streams(baseline)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    payloads = validate_cfb_streams(outer['_hdb'])
    contexts = [read_project_text_context(payloads[key]) for name, key in mapping.items() if name.endswith('.prj')]
    if len(contexts) != 1:
        raise FBDValidationError('FBD project requires one CPU configuration')
    cpu = contexts[0]['cpu']
    requested = native_project_profile(plc_model)
    actual = native_project_profile(cpu)
    if requested is None or actual is None or not requested.program_supported or requested.cpu != actual.cpu:
        raise FBDValidationError(f'FBD project CPU {cpu!r} does not match selected CPU {plc_model!r}')
    return baseline


def _target_context(baseline, plc_model, program_name=None):
    """Bind generation/edit interfaces to the selected native CPU menu range."""
    return read_project_context(_target_baseline(baseline, plc_model), program_name)


def prepare_candidate(output, *, model=None, baseline=None, imported=None, program_name=None,
                      summary="FBD 候选工程", source_ladder=None, plc_model="FX3U", source_name=None,
                      native_save=False, pou_kind='function_block', resource=None, task=None, scope_mode=None,
                      library_source=None, library_name=None, authoring=None, compile_save=False,
                      _prepared_result=None):
    if compile_save and baseline is None:
        raise ValueError('编译保存需要当前已保存的 FBD 基础版本。')
    scoped = any(value is not None for value in (resource, task, scope_mode))
    if (source_name is None and (scoped or pou_kind != 'function_block')
            or source_name is not None and pou_kind not in ('function_block', 'program')
            or pou_kind == 'function_block' and scoped):
        raise ValueError('资源和任务选择只用于新建普通 FBD Program。')
    if imported is None and source_name is None:
        baseline = _target_baseline(baseline, plc_model)
    if compile_save:
        if baseline is None or any(value is not None for value in (model, imported, source_name, source_ladder, library_source, library_name,authoring)) or scoped:
            raise ValueError('编译保存只接受当前已保存的 FBD 工程。')
        result = _native_candidate('compile_project',baseline)
        raw, report = result.data, result.report
    elif authoring is not None:
        if baseline is None or any(value is not None for value in (model, imported, source_name, source_ladder, library_source, library_name)) or scoped:
            raise ValueError('项目定义操作需要当前 FBD 基础版本。')
        if authoring['operation'] in ('edit_global_labels','create_global_labels'):
            authoring={**authoring,'device_profile':plc_model}
        result = _native_candidate('author', baseline, authoring)
        raw, report = result.data, result.report
        program_name = report['caller'] if authoring['operation']=='remove_empty_program' else result.saved_object_name(program_name or report['caller'])
    elif library_source is not None or library_name is not None:
        if (baseline is None or library_source is None or not isinstance(library_name, str)
                or any(value is not None for value in (model, imported, source_name, source_ladder)) or scoped):
            raise ValueError('库导入需要已有 FBD 基础版本、来源 GXW 和 library_name。')
        result = _native_candidate('import_library', baseline, library_source, library_name)
        raw, report = result.data, result.report
        program_name = result.saved_object_name(program_name)
    elif source_name is not None:
        if baseline is None or any(value is not None for value in (model, imported, program_name, source_ladder)):
            raise ValueError('新建 FBD POU 需要已有 GXW 基础版本和 source_name。')
        baseline = _target_baseline(baseline, plc_model)
        result = (_native_program_candidate(baseline, source_name, resource=resource, task=task, scope_mode=scope_mode)
                  if pou_kind == 'program' else _native_candidate('create', baseline, source_name))
        raw, report = result.data, result.report
        program_name = report['created_program']
    elif imported is None:
        result = (_prepared_result if _prepared_result is not None else
                  _write_candidate(model, baseline=baseline, native_save=native_save))
        raw, report = result.data, result.report
        program_name = result.saved_object_name(model.get("program"))
    else:
        raw = imported
        report = {"operation": "import", "gxw_sha256": hashlib.sha256(raw).hexdigest(),
                  "modified_objects": [], "metadata_changes": []}
    context = read_project_context(raw, program_name)
    graph = context.object_model()
    files = {"gxw": ("program.gxw", raw), "fbd": ("fbd.json", json_bytes(graph)),
             "svg": ("fbd.svg", context.svg().encode("utf-8")),
             "write_report": ("write_report.json", json_bytes(report))}
    if authoring is not None:
        files['definition'] = ('definition.json', json_bytes(report['definition_after']))
    messages = ["已校验工程结构、对象和声明；GX Works2 编译尚未执行。"]
    if imported is not None:
        messages.append("导入文件的 CPU 设置保持原样，尚未校验与工作台机型一致。")
    if source_ladder is not None:
        files["source_ladder"] = ("source_ladder.json", json_bytes(source_ladder))
        messages.append("原梯形图及注释保存在 source_ladder.json；未生成尚未验证的 GXW 注释记录。")
    if isinstance(model, dict) and 'networks' in model:
        files['source_fbd'] = ('source_fbd.json', json_bytes(model))
        messages.append('接口和图形由当前源码生成；原始参数保存在 source_fbd.json。')
    validate_candidate({key: value[1] for key, value in files.items()})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for key, (name, data) in files.items():
        (output / name).write_bytes(data)
        artifacts[key] = {"path": name, "sha256": hashlib.sha256(data).hexdigest()}
    metadata = {"target_mode": "fbd", "plc_model": plc_model, "program_name": context.program.logical_name,
                "summary": summary, "validation": {"status": "validated", "messages": messages,
                "gx_compile": "not_run", "object_issues": graph["issues"],
                'native_save': report.get('validation', {}).get('native_save', 'not_run'),
                'native_source_save_required': native_save or report.get('validation', {}).get('native_save') == 'passed'}}
    if compile_save:
        metadata['validation'].update(report['validation'])
        metadata['native_compilation'] = {'build':report['native_validation'],'cold_reopen':report['cold_validation']}
        messages[:] = ['当前源码已原生编译、完整检查并保存；独立冷重开后的保存代码与重新编译一致。运行验证尚未执行。']
        if report['validation'].get('gui_configuration') != 'matched':
            metadata['validation']['status'] = 'unverified'
            messages.append('GUI 自动分配配置尚未完整验证；已验证的编译、源码检查和保存代码记录分别保留，不能视为可见原生会话通过。')
    if library_source is not None:
        metadata['library_import'] = copy.deepcopy(report['library_import'])
        for definition in metadata['library_import']['definitions']:
            definition.pop('source_text', None)
        metadata['validation']['library_definitions'] = report['validation']['library_definitions']
        metadata['validation']['library_source_text'] = report['validation']['source_text']
        metadata['validation']['initial_value_semantics'] = report['validation']['initial_value_semantics']
        metadata['validation']['native_initial_value_changes'] = report.get('native_initial_value_changes', [])
        messages.append('已通过原生库格式导入定义、结构和库全局表；来源工程的调用者和项目全局声明未导入。')
        if report.get('native_initial_value_changes'):
            messages.append('原生导入改变了声明表中的初始值字段，差异已保留；源码文本一致不表示初值运行语义已验证。')
    if authoring is not None:
        metadata['definition'] = {'name': report['definition_after']['name'], 'kind': report['definition_after']['kind']}
        metadata['validation']['definition_readback'] = 'matched'
        messages.append('项目定义及实现已原生保存并回读，编译和运行验证分别执行。')
    if pou_kind == 'program':
        metadata['validation']['native_program_registration'] = 'passed'
        metadata['validation']['program_scope'] = {'resource': resource, 'task': task, 'scope_mode': scope_mode}
    from plc.maintainability import review_maintainability
    metadata["maintainability_review"] = review_maintainability(graph, target_mode="fbd")
    return {"target_mode": "fbd", "plc_model": plc_model, "staging_dir": str(output),
            "artifacts": artifacts, "metadata": metadata}


def graph_diff(before, after):
    def projection(model):
        if model is None:
            return {"nodes": [], "wires": [], "labels": {}, "cpu": None}
        return {"nodes": [{k: v for k, v in n.items() if k not in ("id", "source_offset")} for n in model["nodes"]],
                "wires": [{k: v for k, v in w.items() if k != "source_offset"} for w in model["wires"]],
                "labels": model["labels"], "program": model["program"], "cpu": model.get('cpu'),
                "unknown_record_count": model["unknown_record_count"]}
    a, b = projection(before), projection(after)
    text = "".join(unified_diff(json_bytes(a).decode().splitlines(True), json_bytes(b).decode().splitlines(True),
                                fromfile="base/fbd", tofile="candidate/fbd"))
    return {"kind": "fbd", "has_changes": a != b, "unified_diff": text,
            "before_object_count": len(a["nodes"]), "after_object_count": len(b["nodes"]),
            "before_wire_count": len(a["wires"]), "after_wire_count": len(b["wires"]),
            "declarations_changed": a["labels"] != b["labels"]}


def prepare_topology_candidate(output, topology, *, baseline, plc_model, program_name=None,
                               summary="FBD 语义连接候选", native_save=False):
    """Internal additive adapter into the existing candidate/save transaction.

    No Web/MCP protocol extension or implicit type/Program creation is involved.
    Caller authorization for native_save remains the existing operation boundary.
    """
    from gxw.topology import assemble_topology, verify_topology
    from application.engineering_acceptance import acceptance_record
    context = read_project_context(baseline, program_name)
    model, receipt = assemble_topology(context, topology, plc_model=plc_model)
    payload = prepare_candidate(output, model=model, baseline=baseline, plc_model=plc_model,
                                summary=summary, native_save=native_save)
    raw = (Path(output) / payload["artifacts"]["gxw"]["path"]).read_bytes()
    saved = read_project_context(raw, payload["metadata"]["program_name"])
    receipt["cold_read"] = verify_topology(saved.program, model, topology["connections"])
    payload["metadata"]["topology_receipt"] = receipt
    payload["metadata"]["engineering_acceptance"] = acceptance_record(
        cpu=context.sources.cpu, source=context.program.logical_name,
        results={"identity_interface": {"status": "passed", "evidence": receipt["binding"]},
                 "save_cold_read": {"status": "passed", "evidence": receipt["cold_read"]}})
    return payload


def build_fbd_generation_context(request, *, plc_model, confirmed_spec=None, catalog,
                                 previous=None, model_profile=None):
    """Expose the same source-owned interfaces and discovery to API and MCP."""
    from application.capability_context import augment_generation_knowledge
    from application.generation_support import public_generation_specification, public_generation_value
    from gxw.generation_contract import FBD_GENERATION_PROMPT
    from knowledge.evidence import context_manifest
    from plc.specification.provenance import handoff_snapshot

    requirement = public_generation_value(str(request or ""))
    specification = public_generation_specification(confirmed_spec)
    knowledge = augment_generation_knowledge(requirement, plc_model=plc_model, target_mode="fbd",
        confirmed_spec=specification, catalog=catalog)
    manifest = context_manifest(knowledge, stage="generate" if previous is None else "edit")
    handoff = handoff_snapshot(specification, evidence=manifest, stage=manifest["stage"])
    handoff["capability_discovery"] = copy.deepcopy(manifest["capability_discovery"])
    parameter_catalog = [{key: value for key, value in item.items() if key not in
                          ('width','height','prototype_offset','ports')} | {
                          'ports':[{key:value for key,value in port.items() if key not in ('x','y')}
                                   for port in item.get('ports',[])]} for item in catalog['nodes']]
    from gxw.generation_contract import FBD_PARAMETER_MODEL_SCHEMA
    context = {"schema_version": 2, "cpu": catalog["cpu"],
               "program": catalog["program"], "catalog": parameter_catalog,
               'parameter_schema': FBD_PARAMETER_MODEL_SCHEMA,
               "declaration_tables": catalog["declaration_tables"],
               "confirmed_spec": specification,
               'existing_declarations': (previous or {}).get('labels', {}),
               "previous": catalog.get('parameter_model', previous) if previous is not None else None,
               "request": requirement}
    from shared.model_context import technical_context_text, technical_value
    return {"generation_instructions": FBD_GENERATION_PROMPT + technical_context_text(knowledge),
            "generation_input": technical_value(public_generation_value(context)), "generation_handoff": handoff}


def generate_candidate(output, snapshot, images, ctx):
    """Ask the existing provider for the supported object contract, then validate it."""
    from application.model_api import _request_model, _user_message_with_images
    from model_runtime.responses import ResponseContract
    project = snapshot["project"]
    baseline = base64.b64decode(snapshot["fbd_baseline"]) if snapshot.get("fbd_baseline") else None
    source_context = _target_context(baseline, project.get('plc_model', 'FX3U'), snapshot.get('fbd_program'))
    previous = source_context.object_model() if baseline is not None else None
    from gxw.editor import editor_catalog
    generation = build_fbd_generation_context(snapshot.get("text", ""),
        plc_model=project.get("plc_model", "FX3U"), confirmed_spec=project.get("confirmed_spec"),
        catalog=editor_catalog(source_context), previous=previous, model_profile=snapshot.get("model"))
    ctx.emit("progress", {"message": "正在生成 FBD 对象、连线及声明"})
    response = _request_model([{"role": "system", "content": generation["generation_instructions"]},
        _user_message_with_images(json.dumps(generation["generation_input"], ensure_ascii=False), images)],
        model_name=snapshot.get("model", {}).get("model"), effort=None, stream=True,
        on_reasoning_chunk=lambda t: ctx.emit("reasoning", {"text": t}),
        on_content_chunk=lambda t: ctx.emit("content", {"text": t}),
        response_contract=ResponseContract("fbd", "json", human_paths=("summary", "unsupported")))
    text = response.message.content.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    answer = json.loads(text)
    if isinstance(answer, dict) and set(answer) == {'unsupported'} and isinstance(answer['unsupported'], str):
        raise FBDValidationError('当前 FBD 生成能力存在缺口：' + answer['unsupported'])
    if not isinstance(answer, dict) or set(answer) != {"summary", "model"} or not isinstance(answer["summary"], str):
        raise ValueError("FBD generation did not produce a supported object candidate")
    ctx.checkpoint()
    result = prepare_candidate(output, model=answer["model"], baseline=source_context.raw,
                               summary=answer["summary"], plc_model=project.get("plc_model", "FX3U"),
                               native_save=version_requires_native_save(snapshot.get('version')))
    result["metadata"]["generation_handoff"] = generation["generation_handoff"]
    return result


class FBDService:
    def __init__(self, workbench):
        self.workbench = workbench
        # One bounded, private artifact. It is reusable only for the exact
        # source/model/policy that produced the already checked preview.
        self._prepared_preview = None

    def _preview_result(self, project_id, version_id, baseline, model, native_save, *, create=False):
        model_key = json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        key = (project_id, version_id, baseline, model_key, native_save)
        cached = self._prepared_preview
        if cached is not None and time.monotonic() < cached['expires'] and cached['key'] == key:
            return copy.deepcopy(cached['result'])
        if not create:
            return None
        result = _write_candidate(model, baseline=baseline, native_save=native_save)
        # Baseline and output are immutable bytes; the model/report need their
        # own copies so caller edits cannot change this artifact's binding.
        self._prepared_preview = (dict(key=copy.deepcopy(key), result=copy.deepcopy(result),
            expires=time.monotonic() + 300) if len(baseline or b'') + len(result.data) <= 64 * 1024 * 1024 else None)
        return result

    def _context(self, project_id, version_id=None, program_name=None):
        wb = self.workbench
        project = wb.projects.raw_project(project_id)
        if version_id:
            version = wb.projects.raw_version(project_id, version_id)
            if version.get('target_mode') != 'fbd':
                raise FBDValidationError('请选择 FBD 版本。')
            raw = wb.projects.artifact(project_id, version_id, 'gxw').read_bytes()
            program_name = program_name or version.get('program_name') or wb.projects.program(project_id, version_id)['program']
        else:
            if project.get('active_version_id'):
                raise ConflictError('Select the current base version before editing an FBD draft')
            raw = None
        return _target_context(raw, project.get('plc_model', 'FX3U'), program_name)

    def catalog(self, project_id=None, version_id=None, program=None):
        from gxw.editor import editor_catalog
        wb = self.workbench
        with wb.lock.thread_lock if wb.lock else nullcontext():
            if project_id is None:
                if version_id is not None or program is not None:
                    raise FBDValidationError('Project is required when selecting a saved FBD source')
                return editor_catalog()
            return editor_catalog(self._context(project_id, version_id, program))

    def editor(self, project_id, model=None, command=None, version_id=None, program=None):
        from gxw.editor import edit_draft, presentation
        from gxw.models import GXWFormatError
        wb = self.workbench
        with wb.lock.thread_lock if wb.lock else nullcontext():
            try:
                if program is not None and model is not None and model.get('program') != program:
                    raise FBDValidationError('所选 POU 与草稿的源码对象不一致。')
                context = self._context(project_id, version_id, program or (model or {}).get('program'))
                model = model if model is not None else context.object_model() if version_id else context.empty_model()
                templates = context.catalog(declaration_edits=model.get('declaration_edits'))
                result = edit_draft(model, command, templates=templates, context=context)
                templates = context.catalog(declaration_edits=result['model'].get('declaration_edits'))
                result['presentation'] = presentation(result['model'], templates=templates, context=context)
                result['catalog'] = templates
                result['programs'] = context.programs
                result['task_scopes'] = context.task_scopes()
                result['source_svg'] = context.svg()
                return result
            except (GXWFormatError, KeyError, TypeError) as error:
                raise FBDValidationError(str(error)) from error

    def preview(self, project_id, model, version_id=None):
        """Validate/render the current draft without saving a proposal or version."""
        from gxw.models import GXWFormatError
        wb = self.workbench
        with wb.lock.thread_lock if wb.lock else nullcontext():
            try:
                baseline = self._context(project_id, version_id, model.get('program')).raw
                version = wb.projects.raw_version(project_id, version_id) if version_id else None
                result = self._preview_result(project_id, version_id, baseline, model,
                    version_requires_native_save(version), create=True)
                context = read_project_context(result.data, result.saved_object_name(model.get('program')))
                return {"model_sha256": canonical_hash(model),
                        "gxw_sha256": hashlib.sha256(result.data).hexdigest(),
                        "svg": context.svg(), "model": context.object_model(),
                        "gx_compile": "not_run"}
            except GXWFormatError as error:
                self._prepared_preview = None
                raise FBDValidationError(str(error)) from error
            except Exception:
                self._prepared_preview = None
                raise

    def propose(self, command):
        from gxw.models import GXWFormatError
        try:
            return self._propose(command)
        except GXWFormatError as error:
            raise FBDValidationError(str(error)) from error

    def _propose(self, command):
        wb = self.workbench
        wb.writable()
        record_id(command["request_id"])
        digest = canonical_hash(command)
        key = hashlib.sha256(command["request_id"].encode()).hexdigest()
        saved_path = wb.state_dir / "fbd_commands" / (key + ".json")
        with wb.lock.thread_lock:
            if saved_path.exists():
                saved = read_json(saved_path)
                if saved["command_hash"] != digest:
                    raise ConflictError("Request ID is already bound to another FBD command")
                return wb.proposals.get(saved["proposal_id"])
            project_id = command["project_id"]
            project = wb.projects.raw_project(project_id)
            base_id = command.get("version_id")
            if base_id is None and project.get("active_version_id") is not None:
                raise ConflictError("Select the current base version before creating an FBD candidate")
            version = wb.projects.raw_version(project_id, base_id) if base_id else None
            baseline = None
            if version and version["target_mode"] == "fbd":
                baseline = wb.projects.artifact(project_id, base_id, "gxw").read_bytes()
            operation = command["operation"]
            if command.get('native_save') is not None and (operation != 'import' or type(command['native_save']) is not bool):
                raise ValueError('native_save 只用于导入时选择后续的原生保存。')
            if operation not in ('create_fb', 'create_program', *AUTHORING_OPERATIONS) and command.get('source_name') is not None:
                raise ValueError('source_name 只用于新建 FBD POU。')
            if operation != 'import_library' and command.get('library_name') is not None:
                raise ValueError('library_name 只用于导入用户库。')
            if operation != 'create_program' and any(command.get(key) is not None for key in ('resource', 'task', 'scope_mode')):
                raise ValueError('资源和任务选择只用于新建普通 FBD Program。')
            if operation not in AUTHORING_OPERATIONS and any(command.get(key) is not None for key in ('members', 'declarations', 'st_text', 'options')):
                raise ValueError('members、declarations、st_text 和 options 只用于编写项目定义或配置。')
            args = {"plc_model": project.get("plc_model", "FX3U")}
            if operation in AUTHORING_OPERATIONS:
                if baseline is None:
                    raise ValueError('编写项目定义需要已保存的 FBD 基础版本。')
                args.update(baseline=baseline, authoring=authoring_command(command), program_name=version.get('program_name'),
                            summary='编写项目定义：' + command['source_name'])
            elif operation == 'compile':
                if baseline is None or any(command.get(key) is not None for key in ('model','data_base64','program')):
                    raise ValueError('编译保存需要当前 FBD 基础版本，不接受源码修改。')
                args.update(baseline=baseline,compile_save=True,program_name=version.get('program_name'),summary='原生编译保存 FBD 工程')
            elif operation == "import":
                args.update(imported=decode_upload(command.get("data_base64", "")),
                            program_name=command.get("program"), summary="导入 GXW 工程中的结构化梯形图/FBD",
                            native_save=command.get('native_save') is True)
            elif operation == 'import_library':
                if baseline is None or not isinstance(command.get('library_name'), str):
                    raise ValueError('库导入需要已保存的 FBD 基础版本和 library_name。')
                if any(command.get(key) is not None for key in ('model', 'program', 'source_name')):
                    raise ValueError('库导入只接受来源 GXW、库名和基础版本。')
                args.update(baseline=baseline, library_source=decode_upload(command.get('data_base64')),
                            library_name=command['library_name'], program_name=version.get('program_name'),
                            summary='导入用户库：' + command['library_name'])
            elif operation == "convert":
                from gxw.ladder_lowering import ladder_to_object_model
                if not version or version["target_mode"] != "ladder":
                    raise ValueError("Select a ladder version for FBD conversion")
                source = wb.projects.program(project_id, base_id)
                converted = ladder_to_object_model(source)
                converted['program'] = _target_context(None, project.get('plc_model', 'FX3U')).program.logical_name
                args.update(model=converted, source_ladder=source,
                            summary="将现有梯形图转换为 GXW 结构化梯形图")
            elif operation == "edit":
                if not baseline:
                    raise ValueError("FBD editing requires an existing FBD version")
                args.update(model=command.get("model"), baseline=baseline, summary="修改 FBD 对象、连接或声明",
                            native_save=version_requires_native_save(version))
            elif operation in ('create_fb', 'create_program'):
                if baseline is None or not isinstance(command.get('source_name'), str):
                    raise ValueError('新建 FBD POU 需要已有 FBD 基础版本和 source_name。')
                if any(command.get(key) is not None for key in ('model', 'data_base64', 'program')):
                    raise ValueError('新建 FBD POU 只接受名称、目标任务和基础版本。')
                args.update(baseline=baseline, source_name=command['source_name'],
                            summary=('新建 FBD Program：' if operation == 'create_program' else '新建 FBD FB：') + command['source_name'])
                if operation == 'create_program':
                    args.update(pou_kind='program', **{key: command.get(key) for key in ('resource', 'task', 'scope_mode')})
            elif operation == "generate":
                if version is not None:
                    raise ValueError("Use edit or convert when a base version is selected")
                args.update(model=command.get("model"), summary="直接生成 GXW 结构化梯形图/FBD")
            else:
                raise ValueError("Unsupported FBD operation")
            wb.state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=wb.state_dir, prefix="fbd-") as directory:
                if operation in ('edit', 'generate'):
                    source = baseline if baseline is not None else _target_baseline(None, args['plc_model'])
                    prepared = self._preview_result(project_id, base_id, source, command.get('model'),
                                                    version_requires_native_save(version))
                    if prepared is not None:
                        args['_prepared_result'] = prepared
                payload = prepare_candidate(directory, **args)
                payload["_confirmed_spec"] = copy.deepcopy(project.get("confirmed_spec"))
                payload = wb._with_candidate_diff(project_id, base_id, payload)
                proposal = wb.proposals.create("accept_local", project_id, payload,
                    public_summary={"summary": payload["metadata"]["summary"],
                                    "validation": payload["metadata"]["validation"],
                                    "diff": wb._diff_summary(payload["_preview_diff"])},
                    base_version_id=base_id, request_id="fbd_" + key)
            proposal = wb._save_local_proposal(proposal)
            atomic_json(saved_path, {"command_hash": digest, "proposal_id": proposal["id"]})
            return proposal
