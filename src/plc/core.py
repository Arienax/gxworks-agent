"""Model-free facade over the existing deterministic PLC implementation."""

from __future__ import annotations

import copy
import hashlib
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol

from plc.ir import (
    apply_network_patch,
    build_plc_ir,
    canonical_sha256,
    validate_plc_ir,
)


class PLCCorePort(Protocol):
    def diff_programs(
        self, before: Optional[Mapping[str, Any]], after: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def read_network(self, program: Mapping[str, Any], network_id: str) -> Mapping[str, Any]: ...

    def get_diagnostics(self, program: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def validate_project(
        self, program: Mapping[str, Any], confirmed_spec: Optional[Mapping[str, Any]] = None
    ) -> Mapping[str, Any]: ...

    def patch_program(
        self,
        program: Mapping[str, Any],
        patch: Mapping[str, Any],
        confirmed_spec: Optional[Mapping[str, Any]] = None,
    ) -> Mapping[str, Any]: ...

    def create_program_candidate(
        self,
        ladder: Mapping[str, Any],
        *,
        plc_model: str,
        program_name: str = "MAIN",
        confirmed_spec: Optional[Mapping[str, Any]] = None,
        previous_program: Optional[Mapping[str, Any]] = None,
    ) -> Mapping[str, Any]: ...

    def compile_project(
        self, program: Mapping[str, Any], output_dir: Optional[Path] = None,
        *, validation_profile: str = "strict",
    ) -> Mapping[str, Any]: ...


def _network_map(program: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(item.get("id") or ""): item
        for item in (program.get("networks") or [])
        if isinstance(item, Mapping) and item.get("id")
    }


def _program_diff(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> Dict[str, Any]:
    old = _network_map(before)
    new = _network_map(after)
    added = [key for key in new if key not in old]
    deleted = [key for key in old if key not in new]
    modified = [key for key in new if key in old and new[key] != old[key]]
    changes = []
    for marker, keys, source in (
        ("+", added, new),
        ("-", deleted, old),
        ("~", modified, new),
    ):
        for network_id in keys:
            network = source[network_id]
            changes.append(
                {
                    "marker": marker,
                    "network": network_id,
                    "comment": str(network.get("comment") or ""),
                    "instruction_count": len(network.get("instructions") or []),
                }
            )
    def comments(program):
        return {
            str(address): str(record.get("comment") or "")
            for address, record in (program.get("devices") or {}).items()
            if isinstance(record, Mapping)
            and (record.get("comment_declared") or record.get("comment"))
        }

    old_comments = comments(before)
    new_comments = comments(after)
    return {
        "added": added,
        "deleted": deleted,
        "modified": modified,
        "changes": changes,
        "device_comments_changed": old_comments != new_comments,
        "before_network_count": len(old),
        "after_network_count": len(new),
    }


class PLCCore:
    """Thin facade; all PLC semantics stay in the existing modules."""

    def __init__(self, candidate_service=None):
        from plc.candidate_service import CandidateService
        self.candidates = candidate_service or CandidateService()

    def diff_programs(
        self, before: Optional[Mapping[str, Any]], after: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Return a deterministic review of two IRs; None denotes first creation.

        The existing network summary remains stable. Each changed network also
        carries copied old/new values so an operator can review actual edits.
        No model, rendering, workspace mutation, or GX operation is involved.
        """
        if before is not None:
            validate_plc_ir(before, validate_ladder=False)
        validate_plc_ir(after, validate_ladder=False)
        old_program = before if before is not None else {}
        result = _program_diff(old_program, after)
        old, new = _network_map(old_program), _network_map(after)
        for change in result["changes"]:
            change["before"] = copy.deepcopy(old.get(change["network"]))
            change["after"] = copy.deepcopy(new.get(change["network"]))
        comments = lambda program: {
            str(address): str(record.get("comment") or "")
            for address, record in (program.get("devices") or {}).items()
            if isinstance(record, Mapping) and (record.get("comment_declared") or record.get("comment"))
        }
        old_comments, new_comments = comments(old_program), comments(after)
        result["device_comment_changes"] = [
            {"address": address, "before": old_comments.get(address), "after": new_comments.get(address)}
            for address in sorted(old_comments.keys() | new_comments.keys())
            if old_comments.get(address) != new_comments.get(address)
        ]
        result["before_network_order"] = list(old)
        result["after_network_order"] = list(new)
        result["network_order_changed"] = list(old) != list(new)
        # Include metadata and non-network semantics instead of silently treating
        # an unchanged network list as an unchanged program.
        result["property_changes"] = {
            key: {"before": copy.deepcopy(old_program.get(key)), "after": copy.deepcopy(after.get(key))}
            for key in sorted(old_program.keys() | after.keys())
            if key not in {"networks", "devices"} and old_program.get(key) != after.get(key)
        }
        result.update(kind="ladder", has_changes=bool(result["changes"] or result["device_comment_changes"]
            or result["network_order_changed"] or result["property_changes"]))
        return result

    def read_network(
        self, program: Mapping[str, Any], network_id: str
    ) -> Mapping[str, Any]:
        validate_plc_ir(program, validate_ladder=False)
        network = _network_map(program).get(str(network_id or "").strip())
        if network is None:
            raise KeyError(f"找不到 Network：{network_id}")
        return copy.deepcopy(dict(network))

    def get_diagnostics(self, program: Mapping[str, Any]) -> Mapping[str, Any]:
        validate_plc_ir(program, validate_ladder=False)
        analysis = program.get("analysis") or {}
        return {
            "counts": copy.deepcopy(analysis.get("counts") or {}),
            "findings": copy.deepcopy(analysis.get("findings") or []),
            "rules_checked": list(analysis.get("rules_checked") or []),
        }

    def validate_project(
        self,
        program: Mapping[str, Any],
        confirmed_spec: Optional[Mapping[str, Any]] = None,
    ) -> Mapping[str, Any]:
        try:
            validate_plc_ir(program, confirmed_spec=confirmed_spec)
        except (TypeError, ValueError) as error:
            return {"valid": False, "error": str(error), "counts": {"error": 1}}
        diagnostics = self.get_diagnostics(program)
        return {
            "valid": int((diagnostics.get("counts") or {}).get("error", 0)) == 0,
            **diagnostics,
        }

    def patch_program(
        self,
        program: Mapping[str, Any],
        patch: Mapping[str, Any],
        confirmed_spec: Optional[Mapping[str, Any]] = None,
    ) -> Mapping[str, Any]:
        candidate = apply_network_patch(program, patch)
        validation = self.validate_project(candidate, confirmed_spec)
        if not validation.get("valid"):
            raise ValueError(
                "候选补丁未通过 PLC 校验：" + str(validation.get("error") or validation.get("counts"))
            )
        return {
            "candidate_id": "candidate_" + uuid.uuid4().hex[:16],
            "base_revision": int(program.get("revision") or 0),
            "base_ir_sha256": canonical_sha256(program),
            "target_revision": int(candidate.get("revision") or 0),
            "candidate_ir_sha256": canonical_sha256(candidate),
            "candidate_ir": candidate,
            "diff": _program_diff(program, candidate),
            "diagnostics": validation,
        }

    def create_program_candidate(
        self, ladder: Mapping[str, Any], *, plc_model: str,
        program_name: str = "MAIN",
        confirmed_spec: Optional[Mapping[str, Any]] = None,
        previous_program: Optional[Mapping[str, Any]] = None,
    ) -> Mapping[str, Any]:
        """Prepare one generated full/partial ladder through the API fast path."""
        from plc.ir import ir_to_ladder

        if not isinstance(ladder, Mapping):
            raise TypeError("ladder must be an object")
        if not isinstance(program_name, str) or not program_name.strip() or len(program_name) > 64:
            raise ValueError("program_name must contain 1 to 64 characters")
        previous_ladder = None
        revision = 1
        if previous_program is not None:
            validate_plc_ir(previous_program, validate_ladder=False)
            previous_ladder = ir_to_ladder(previous_program)
            revision = int(previous_program.get("revision") or 0) + 1
        prepared = self.candidates.prepare(
            dict(ladder), plc_model=plc_model, program_name=program_name,
            revision=revision, confirmed_spec=copy.deepcopy(confirmed_spec),
            previous_ladder=previous_ladder,
        )
        program_ir = prepared["program_ir"]
        validation = {"valid": True, "profile": prepared["validation_profile"],
                      "messages": prepared["validation_messages"],
                      **self.get_diagnostics(program_ir)}
        devices_by_kind: Dict[str, int] = {}
        for device in program_ir["devices"].values():
            kind = device["kind"]
            devices_by_kind[kind] = devices_by_kind.get(kind, 0) + 1
        return {
            "candidate_id": "candidate_" + uuid.uuid4().hex[:16],
            "revision": revision,
            "validation_profile": prepared["validation_profile"],
            "normalization": prepared["normalization"],
            "candidate_ir_sha256": canonical_sha256(program_ir),
            "ladder_sha256": program_ir["source"]["ladder_sha256"],
            "candidate_ir": program_ir,
            "diagnostics": validation,
            "summary": {
                "network_count": len(program_ir["networks"]),
                "network_ids": [network["id"] for network in program_ir["networks"]],
                "instruction_count": sum(len(network["instructions"]) for network in program_ir["networks"]),
                "device_count": len(program_ir["devices"]),
                "devices_by_kind": dict(sorted(devices_by_kind.items())),
            },
        }

    def compile_project(
        self,
        program: Mapping[str, Any],
        output_dir: Optional[Path] = None,
        *,
        validation_profile: str = "strict",
    ) -> Mapping[str, Any]:
        def render(target: Path) -> Mapping[str, Any]:
            rendered = self.candidates.compile(
                program, target, validation_profile=validation_profile,
            )
            # Preserve the public Core manifest; Web also uses renderer metadata.
            return {"artifacts": rendered["artifacts"], "hashes": rendered["hashes"]}

        if output_dir is not None:
            return render(Path(output_dir))
        with tempfile.TemporaryDirectory(prefix="plc-ai-candidate-") as directory:
            return render(Path(directory))


def accept_candidate_patch(store: Any, action: Mapping[str, Any]) -> Mapping[str, Any]:
    """Persist one in-memory candidate as a new local version, never GX sync."""

    project_id = str(action.get("project_id") or "")
    base_version_id = str(action.get("base_version_id") or "")
    candidate = action.get("_candidate_ir")
    confirmed_spec = action.get("_confirmed_spec")
    if not project_id or not base_version_id or not isinstance(candidate, Mapping):
        raise ValueError("候选补丁缺少项目、基础版本或 IR。")
    base_version = store.get_version(project_id, base_version_id)
    base_program = store.load_program_ir(project_id, base_version_id)
    if not isinstance(base_version, Mapping) or not isinstance(base_program, Mapping):
        raise ValueError("候选补丁绑定的基础版本不存在。")
    if canonical_sha256(base_program) != str(action.get("base_ir_sha256") or ""):
        raise ValueError("基础版本已变化，请重新生成候选补丁。")
    expected_spec_hash = action.get("confirmed_spec_hash")
    actual_spec_hash = (
        canonical_sha256(confirmed_spec) if confirmed_spec is not None else None
    )
    if expected_spec_hash != actual_spec_hash:
        raise ValueError("候选补丁绑定的确认规格已变化，请重新生成。")
    validation_profile = str(action.get("_validation_profile") or "strict")
    structural = validation_profile == "generation_structural"
    validate_plc_ir(
        candidate, confirmed_spec=confirmed_spec, validate_ladder=not structural
    )
    if structural:
        from plc.validation import validate_ladder_candidate_structure
        from plc.ir import ir_to_ladder
        validate_ladder_candidate_structure(
            ir_to_ladder(candidate),
            plc_model=str((candidate.get("plc") or {}).get("cpu") or "FX3U"),
        )
    if canonical_sha256(candidate) != str(action.get("candidate_ir_sha256") or ""):
        raise ValueError("候选补丁内容已变化，请重新生成。")

    core = PLCCore()
    version_id = None
    try:
        version_id, output_dir = store.prepare_version(project_id)
        compiled = core.compile_project(
            candidate, output_dir, validation_profile=validation_profile
        )
        metadata = store._ir_metadata(candidate)
        st_path = output_dir / compiled["artifacts"]["st_from_ir"]
        from plc.st_renderer import ST_RENDERER_SCHEMA_VERSION

        metadata.update(
            {
                "target_mode": "ladder",
                "plc_model": str((candidate.get("plc") or {}).get("cpu") or "FX3U"),
                "summary": "AI 候选补丁（用户确认）",
                "st_from_ir_sha256": hashlib.sha256(st_path.read_bytes()).hexdigest(),
                "st_renderer_schema_version": ST_RENDERER_SCHEMA_VERSION,
                "artifacts": dict(compiled["artifacts"]),
                "validation_profile": validation_profile,
                "normalization": copy.deepcopy(action.get("normalization")),
                "generation_handoff": copy.deepcopy(action.get("_generation_handoff")),
                "validation": {
                    "status": "candidate_ready" if structural else "passed",
                    "messages": (["候选结构可解析；需求一致性未在生成后重复判定"]
                                 if structural else ["候选补丁和确定性校验已通过"]),
                },
                "confirmed_spec_snapshot": copy.deepcopy(
                    confirmed_spec
                ),
                "confirmed_spec_hash": actual_spec_hash,
                "parent_version_id": base_version_id,
                "source_candidate_id": str(action.get("candidate_id") or ""),
                "lifecycle_status": "accepted",
            }
        )
        return store.complete_version(project_id, version_id, metadata, activate=True)
    except Exception:
        if version_id is not None and store.get_version(project_id, version_id) is None:
            store.discard_version(project_id, version_id)
        raise


__all__ = ["PLCCore", "PLCCorePort", "accept_candidate_patch"]
