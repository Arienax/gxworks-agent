"""Managed FBD candidates. GXW engineering stays in gxw; approval stays shared."""
from __future__ import annotations

import base64
import copy
from contextlib import nullcontext
from difflib import unified_diff
import hashlib
import json
from pathlib import Path
import tempfile

from gxw.object_model import (default_baseline, export_object_model,
                              prepare_object_project, read_project_context)
from gxw.project_writer import write_prepared_project
from gxw.render import render_structured_svg
from .workspace import ConflictError, atomic_json, canonical_hash, contained, read_json, record_id


class FBDValidationError(ValueError):
    """A source/model validation problem safe to explain in the operator UI."""


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
    return {"programs": sorted(k for k, v in mapping.items() if k.endswith(".Program.pou") and v in payloads)}


def read_snapshot(raw, program_name=None):
    """One shared source projection for Web and tool clients; no native calls."""
    context = read_project_context(raw, program_name)
    return {'programs': context.programs, 'model': context.object_model(),
            'svg': context.svg(), 'gx_compile': 'not_run'}


def catalog_snapshot(raw, program_name=None):
    from gxw.editor import editor_catalog
    return editor_catalog(read_project_context(raw, program_name))


def pack_candidate(payload, root):
    """Keep prepared artifacts private when a runtime has no managed staging."""
    return {key: copy.deepcopy(payload[key]) for key in ('target_mode', 'plc_model', 'artifacts', 'metadata')} | {
        'files': {name: base64.b64encode(data).decode('ascii') for name, data in staged_bytes(payload, root).items()}}


def unpack_candidate(snapshot, output):
    """Restore the already prepared candidate into the existing proposal flow."""
    files = {name: base64.b64decode(data, validate=True) for name, data in snapshot['files'].items()}
    if set(files) != set(snapshot['artifacts']):
        raise ValueError('FBD candidate artifact manifest differs from its files')
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


def _write_candidate(model, baseline=None):
    prepared = prepare_object_project(model, baseline=baseline)
    if not prepared.requires_native_save:
        return write_prepared_project(prepared)
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave
    from .execution import DesktopResourceLock, ExecutionUnavailableError
    try:
        with DesktopResourceLock():
            return NativeWorkspaceSourceSave().save(prepared)
    except ExecutionUnavailableError as error:
        raise ConflictError('GX 原生资源正被其他操作占用，本次未生成候选工程。') from error


def prepare_candidate(output, *, model=None, baseline=None, imported=None, program_name=None,
                      summary="FBD 候选工程", source_ladder=None, plc_model="FX3U"):
    if imported is None:
        if baseline is None and plc_model.upper() != "FX3U":
            raise ValueError("Native FBD generation currently has an FX3U project template only")
        result = _write_candidate(model, baseline=baseline)
        raw, report = result.data, result.report
        program_name = model.get("program")
    else:
        raw = imported
        report = {"operation": "import", "gxw_sha256": hashlib.sha256(raw).hexdigest(),
                  "modified_objects": [], "metadata_changes": []}
    context = read_project_context(raw, program_name)
    graph = context.object_model()
    files = {"gxw": ("program.gxw", raw), "fbd": ("fbd.json", json_bytes(graph)),
             "svg": ("fbd.svg", context.svg().encode("utf-8")),
             "write_report": ("write_report.json", json_bytes(report))}
    messages = ["已校验工程结构、对象和声明；GX Works2 编译尚未执行。"]
    if imported is not None:
        messages.append("导入文件的 CPU 设置保持原样，尚未校验与工作台机型一致。")
    if source_ladder is not None:
        files["source_ladder"] = ("source_ladder.json", json_bytes(source_ladder))
        messages.append("原梯形图及注释保存在 source_ladder.json；未生成尚未验证的 GXW 注释记录。")
    validate_candidate({key: value[1] for key, value in files.items()})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for key, (name, data) in files.items():
        (output / name).write_bytes(data)
        artifacts[key] = {"path": name, "sha256": hashlib.sha256(data).hexdigest()}
    metadata = {"target_mode": "fbd", "plc_model": plc_model, "program_name": context.program.logical_name,
                "summary": summary, "validation": {"status": "validated", "messages": messages,
                "gx_compile": "not_run", "object_issues": graph["issues"]}}
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


