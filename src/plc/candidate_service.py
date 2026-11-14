# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Single deterministic candidate preparation/compilation service.

Web generation and PLCCore/MCP enter here. Persistence, approval, provider calls,
GX synchronization and test execution remain outside this service.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


class CandidateService:
    def prepare(
        self, candidate, *, plc_model="FX3U", program_name="MAIN", revision=1,
        confirmed_spec=None, previous_ladder=None, repair_mode=False,
        allowed_rung_ids=None, allowed_addresses=None, task_type=None,
        candidate_origin="external", on_progress=None, behavior_traces=None,
    ):
        from plc.generation import prepare_ladder_candidate
        result = prepare_ladder_candidate(
            candidate, plc_model=plc_model, program_name=program_name, revision=revision,
            confirmed_spec=confirmed_spec, previous_ladder=previous_ladder,
            repair_mode=repair_mode, allowed_rung_ids=allowed_rung_ids,
            allowed_addresses=allowed_addresses, task_type=task_type,
            candidate_origin=candidate_origin, on_progress=on_progress, behavior_traces=behavior_traces,
        )
        from plc.maintainability import review_maintainability
        result["maintainability_review"] = review_maintainability(
            result["program_ir"], confirmed_spec=confirmed_spec)
        return result

    def compile(self, program, output_dir, *, validation_profile="generation_structural",
                include_st_translation=False):
        target = Path(output_dir)
        if validation_profile == "generation_structural":
            from plc.generation import render_generation_artifacts
            rendered = render_generation_artifacts(
                program, target, include_st_translation=include_st_translation)
        else:
            from plc.artifacts import render_candidate_artifacts
            program_size = {}
            rendered = {"artifacts": render_candidate_artifacts(
                program, target, include_st_translation=include_st_translation, program_size=program_size)}
            if program_size:
                rendered["program_size"] = program_size
        if not rendered['artifacts'].get('st_from_ir'):
            rendered.setdefault('st_translation', 'unavailable' if include_st_translation else 'disabled')
        return {**rendered, "hashes": {
            name: hashlib.sha256((target / filename).read_bytes()).hexdigest()
            for name, filename in rendered["artifacts"].items()
        }}
