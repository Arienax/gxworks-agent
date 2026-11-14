# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Check and repair a job's temporary Ladder candidate before version delivery."""
from __future__ import annotations

import copy
import json
import re

from application.compact_bindings import CompactBindingError, CompactBindings
from application.compact_protocol import CompactProtocolError, expand_compact_partial, normalize_compact
from gxworks2.native_export import GXNativeLoweringError
from plc.candidate_repair import RepairAssemblyError, candidate_base, materialize_partial, validation_diagnostic
from plc.generation import CONFIRMED_AGENT_ORIGIN
from plc.ir import PLCIRValidationError
from plc.specification.repair import patch_device_addresses
from plc.validation import PLCJsonValidationError, should_auto_repair_validation_error


class LadderCandidateFlow:
    def __init__(self, *, candidates, output_dir, plc_model, program_name, revision, previous_ladder,
                 project_id=None, native_delivery=False, on_stage=None, acceptance_cases=None):
        self.candidates = candidates
        self.output_dir = output_dir
        self.plc_model = plc_model
        self.program_name = program_name
        self.revision = revision
        self.previous = candidate_base(previous_ladder)
        self.project_id = project_id
        self.native_delivery = native_delivery
        self.on_stage = on_stage or (lambda stage, message: None)
        self.wire = None
        self.ladder = None
        self.prepared = None
        self.expansion = {}
        self.rendered = None
        self.delivery = {}
        self.behavior = None
        self.issues = []
        self.error = None
        self.repair_base = None
        self.scope = set()
        self.local_field_repair = False
        if acceptance_cases is None:
            from simulator.io_example import load_acceptance_cases
            acceptance_cases = load_acceptance_cases(project_id, plc_model)
        self.cases = acceptance_cases

    def bind_specification(self, confirmed_spec, *, user_text=""):
        from plc.device_identity import canonical_device, reference_device_tokens
        from plc.generation_contract import intent_context
        from simulator.bounded import finite_behavior_traces

        self.spec = copy.deepcopy(confirmed_spec or {})
        self.spec["plc_model"] = self.plc_model
        request_texts = [user_text, *(row.get("text", "") for row in intent_context(self.spec).get("requests", [])
                                      if isinstance(row, dict) and not row.get("superseded_by")
                                      and row.get("source") in {"user_request", "user_answer"})]
        self.known_addresses = {canonical_device(address) for text in request_texts
                                for address in reference_device_tokens(text)}
        self.known_addresses.update(canonical_device(row["address"])
            for row in self.spec.get("io_bindings", []) + self.spec.get("io_table", [])
            if isinstance(row, dict) and isinstance(row.get("address"), str))
        self.bindings = CompactBindings(self.plc_model, self.spec)
        self.traces = finite_behavior_traces(self.spec) if self.spec.get("behavior_constraints") else None

    def assess(self, program, *, allow_field_repair=True):
        from application.generation_agent import prepare_model_candidate

        self.wire = program
        self.ladder = self.prepared = self.rendered = self.behavior = self.error = self.repair_base = None
        self.expansion, self.delivery, self.issues = {}, {}, []
        self.scope = set()
        try:
            self.wire, changes = normalize_compact(program)
            if not isinstance(self.wire, dict):
                raise CompactProtocolError("expected a program object")
            if self.wire.get("mode") == "partial":
                ladder = self._partial(self.wire)
            else:
                self.expansion = prepare_model_candidate(self.wire, self.spec, self.plc_model)
                self.expansion["changes"] = [*changes, *self.expansion["changes"]]
                ladder = self.expansion["ladder"]
            self.ladder = (materialize_partial(self.previous, ladder) if ladder.get("mode") == "partial" else ladder)
            self.prepared = self.candidates.prepare(
                ladder, plc_model=self.plc_model, program_name=self.program_name, revision=self.revision,
                confirmed_spec=self.spec, previous_ladder=self.previous, candidate_origin=CONFIRMED_AGENT_ORIGIN,
                on_progress=lambda message: self.on_stage("candidate_check", message), behavior_traces=self.traces,
            )
            self.ladder = self.prepared["ladder"]
        except (CompactProtocolError, PLCJsonValidationError, PLCIRValidationError, RepairAssemblyError) as error:
            self.error = error
            self._validation_failure(error)
            if allow_field_repair and self.ladder is not None:
                self._repair_proven_field()
            return

        self._check_positional_forms()
        if self.issues:
            return
        self._check_behavior()
        try:
            self.rendered = self.candidates.compile(self.prepared["program_ir"], self.output_dir)
        except GXNativeLoweringError as error:
            self.error = error
            self.issues = [{"category": "export", "reason": "gxworks2_lowering",
                            "actual": str(error), "details": error.details,
                            "rung_ids": [error.rung_id] if error.rung_id is not None else []}]
            self._scope_ladder(self.issues)
            return
        if not self.issues and self.native_delivery:
            self._check_native()

    def _repair_proven_field(self):
        from application.field_repair import apply, deterministic_response, plan

        if self.repair_base is None:
            return
        repair = plan(self.ladder, self.issues, self.plc_model)
        if repair is None or repair["target"]["strategy"] != "deterministic":
            return
        patch = deterministic_response({"repair_mode": "field_patch", **repair})
        value = apply(self.ladder, patch, repair)
        self.local_field_repair = True
        self.on_stage("deterministic_field_repair", "已按唯一可核对的指令或字段写法修正候选")
        self.assess(value, allow_field_repair=False)

    def _partial(self, program):
        if "r" in program:
            return expand_compact_partial(program, self.spec, plc_model=self.plc_model, bindings=self.bindings)
        if candidate_base({"rungs": program.get("rungs"), "device_comments": program.get("device_comments", {})}) is None and program.get("rungs") != []:
            raise CompactProtocolError("partial candidate requires numbered rungs")
        return program

    def reject_program_shape(self, program, error, diagnostic):
        self.wire, self.error = program, error
        self._validation_failure(error)
        self.issues[0].update(diagnostic)

    def _validation_failure(self, error):
        if isinstance(error, CompactProtocolError):
            diagnostic = (error.binding_diagnostic if isinstance(error, CompactBindingError) else
                          {"category": "structure", "path": error.path, "reason": error.reason})
            self.issues = [diagnostic]
            match = re.match(r"content\.r\.(\d+)(?:\.|$)", error.path)
            rows = self.wire.get("r") if isinstance(self.wire, dict) else None
            if match and isinstance(rows, list) and int(match[1]) < len(rows):
                index = int(match[1])
                row = rows[index]
                if isinstance(row, dict) and "construct" not in row:
                    identity = row.get("rung_id") if self.wire.get("mode") == "partial" else index + 1
                    if type(identity) is int:
                        self.repair_base = {"compact": True, "index": index, "rungs": [{**row, "rung_id": identity}]}
                        self.scope = {identity}
            return
        diagnostic = validation_diagnostic(error)
        diagnostic.update(category="structure", actual=str(error)[:360])
        match = re.search(r"\.rungs\.(\d+)\.branches\.(\d+)\.outputs\.(\d+)", diagnostic["path"])
        if match and isinstance(self.ladder, dict):
            try:
                output = self.ladder["rungs"][int(match[1])]["branches"][int(match[2])]["outputs"][int(match[3])]
            except (IndexError, KeyError, TypeError):
                output = None
            if isinstance(output, dict) and output.get("type") == "APP_INSTR":
                opcode = output.get("opcode")
                if isinstance(opcode, str) and len(opcode) <= 64:
                    diagnostic.update(opcode=opcode, operands=output.get("operands"),
                                      expected=self.bindings.instruction_forms([opcode.strip().upper()]))
        self.issues = [diagnostic]
        if should_auto_repair_validation_error(error):
            self._scope_ladder(self.issues)

    def _check_positional_forms(self):
        from application.compact_bindings import positional_form_issues
        self.issues.extend(positional_form_issues(self.ladder, self.spec, self.bindings, previous=self.previous))
        if self.issues:
            self.error = CompactBindingError("r", self.issues[0]["reason"], opcode=self.issues[0]["opcode"])
            self._scope_ladder(self.issues)

    def _scope_ladder(self, issues):
        base = candidate_base(self.ladder)
        if base is None:
            return
        rows = base["rungs"]
        ids = set()
        for item in issues:
            scope = set(item.get("rung_ids") or ())
            match = re.search(r"\.rungs\.(\d+)(?:\.|$)", item.get("path", ""))
            if match and int(match[1]) < len(rows):
                scope.add(rows[int(match[1])]["rung_id"])
            if not scope:
                return
            ids.update(scope)
        if self.previous is not None:
            prior = {rung["rung_id"]: rung for rung in self.previous["rungs"]}
            if any(prior.get(rung["rung_id"]) == rung for rung in rows if rung["rung_id"] in ids):
                return
        selected = [rung for rung in rows if rung["rung_id"] in ids]
        if selected and len(selected) == len(ids):
            self.repair_base, self.scope = {"rungs": selected}, ids

    def _check_behavior(self):
        from simulator.io_example import check_independent_acceptance

        self.behavior = check_independent_acceptance(
            self.ladder, self.plc_model, project_id=self.project_id, confirmed_spec=self.spec,
            loaded_cases=self.cases, confirmed_check=self.prepared.get("behavior_check"))
        if self.behavior["status"] == "mismatch":
            indexes = self.behavior.get("network_indexes", [])
            rungs = self.ladder["rungs"]
            self.issues = [{
                "category": "behavior", "reason": self.behavior.get("reason", "confirmed_behavior_mismatch"),
                **{key: self.behavior[key] for key in ("case_id", "input_sequence", "scan_ms", "at_ms",
                                                     "scan", "address", "expected", "actual", "location", "findings")
                   if key in self.behavior},
                "rung_ids": [rungs[index]["rung_id"] for index in indexes if 0 <= index < len(rungs)],
            }]
            if len(self.behavior.get("counterexamples", ())) > 1:
                self.issues[0]["counterexamples"] = [
                    {key: witness[key] for key in ("at_ms", "address", "expected", "actual")}
                    for witness in self.behavior["counterexamples"]]
                self.issues[0]["case_input_sequence"] = self.behavior["case_input_sequence"]
        else:
            # A failed preference/coverage check does not, by itself, identify a
            # definite conflict in a specific rung.
            semantic = self.prepared.get("semantic_validation") or {}
            self.issues = [{"category": "semantic", **item,
                            "rung_ids": [item["rung_id"]]}
                           for item in semantic.get("violations", [])
                           if item.get("check") in {"structure_binding_predicate", "instruction_instance"}
                           and type(item.get("rung_id")) is int and "expected" in item]
        if self.issues:
            self._scope_ladder(self.issues)

    def _check_native(self):
        from application.gxw_delivery import delivery_progress, finalize_saved

        metadata = {"target_mode": "ladder", "plc_model": self.plc_model, "program_name": self.program_name,
                    "revision": self.revision, "artifacts": dict(self.rendered["artifacts"]),
                    "validation": {"status": "candidate_ready"}}
        with delivery_progress(lambda _kind, payload: self.on_stage(payload.get("stage", "native_check"),
                                                                    payload.get("message", ""))):
            finalize_saved(self.output_dir, metadata)
        metadata.setdefault("generation_metadata", {})["delivery_finalized"] = True
        self.delivery = metadata
        validation = metadata["validation"]
        if validation.get("gx_compile") == "failed":
            self.issues = [{"category": "native_compile", **row, "reason": row.get("code")}
                           for row in validation.get("native_compile_findings", []) if row.get("severity") == "error"]
            if not self.issues:
                self.issues = [{"category": "native_compile", "reason": "unlocalized_native_rejection"}]
            self._scope_ladder(self.issues)

    def patch(self, partial):
        if not self.scope:
            raise RepairAssemblyError("$.rungs", "repair_scope_violation", kind="no_local_scope")
        if not isinstance(partial, dict) or partial.get("mode") != "partial":
            raise RepairAssemblyError("$.mode", "repair_shape_invalid", expected="partial")
        for field in ("delete_rung_ids", "device_comments"):
            if partial.get(field):
                raise RepairAssemblyError("$." + field, "repair_scope_violation",
                                          kind="field_out_of_scope", expected="empty")
        patch = self._partial(partial)
        ids = [rung.get("rung_id") for rung in patch["rungs"]]
        if not ids:
            raise RepairAssemblyError("$.rungs", "repair_no_progress")
        outside = [identity for identity in ids if type(identity) is not int or identity not in self.scope]
        if outside:
            raise RepairAssemblyError("$.rungs", "repair_scope_violation", kind="rungs_out_of_scope",
                                      submitted_rung_ids=outside[:16], allowed_rung_ids=sorted(self.scope))
        from plc.device_identity import canonical_device, reference_device_tokens
        prior_devices = (patch_device_addresses(self.ladder) if self.ladder is not None else
                         reference_device_tokens(json.dumps(self.wire, ensure_ascii=False)))
        allowed = self.known_addresses | {canonical_device(address) for address in prior_devices}
        added = {canonical_device(address) for address in patch_device_addresses(patch)} - allowed
        if added:
            raise RepairAssemblyError("$.rungs", "repair_scope_violation", kind="new_device_addresses",
                                      submitted_addresses=sorted(added)[:16],
                                      allowed_addresses=sorted(allowed)[:64],
                                      address_scope="addresses in the candidate and confirmed requirements")
        if self.repair_base.get("compact"):
            rows = partial.get("r")
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise RepairAssemblyError("$.r", "repair_shape_invalid", expected="one compact replacement rung")
            replacement = copy.deepcopy(rows[0])
            if self.wire.get("mode") != "partial":
                replacement.pop("rung_id")
            program = copy.deepcopy(self.wire)
            program["r"][self.repair_base["index"]] = replacement
        else:
            program = materialize_partial(self.ladder, patch, replacement_only=True)
        if program == self.wire or program == self.ladder:
            raise RepairAssemblyError("$.rungs", "repair_no_progress")
        self.assess(program)

    def rejection_text(self):
        candidate = self.ladder if self.ladder is not None else self.wire
        return json.dumps(candidate, ensure_ascii=False) if candidate is not None else ""