def build_fbd_generation_context(request, *, plc_model, confirmed_spec=None, catalog,
                                 previous=None, model_profile=None):
    """Expose the same source-owned interfaces and discovery to API and MCP."""
    from application.capability_context import augment_generation_knowledge
    from application.context_compiler import model_budget
    from application.generation_support import public_generation_specification, public_generation_value
    from gxw.generation_contract import FBD_GENERATION_PROMPT
    from knowledge.evidence import context_manifest
    from plc.specification.provenance import handoff_snapshot

    requirement = public_generation_value(str(request or ""))
    specification = public_generation_specification(confirmed_spec)
    knowledge = augment_generation_knowledge(requirement, plc_model=plc_model, target_mode="fbd",
        confirmed_spec=specification, catalog=catalog,
        token_budget=model_budget(model_profile)["rag_evidence_token_budget"])
    manifest = context_manifest(knowledge, stage="generate" if previous is None else "edit")
    handoff = handoff_snapshot(specification, evidence=manifest, stage=manifest["stage"])
    handoff["capability_discovery"] = copy.deepcopy(manifest["capability_discovery"])
    context = {"schema_version": catalog["schema_version"], "cpu": catalog["cpu"],
               "program": catalog["program"], "catalog": catalog["nodes"],
               "declaration_tables": catalog["declaration_tables"],
               "confirmed_spec": specification, "previous": previous, "request": requirement}
    return {"generation_instructions": FBD_GENERATION_PROMPT + str(knowledge),
            "generation_input": public_generation_value(context), "generation_handoff": handoff}


def generate_candidate(output, snapshot, images, ctx):
    """Ask the existing provider for the supported object contract, then validate it."""
    from application.model_api import _request_model, _user_message_with_images
    from model_runtime.responses import ResponseContract
    project = snapshot["project"]
    baseline = base64.b64decode(snapshot["fbd_baseline"]) if snapshot.get("fbd_baseline") else None
    source_context = read_project_context(baseline if baseline is not None else default_baseline(), snapshot.get('fbd_program'))
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
    if not isinstance(answer, dict) or set(answer) != {"summary", "model"} or not isinstance(answer["summary"], str):
        raise ValueError("FBD generation did not produce a supported object candidate")
    ctx.checkpoint()
    result = prepare_candidate(output, model=answer["model"], baseline=baseline,
                               summary=answer["summary"], plc_model=project.get("plc_model", "FX3U"))
    result["metadata"]["generation_handoff"] = generation["generation_handoff"]
    return result


class FBDService:
    def __init__(self, workbench):
        self.workbench = workbench

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
            if project.get('plc_model', 'FX3U').upper() != 'FX3U':
                raise FBDValidationError('Native FBD generation currently has an FX3U project template only')
            raw = default_baseline()
        return read_project_context(raw, program_name)

    def catalog(self, project_id=None, version_id=None, program=None):
        from gxw.editor import editor_catalog
        wb = self.workbench
        with wb.lock.thread_lock if wb.lock else nullcontext():
            if project_id is None:
                if version_id is not None or program is not None:
                    raise FBDValidationError('Project is required when selecting a saved FBD source')
                return editor_catalog()
            return editor_catalog(self._context(project_id, version_id, program))

    def editor(self, project_id, model=None, command=None, version_id=None):
        from gxw.editor import edit_draft, presentation
        from gxw.models import GXWFormatError
        wb = self.workbench
        with wb.lock.thread_lock if wb.lock else nullcontext():
            try:
                context = self._context(project_id, version_id, (model or {}).get('program'))
                model = model if model is not None else context.object_model() if version_id else context.empty_model()
                templates = context.catalog(declaration_edits=model.get('declaration_edits'))
                result = edit_draft(model, command, templates=templates, context=context)
                templates = context.catalog(declaration_edits=result['model'].get('declaration_edits'))
                result['presentation'] = presentation(result['model'], templates=templates, context=context)
                result['catalog'] = templates
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
                result = _write_candidate(model, baseline=baseline)
                context = read_project_context(result.data, model.get('program'))
                return {"model_sha256": canonical_hash(model),
                        "gxw_sha256": hashlib.sha256(result.data).hexdigest(),
                        "svg": context.svg(), "model": context.object_model(),
                        "gx_compile": "not_run"}
            except GXWFormatError as error:
                raise FBDValidationError(str(error)) from error

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
            args = {"plc_model": project.get("plc_model", "FX3U")}
            if operation == "import":
                args.update(imported=decode_upload(command.get("data_base64", "")),
                            program_name=command.get("program"), summary="导入 GXW 工程中的结构化梯形图/FBD")
            elif operation == "convert":
                from gxw.ladder_lowering import ladder_to_object_model
                if not version or version["target_mode"] != "ladder":
                    raise ValueError("Select a ladder version for FBD conversion")
                source = wb.projects.program(project_id, base_id)
                args.update(model=ladder_to_object_model(source), source_ladder=source,
                            summary="将现有梯形图转换为 GXW 结构化梯形图")
            elif operation == "edit":
                if not baseline:
                    raise ValueError("FBD editing requires an existing FBD version")
                args.update(model=command.get("model"), baseline=baseline, summary="修改 FBD 对象、连接或声明")
            elif operation == "generate":
                if version is not None:
                    raise ValueError("Use edit or convert when a base version is selected")
                args.update(model=command.get("model"), summary="直接生成 GXW 结构化梯形图/FBD")
            else:
                raise ValueError("Unsupported FBD operation")
            wb.state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=wb.state_dir, prefix="fbd-") as directory:
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
