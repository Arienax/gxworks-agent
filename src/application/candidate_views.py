"""Candidate and saved-version presentation over bounded application queries."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from application.projects import contained, public, record_id
from application.workspace import ConflictError, canonical_hash, read_json


class CandidateViews:
    def __init__(self, projects, state_dir, jobs, proposals, lock):
        self.projects = projects
        self.state_dir = state_dir
        self.jobs = jobs
        self.proposals = proposals
        self.lock = lock

    def output(self, job_id):
        if not self.jobs:
            raise KeyError("No job outputs in read-only mode")
        self.jobs.get(job_id)
        path = contained(self.state_dir / "outputs" / (record_id(job_id) + ".json"), self.state_dir)
        if not path.is_file():
            raise KeyError("No output is available yet")
        output = read_json(path)
        if isinstance(output, dict) and isinstance(output.get("analysis"), dict):
            from plc.specification.confirmed import restore_review_choices
            if isinstance(output.get("spec_draft"), dict):
                output["spec_draft"] = restore_review_choices(output["spec_draft"], output["analysis"])
        return public(output)

    def render_ladder_preview(
        self, program, *, theme=None, confirmed_spec=None, validation_profile="strict"
    ):
        """Deterministic, in-memory view. Never trust an old rendered-file cache."""
        from plc.ir import ir_to_ladder, validate_plc_ir
        from plc.st_renderer import render_plc_ir_to_st
        from rendering.ladder_svg import AdvancedSVGLadder

        validate_plc_ir(
            program, confirmed_spec=confirmed_spec,
            validate_ladder=(validation_profile != "generation_structural"),
        )
        ladder = ir_to_ladder(program)
        svg = AdvancedSVGLadder().generate_ladder(json.dumps(ladder, ensure_ascii=False))
        return {"target_mode": "ladder", "ladder": ladder, "program": public(program),
                "svg": self.projects.themed_svg(svg, theme), "st": render_plc_ir_to_st(program)}

    def version_preview(self, project_id, version_id, *, theme=None):
        """Re-render a saved ladder from its verified IR without changing a version.

        A missing/corrupt SVG can be recovered as a display-only response. The
        original files, approval state, project history and GX state stay intact.
        """
        from plc.ir import canonical_sha256

        version = self.projects.raw_version(project_id, version_id)
        if version.get("target_mode") != "ladder":
            raise ValueError("Only a ladder version has a regenerable ladder preview")
        program = self.projects.program(project_id, version_id)
        if not program:
            raise KeyError("Canonical program is unavailable")
        if version.get("ir_sha256") and canonical_sha256(program) != version["ir_sha256"]:
            raise ConflictError("Version IR changed after validation")
        return {**self.render_ladder_preview(
                    program, theme=theme,
                    confirmed_spec=version.get("confirmed_spec_snapshot"),
                    validation_profile=version.get("validation_profile", "strict")),
                "version_id": version_id, "read_only": True}

    def generation_preview(self, job_id, *, theme=None):
        """Inspect a completed candidate, including explicitly blocked diagnostics.

        This is not an acceptance route. Legacy completed jobs retain their
        staged IR and can be inspected without paying for another generation.
        """
        from plc.ir import canonical_sha256

        if not self.jobs:
            raise KeyError("Generation jobs are unavailable")
        job = self.jobs.get(job_id)
        if job["kind"] not in {"generation", "direct_generation"} or job["status"] != "completed":
            raise KeyError("No completed generation is available")
        self.projects.raw_project(job["project_id"])
        output = self.output(job_id)
        if output.get("proposal_id"):
            proposal = self.proposals.get(output["proposal_id"])
            if proposal["project_id"] != job["project_id"]:
                raise ConflictError("Generation proposal belongs to another project")
            return {**self.proposal_preview(proposal["id"], theme=theme), "job_id": job_id,
                    "read_only": True, "proposal_id": proposal["id"]}
        metadata = output.get("generation") or {}
        mismatch = metadata.get("contract_mismatch")
        if not mismatch or output.get("status") != "contract_mismatch" or metadata.get("target_mode") != "ladder":
            raise KeyError("No inspectable generation candidate is available")
        root = contained(self.state_dir / "staging" / record_id(job_id), self.state_dir / "staging")
        path = contained(root / "program.ir.json", root)
        if not path.is_file():
            raise KeyError("Generated IR is unavailable")
        program = read_json(path)
        if canonical_sha256(program) != metadata.get("ir_sha256"):
            raise ConflictError("Generated IR changed after validation")
        # Display the known approach mismatch; do not waive it for acceptance.
        # Structural, instruction and IR consistency checks remain mandatory.
        return {**self.render_ladder_preview(program, theme=theme),
                "job_id": job_id, "read_only": True, "status": "contract_mismatch",
                "contract_mismatch": public(mismatch),
                "validation": public(metadata.get("validation") or {})}

    def candidate_diff(self, project_id, base_version_id, payload):
        """Review the proposal's bound version, never the UI's active selection."""
        if "_candidate_ir" in payload:
            from plc.core import PLCCore
            before = self.projects.program(project_id, base_version_id) if base_version_id else None
            expected = payload.get("base_ir_sha256")
            if expected and canonical_hash(before) != expected:
                raise ConflictError("The proposal's base program changed")
            result = dict(PLCCore().diff_programs(before, payload["_candidate_ir"]))
        elif payload.get("target_mode") == "fbd":
            from application.fbd import graph_diff, staged_bytes
            from gxw.object_model import read_project_context
            before = None
            artifacts = staged_bytes(payload, self.state_dir)
            after = json.loads(artifacts['fbd'])
            if base_version_id and self.projects.raw_version(project_id, base_version_id)["target_mode"] == "fbd":
                base = self.projects._fbd_context(project_id, base_version_id)
                program = after['program']
                if program not in base.programs:
                    # Native save verifies Body/Program role normalization and
                    # records that mapping; never guess it from an FB name.
                    report = json.loads(artifacts['write_report'])
                    matches = [row['object'] for row in report.get('objects', [])
                               if row.get('saved_object') == program and row.get('object') in base.programs]
                    if len(matches) == 1:
                        program = matches[0]
                if program in base.programs:
                    before = read_project_context(base.raw, program).object_model()
            result = graph_diff(before, after)
            report = json.loads(artifacts['write_report'])
            if report.get('operation') == 'native_compilation_save':
                result['compilation_save'] = {'public_check':report['cold_validation']['public_check'],
                    'saved_code':report['cold_validation']['inspection']['saved_code'],
                    'gui_configuration':report.get('validation', {}).get('gui_configuration', 'not_verified'), 'execution':'not_run'}
                result['has_changes'] = True
                result['unified_diff'] += '\n+ 已保存原生编译结果；冷重开代码与重新编译一致，运行尚未验证。\n'
                if report.get('validation', {}).get('gui_configuration') != 'matched':
                    result['unified_diff'] += '+ GUI 自动分配配置尚未完整验证，不能视为可见原生会话通过。\n'
            if report.get('operation') == 'native_library_import':
                definition = report['library_import']
                result['library_changes'] = {'added': [definition['name']],
                    'definitions': [row['name'] for row in definition['definitions']],
                    'structures': [row['name'] for row in definition['structures']],
                    'global_tables': [row['name'] for row in definition['global_tables']]}
                result['has_changes'] = True
            if report.get('operation') == 'native_definition_authoring':
                from difflib import unified_diff
                previous, current = report['definition_before'], report['definition_after']
                result['definition_changes'] = {'before': previous, 'after': current}
                text = lambda value: json.dumps(value, ensure_ascii=False, indent=2).splitlines(True)
                result['unified_diff'] += ''.join(unified_diff(text(previous), text(current),
                    fromfile='base/definition', tofile='candidate/definition'))
                result['has_changes'] = result['has_changes'] or previous != current
        elif payload.get("target_mode") == "sfc":
            from application.sfc import read_snapshot, source_diff
            from application.fbd import staged_bytes
            artifacts = staged_bytes(payload, self.state_dir)
            after = json.loads(artifacts['sfc'])
            before = None
            if base_version_id:
                base = self.projects.raw_version(project_id, base_version_id)
                if base.get('target_mode') == 'sfc':
                    raw = self.projects.artifact(project_id, base_version_id, 'gxw').read_bytes()
                    report = json.loads(artifacts['write_report'])
                    originals = [row['object'] for row in report.get('objects', [])
                                 if row.get('saved_object', row.get('object')) == after['program']]
                    if len(originals) > 1:
                        raise ValueError('SFC 候选的原始源码名称不唯一。')
                    before = read_snapshot(raw, originals[0] if originals else after['program'])['model']
            result = source_diff(before, after)
        elif payload.get("target_mode") == "st":
            from difflib import unified_diff
            before = ""
            if base_version_id:
                base = self.projects.raw_version(project_id, base_version_id)
                artifacts = base.get('artifacts') or {}
                binding = ((payload.get('metadata') or {}).get('validation') or {}).get('source_binding')
                if (isinstance(binding, dict) and binding.get('kind') == 'program'
                        and binding.get('language') == 'st' and 'gxw' in artifacts):
                    from gxw.fb_authoring import st_program_snapshot
                    saved = self.projects.artifact(project_id, base_version_id, 'gxw').read_bytes()
                    same_owner = [row for row in st_program_snapshot(saved)['programs']
                                  if row['name'].casefold() == binding['owner'].casefold()]
                    if len(same_owner) > 1:
                        raise ValueError('ST 候选在基础工程中的 Program 归属不唯一。')
                    before = same_owner[0]['st_text'] if same_owner else ''
                else:
                    artifact_id = next((key for key in ('st', 'st_from_ir') if key in artifacts), None)
                    if artifact_id:
                        before = self.projects.artifact(project_id, base_version_id, artifact_id).read_text(encoding="utf-8")
            root = contained(Path(payload["staging_dir"]), self.state_dir)
            entry = payload["artifacts"]["st"]
            data = contained(root / entry["path"], root).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ConflictError("Staged candidate changed")
            after = data.decode("utf-8")
            lines = unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"{base_version_id or 'empty'}/program.st", tofile="candidate/program.st")
            result = {"kind": "st", "has_changes": before != after, "before": before, "after": after,
                "unified_diff": "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)}
        else:
            raise ValueError("Candidate has no reviewable program")
        result["base_version_id"] = base_version_id
        return result

    @staticmethod
    def diff_summary(diff):
        if diff["kind"] in ("fbd", "sfc"):
            return {key: value for key, value in diff.items() if key != "unified_diff"}
        if diff["kind"] == "st":
            return {"kind": "st", "modified": ["program.st"] if diff["has_changes"] else []}
        summary = {key: diff[key] for key in ("kind", "added", "deleted", "modified", "device_comments_changed",
                    "before_network_count", "after_network_count")}
        summary["changes"] = [{key: change[key] for key in ("marker", "network", "comment", "instruction_count")}
                              for change in diff["changes"]]
        return summary

    def with_candidate_diff(self, project_id, base_version_id, payload):
        payload = copy.deepcopy(payload)
        # Freeze review data inside the hash-bound private payload. Reopening an
        # accepted or stale proposal therefore still displays its original base.
        payload["_preview_diff"] = self.candidate_diff(project_id, base_version_id, payload)
        return payload

    def proposal_preview(self, proposal_id, *, theme=None):
        record = self.proposals.get(proposal_id)
        payload = self.proposals.read_private(proposal_id)
        if payload.get("target_mode") == "fbd":
            from application.fbd import editor_snapshot, staged_bytes
            artifacts = staged_bytes(payload, self.state_dir)
            model = json.loads(artifacts['fbd'])
            editor = public(editor_snapshot(artifacts['gxw'], model['program']))
            # SVG is a verified engineering artifact, not metadata containing
            # host paths. Use the same bytes as the proposal's diagram.
            editor['source_svg'] = artifacts['svg'].decode('utf-8')
            return {"target_mode": "fbd", "program": public(json.loads(artifacts["fbd"])),
                    "svg": artifacts["svg"].decode("utf-8"), "diff": public(payload["_preview_diff"]),
                    'editor': editor}
        if "_candidate_ir" in payload:
            # read_private verified the frozen payload hash. Re-render each read
            # instead of returning a stale/corrupt cache. No project writes occur.
            return {**self.render_ladder_preview(
                        payload["_candidate_ir"], theme=theme,
                        confirmed_spec=payload.get("_confirmed_spec"),
                        validation_profile=payload.get("_validation_profile", "strict")),
                    "diff": public(payload.get("_preview_diff") or self.candidate_diff(record["project_id"], record["base_version_id"], payload))}
        if payload.get("target_mode") == "sfc":
            from application.fbd import staged_bytes
            artifacts = staged_bytes(payload, self.state_dir)
            return {'target_mode': 'sfc', 'program': public(json.loads(artifacts['sfc'])),
                    'validation': public(payload['metadata']['validation']),
                    'diff': public(payload['_preview_diff'])}
        if payload.get("target_mode") == "st":
            entry = payload["artifacts"]["st"]
            path = contained(Path(payload["staging_dir"]) / entry["path"], self.state_dir)
            return {"target_mode": "st", "st": path.read_text(encoding="utf-8"),
                    "diff": public(payload.get("_preview_diff") or self.candidate_diff(record["project_id"], record["base_version_id"], payload))}
        version_id = record["base_version_id"]
        if not version_id or payload.get("version_id", version_id) != version_id:
            raise ConflictError("Execution proposal has no consistent bound version")
        with self.lock.thread_lock:
            project_id = record["project_id"]
            version = self.projects.version(project_id, version_id)
            artifacts = {item["id"] for item in version["artifacts"]}
            mode = version["target_mode"]
            program = self.projects.program(project_id, version_id) if mode in ("ladder", "fbd") else None
            if mode in ("ladder", "fbd") and program is None:
                raise ValueError("The execution proposal's program is unavailable")
            svg = self.projects.svg_preview(project_id, version_id, theme=theme) if "svg" in artifacts else None
            st_id = "st" if mode == "st" else "st_from_ir" if "st_from_ir" in artifacts else "st"
            st = self.projects.artifact(project_id, version_id, st_id).read_text(encoding="utf-8") if st_id in artifacts else None
            if mode == "st" and st is None:
                raise ValueError("The execution proposal's ST artifact is unavailable")
            return {"action": record["action"], "version_id": version_id, "version": version, "target_mode": mode,
                    "program": public(program), "svg": svg, "st": st, "plan": public(payload.get("plan"))}
