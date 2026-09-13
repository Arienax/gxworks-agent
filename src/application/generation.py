"""Synchronous generation orchestration shared by desktop and local services.

No GUI, workspace activation, GX automation or simulator operations belong here.
Model response acceptance remains inside api/collect_response before callbacks,
but ladder JSON syntax rejections may be retained privately for deterministic
cleanup or an explicit operator-confirmed repair.
"""
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Optional
import copy

import api
from application.base import model_call
from application.jobs import JobCancelled
from application.generation_repair import (
    GenerationError, GenerationValidationError, candidate_base, validation_diagnostic,
)
from i18n import get_language, language_context, tr
from config_manager import get_active_model_name, load_full_config
from plc_generation import prepare_ladder_candidate, render_generation_artifacts
from plc_json_validator import (
    PLCJsonValidationError, validate_st_json,
)
from plc_ir import (
    IR_SCHEMA_VERSION, PLCIRValidationError, canonical_sha256, is_plc_ir,
)


@dataclass(frozen=True)
class GenerationRequest:
    """Caller-owned inputs are copied before a job crosses a thread boundary."""
    user_input: str
    effort: Optional[str] = None
    target_mode: str = "ladder"
    previous_json: object = None
    conversation_history: object = None
    confirmed_context: object = None
    task_type: Optional[str] = None
    current_version_json: object = None
    previous_ir: object = None
    plc_model: str = "FX3U"
    program_name: str = "MAIN"
    revision: int = 1
    requirement_text: str = ""
    repair_mode: bool = False
    format_repair: bool = False
    allowed_rung_ids: object = None
    allowed_addresses: object = None
    image_attachments: object = None
    model_name: Optional[str] = None
    response_language: Optional[str] = None

    def __post_init__(self):
        for item in fields(self):
            object.__setattr__(self, item.name, copy.deepcopy(getattr(self, item.name)))
        object.__setattr__(self, "response_language", self.response_language or get_language())


@dataclass(frozen=True)
class GenerationDependencies:
    """Inject model calls or a provider snapshot; defaults use the accepted API."""
    stream_response: Optional[Callable] = None
    generate_json: Optional[Callable] = None
    repair_response: Optional[Callable] = None
    provider: object = None
    check_cancelled: Optional[Callable] = None
    preserve_rejected_candidate: bool = False



class GenerationWorkflow:
    def __init__(self, request, output_dir, on_event=None, dependencies=None):
        self.request = copy.deepcopy(request)
        for item in fields(self.request):
            setattr(self, item.name, copy.deepcopy(getattr(self.request, item.name)))
        self.output_dir = Path(output_dir)
        self.on_event = on_event
        self.dependencies = dependencies or GenerationDependencies()
        self.conversation_history = self.conversation_history or []
        self.task_type = self.task_type or ("edit" if self.previous_json is not None else "generate")
        # The local merge base must also be visible to the model. Previously an
        # edit request set ``is_edit_mode`` but omitted the current ladder from
        # model context, so the model often regenerated the program from spec.
        if self.current_version_json is None and self.previous_json is not None:
            self.current_version_json = copy.deepcopy(self.previous_json)
        self.previous_ir = self.previous_ir if is_plc_ir(self.previous_ir) else None
        self.plc_model = str(self.plc_model or "FX3U").upper()
        self.program_name = str(self.program_name or "MAIN").strip() or "MAIN"
        self.requirement_text = str(self.requirement_text or self.user_input or "")
        try:
            self.revision = max(0, int(self.revision))
        except (TypeError, ValueError):
            self.revision = 1
        self.repair_mode = bool(self.repair_mode)
        self.format_repair = bool(self.format_repair)
        if self.repair_mode and self.format_repair:
            raise ValueError("repair_mode and format_repair are mutually exclusive")
        self.allowed_rung_ids = {int(item) for item in (self.allowed_rung_ids or [])}
        self.allowed_addresses = {
            str(item).strip().upper()
            for item in (self.allowed_addresses or [])
            if str(item).strip()
        }
        self.image_attachments = tuple(self.image_attachments or ())
        if self.model_name is None:
            try:
                self.model_name = get_active_model_name(load_full_config())
            except Exception:
                self.model_name = None

    def _emit(self, event_type, payload):
        if self.dependencies.check_cancelled:
            self.dependencies.check_cancelled()
        if self.on_event:
            if not isinstance(payload, dict):
                payload = {"text": str(payload)}
            self.on_event(event_type, copy.deepcopy(payload))

    def run(self):
        """Build artifacts synchronously, returning their manifest or raising.

        The callback receives progress/reasoning/content events. Terminal state
        and event sequencing belong to the caller's task manager.
        """
        with language_context(self.response_language), api.provider_scope(
            self.dependencies.provider, model_name=self.model_name
        ):
            return self._run()

    def _run(self):
        """Generate one candidate and perform only transport/shape acceptance.

        Once the user has confirmed the specification, this workflow does not
        reinterpret that intent with approach heuristics, regex-derived semantic
        requirements, or hidden model repair loops. Strong semantic/static
        checks remain available to Review, simulator and GX execution paths.

        A completed ladder response rejected *only* for invalid JSON syntax is
        treated differently from transport/language/schema-field rejection: its
        raw candidate is retained privately, because it can either be repaired
        deterministically (for a redundant closing delimiter) or exposed through
        the existing explicit repair flow without paying for the whole generation
        again.
        """
        try:
            import json

            def emit_parsing_progress(message):
                self._emit("progress", {"stage": "parsing", "message": str(message)})

            def clean_json_text(value):
                text = str(value or "").strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1] if "\n" in text else ""
                if text.endswith("```"):
                    text = text.rsplit("\n", 1)[0]
                return text.strip()

            def rejected_json_candidate(error):
                """Return the private raw ladder candidate for JSON-only rejection.

                The collector deliberately withholds rejected bytes from normal
                callbacks/publication. Generation may still stage those same bytes
                privately so the operator can repair them explicitly. No other
                response-acceptance failure is downgraded here.
                """
                from model_provider import ResponseRejectedError

                if not isinstance(error, ResponseRejectedError):
                    return None
                if [(item.path, item.reason) for item in error.violations] != [
                    ("content", "invalid_json_object")
                ]:
                    return None
                raw_response = getattr(error, "raw_response", None)
                message = getattr(raw_response, "message", None)
                content = getattr(message, "content", None)
                if not isinstance(content, str) or not content.strip():
                    return None
                return clean_json_text(content)

            def trim_redundant_json_tail(candidate):
                """Remove only a tiny non-semantic tail after a complete object.

                This is intentionally narrower than a generic JSON fixer. A
                complete top-level object may be followed by a few punctuation
                characters emitted by the model (for example `}`, `]`, `,`, `;`,
                `.`, a backtick, or CJK punctuation). We never discard letters,
                digits, quotes, or a second object/array because those may carry
                semantic content that must remain an explicit repair candidate.
                """
                try:
                    json.loads(candidate)
                    return candidate, False
                except json.JSONDecodeError as error:
                    if error.msg != "Extra data":
                        return candidate, False

                stripped = candidate.lstrip()
                try:
                    value, end = json.JSONDecoder().raw_decode(stripped)
                except json.JSONDecodeError:
                    return candidate, False
                suffix = stripped[end:].strip()
                if (
                    not isinstance(value, dict)
                    or not suffix
                    or len(suffix) > 8
                    or any(char.isalnum() or char in "{[\"'" for char in suffix)
                ):
                    return candidate, False
                return stripped[:end], True

            self.output_dir.mkdir(parents=True, exist_ok=True)
            validation_messages = []
            repair_attempts = 0

            # ---------- Phase 1: one model generation ----------
            full_content = ""
            streaming_succeeded = False
            is_edit_mode = self.target_mode == "ladder" and self.previous_json is not None
            from plc_generation_context import generation_user_input
            model_user_input = generation_user_input(
                self.user_input, is_edit_mode=is_edit_mode,
                target_mode=self.target_mode, repair_mode=(self.repair_mode or self.format_repair),
            )
            repair_call = self.target_mode == "ladder" and (self.repair_mode or self.format_repair)
            repair_payload = None
            if repair_call:
                if self.repair_mode:
                    baseline = self.previous_json if isinstance(self.previous_json, dict) else {}
                    selected_rungs = [copy.deepcopy(rung) for rung in baseline.get("rungs", [])
                                      if isinstance(rung, dict) and rung.get("rung_id") in self.allowed_rung_ids]
                    comments = baseline.get("device_comments", {}) if isinstance(baseline.get("device_comments"), dict) else {}
                    repair_payload = {
                        "repair_mode": "partial",
                        "plc_model": self.plc_model,
                        "instruction": model_user_input,
                        "allowed_rung_ids": sorted(self.allowed_rung_ids),
                        "allowed_addresses": sorted(self.allowed_addresses),
                        "baseline_subset": {
                            "device_comments": {key: value for key, value in comments.items()
                                                if str(key).strip().upper() in self.allowed_addresses},
                            "rungs": selected_rungs,
                        },
                    }
                else:
                    repair_payload = {
                        "repair_mode": "format",
                        "plc_model": self.plc_model,
                        "instruction": model_user_input,
                    }
            try:
                stream_model_response = self.dependencies.stream_response or api.stream_model_response

                def on_reasoning(token):
                    self._emit("reasoning", token)

                def on_content(token):
                    self._emit("content", token)

                self._emit("progress", {"stage": "connecting", "message": tr('正在连接模型')})
                if repair_call:
                    if self.dependencies.repair_response is not None:
                        _reasoning, full_content = model_call(
                            self.dependencies.repair_response, repair_payload, self.model_name, self.effort,
                            mode="partial" if self.repair_mode else "format",
                            on_reasoning_chunk=on_reasoning, on_content_chunk=on_content,
                        )
                    elif self.dependencies.provider is None and self.dependencies.stream_response is not None:
                        # Keep injectable/offline tests compatible without routing production repair
                        # back through the normal generation prompt builder.
                        _reasoning, full_content = model_call(
                            self.dependencies.stream_response,
                            json.dumps(repair_payload, ensure_ascii=False),
                            self.model_name, self.effort, "ladder",
                            on_reasoning_chunk=on_reasoning, on_content_chunk=on_content,
                            is_edit_mode=True, conversation_history=[], confirmed_context=None,
                            persist_history=False, task_type="contract_repair",
                            current_version_json=None, plc_model=self.plc_model, image_attachments=(),
                        )
                    else:
                        _reasoning, full_content = model_call(
                            api.repair_ladder_response, repair_payload, self.model_name, self.effort,
                            mode="partial" if self.repair_mode else "format",
                            on_reasoning_chunk=on_reasoning, on_content_chunk=on_content,
                        )
                else:
                    _reasoning, full_content = model_call(
                        stream_model_response,
                        model_user_input,
                        self.model_name,
                        self.effort,
                        self.target_mode,
                        on_reasoning_chunk=on_reasoning,
                        on_content_chunk=on_content,
                        is_edit_mode=is_edit_mode,
                        conversation_history=self.conversation_history,
                        confirmed_context=self.confirmed_context,
                        persist_history=False,
                        task_type=self.task_type,
                        current_version_json=self.current_version_json,
                        plc_model=self.plc_model,
                        image_attachments=self.image_attachments,
                    )
                emit_parsing_progress(tr('正在解析模型输出：清理流式文本'))
                streaming_succeeded = True
            except Exception as stream_err:
                from model_provider import ResponseRejectedError

                if isinstance(stream_err, JobCancelled):
                    raise
                rejected_candidate = (
                    rejected_json_candidate(stream_err)
                    if self.target_mode == "ladder"
                    else None
                )
                if rejected_candidate is not None:
                    # The provider completed successfully; only the JSON syntax
                    # acceptance gate failed. Keep the bytes private and let the
                    # generation parser decide whether a safe local cleanup is
                    # possible. Otherwise it becomes an explicit repairable job.
                    full_content = rejected_candidate
                    streaming_succeeded = True
                    self._emit("progress", {
                        "stage": "format_recovery",
                        "severity": "warning",
                        "message": tr('模型回复 JSON 格式验收失败；正在检查是否可安全恢复，否则保留候选供显式修复。'),
                    })
                elif isinstance(stream_err, ResponseRejectedError):
                    raise
                elif repair_call:
                    # Explicit repair has its own transport fallback inside the dedicated API.
                    # Never fall back into a normal full generation request.
                    raise
                else:
                    self._emit("progress", {
                        "stage": "fallback",
                        "severity": "warning",
                        "message": tr('流式调用失败，切换普通模式：{v0}', v0=stream_err),
                    })
                    print(tr('流式调用失败，降级至普通模式: {v0}', v0=stream_err))

            if repair_call and (not streaming_succeeded or not full_content):
                raise GenerationError(tr('修复调用未返回候选 JSON'))

            # Transport fallback is not a semantic repair. It obtains the same
            # requested candidate once when streaming itself failed.
            if streaming_succeeded and full_content:
                json_str = clean_json_text(full_content)
            else:
                try:
                    json_str = model_call(
                        self.dependencies.generate_json or api.generate_model_json,
                        model_user_input,
                        self.model_name,
                        self.effort,
                        self.target_mode,
                        is_edit_mode=is_edit_mode,
                        conversation_history=self.conversation_history,
                        confirmed_context=self.confirmed_context,
                        persist_history=False,
                        task_type=self.task_type,
                        current_version_json=self.current_version_json,
                        plc_model=self.plc_model,
                        image_attachments=self.image_attachments,
                    )
                except Exception as fallback_err:
                    if isinstance(fallback_err, JobCancelled):
                        raise
                    rejected_candidate = (
                        rejected_json_candidate(fallback_err)
                        if self.target_mode == "ladder"
                        else None
                    )
                    if rejected_candidate is None:
                        raise
                    json_str = rejected_candidate
                    self._emit("progress", {
                        "stage": "format_recovery",
                        "severity": "warning",
                        "message": tr('普通模式回复 JSON 格式验收失败；正在检查是否可安全恢复，否则保留候选供显式修复。'),
                    })

            if not json_str:
                raise GenerationError(tr('大模型未返回合法数据'))

            # Do not pay for another model call when the response is already one
            # complete object plus a redundant terminal bracket/brace. Anything
            # less obvious remains a failure and is offered to explicit repair.
            json_str, local_tail_repair = trim_redundant_json_tail(json_str)
            if local_tail_repair:
                validation_messages.append(tr('已移除模型 JSON 末尾多余的闭合符号'))
                self._emit("progress", {
                    "stage": "format_recovered",
                    "message": tr('已安全移除 JSON 末尾多余闭合符号；继续解析候选程序。'),
                })

            prepared_candidate = None

            def parse_candidate(candidate):
                nonlocal prepared_candidate
                emit_parsing_progress(tr('正在解析模型输出：读取 JSON 结构'))
                parsed = json.loads(candidate)
                if not isinstance(parsed, dict):
                    raise PLCJsonValidationError("$: expected JSON object")
                if self.target_mode == "ladder":
                    prepared_candidate = prepare_ladder_candidate(
                        parsed, plc_model=self.plc_model, program_name=self.program_name,
                        revision=self.revision, confirmed_spec=self.confirmed_context,
                        previous_ladder=self.previous_json, repair_mode=self.repair_mode,
                        allowed_rung_ids=self.allowed_rung_ids,
                        allowed_addresses=self.allowed_addresses, task_type=self.task_type,
                        on_progress=emit_parsing_progress,
                    )
                    validation_messages.extend(prepared_candidate["validation_messages"])
                    return prepared_candidate["ladder"]
                emit_parsing_progress(tr('正在解析模型输出：校验 ST 结构'))
                validate_st_json(parsed)
                return parsed

            def persist_repair_candidate():
                # Private staging only: only the operator Workbench opts into this.
                # Low-level workflows and explicit repair tools retain zero-artifact failure semantics.
                if not self.dependencies.preserve_rejected_candidate:
                    return
                if self.target_mode != "ladder" or not isinstance(json_str, str):
                    return
                if not json_str.strip() or len(json_str) > 512000:
                    return
                (self.output_dir / "repair_candidate.json").write_text(json_str, encoding="utf-8")

            validation_errors = (PLCJsonValidationError, PLCIRValidationError, json.JSONDecodeError)

            def cascade_format_repair(error):
                nonlocal prepared_candidate, repair_attempts
                if not self.format_repair or not isinstance(error, (PLCJsonValidationError, PLCIRValidationError)):
                    return None
                try:
                    base = candidate_base(json.loads(json_str))
                except json.JSONDecodeError:
                    return None
                if base is None:
                    return None
                import re
                diagnostic = validation_diagnostic(error)
                path = str(diagnostic.get("path") or "")
                match = re.search(r"(?:^|\.)rungs\.(\d+)(?:\.|$)", path)
                if not match:
                    return None
                index = int(match.group(1))
                if index >= len(base["rungs"]):
                    return None
                rung = base["rungs"][index]
                rung_id = rung.get("rung_id") if isinstance(rung, dict) else None
                if isinstance(rung_id, bool) or not isinstance(rung_id, int):
                    return None
                from contract_repair import patch_device_addresses
                addresses = sorted(patch_device_addresses({"mode":"partial","device_comments":{},"rungs":[rung],"delete_rung_ids":[]}))
                payload = {"repair_mode":"partial","plc_model":self.plc_model,
                    "instruction":f"JSON 格式已经恢复，但结构校验仍发现局部协议错误。只修复该梯级的协议/结构表示，不改变控制语义、地址、参数或触点极性。错误位置：{path}；原因：{diagnostic.get('reason','invalid_ladder_structure')}",
                    "allowed_rung_ids":[rung_id],"allowed_addresses":addresses,
                    "baseline_subset":{"device_comments":{},"rungs":[copy.deepcopy(rung)]}}
                self._emit("progress", {"stage":"structural_repair","severity":"warning",
                    "message":tr('JSON 格式已恢复；正在对新暴露的局部结构错误执行一次有界修复。')})
                def on_reasoning(token): self._emit("reasoning", token)
                def on_content(token): self._emit("content", token)
                if self.dependencies.repair_response is not None:
                    _r, local_text = model_call(self.dependencies.repair_response, payload, self.model_name, self.effort, mode="partial", on_reasoning_chunk=on_reasoning, on_content_chunk=on_content)
                else:
                    _r, local_text = model_call(api.repair_ladder_response, payload, self.model_name, self.effort, mode="partial", on_reasoning_chunk=on_reasoning, on_content_chunk=on_content)
                partial = json.loads(clean_json_text(local_text))
                prepared_candidate = prepare_ladder_candidate(partial, plc_model=self.plc_model, program_name=self.program_name, revision=self.revision,
                    confirmed_spec=self.confirmed_context, previous_ladder=base, repair_mode=True, allowed_rung_ids={rung_id},
                    allowed_addresses=set(addresses), task_type="contract_repair", on_progress=emit_parsing_progress)
                validation_messages.extend(prepared_candidate["validation_messages"])
                repair_attempts += 1
                return prepared_candidate["ladder"]

            try:
                parsed_json = parse_candidate(json_str)
            except validation_errors as error:
                try:
                    parsed_json = cascade_format_repair(error)
                except validation_errors as followup_error:
                    persist_repair_candidate()
                    raise GenerationValidationError([followup_error], attempts=1, max_attempts=1, language=self.response_language, stop_reason="explicit_repair_followup_failed") from followup_error
                if parsed_json is None:
                    persist_repair_candidate()
                    raise GenerationValidationError([error], attempts=0, max_attempts=0, language=self.response_language, stop_reason="final_validation") from error

            if self.target_mode == "ladder":
                program_ir = prepared_candidate["program_ir"]
                self._emit("progress", {
                    "stage": "parsed",
                    "message": tr('模型输出已解析为候选程序'),
                })
                rendered = render_generation_artifacts(program_ir, self.output_dir)
                artifacts = rendered["artifacts"]
                from plc_st_renderer import ST_RENDERER_SCHEMA_VERSION

                from plc_semantics import SEMANTICS_SCHEMA_VERSION
                from plc_static_analyzer import STATIC_ANALYSIS_SCHEMA_VERSION
                from plc_timing import TIMING_ANALYSIS_SCHEMA_VERSION
                return {
                    "target_mode": "ladder",
                    "repair_attempts": repair_attempts,
                    "validation_profile": "generation_structural",
                    "program_name": self.program_name,
                    "revision": self.revision,
                    "ir_schema_version": IR_SCHEMA_VERSION,
                    "ir_sha256": canonical_sha256(program_ir),
                    "ladder_sha256": program_ir["source"]["ladder_sha256"],
                    "st_from_ir_sha256": rendered["st_from_ir_sha256"],
                    "st_renderer_schema_version": ST_RENDERER_SCHEMA_VERSION,
                    "semantic_schema_version": SEMANTICS_SCHEMA_VERSION,
                    "semantic_summary": {
                        "requirements": program_ir["logic"].get("requirements", []),
                        "coverage": program_ir["timing"].get("coverage", []),
                        "state_machine_count": len(program_ir["logic"].get("state_machines", [])),
                        "regions": [
                            {"code": region.get("code"), "kind": region.get("kind"),
                             "network_count": len(region.get("network_refs", []))}
                            for region in program_ir["logic"].get("regions", [])
                        ],
                    },
                    "static_analysis_schema_version": STATIC_ANALYSIS_SCHEMA_VERSION,
                    "static_analysis_summary": {
                        "counts": program_ir["analysis"].get("counts", {}),
                        "rules_checked": program_ir["analysis"].get("rules_checked", []),
                        "dependency_nodes": len(program_ir["analysis"].get("dependency_graph", {}).get("nodes", [])),
                        "dependency_edges": len(program_ir["analysis"].get("dependency_graph", {}).get("device_edges", [])),
                    },
                    "timing_analysis_schema_version": TIMING_ANALYSIS_SCHEMA_VERSION,
                    "timing_summary": {
                        "profile": program_ir["timing"].get("performance", {}).get("profile"),
                        "estimate": program_ir["timing"].get("performance", {}).get("estimate", {}),
                        "scan_budget": program_ir["timing"].get("performance", {}).get("scan_budget", {}),
                        "scan_monitor": program_ir["timing"].get("performance", {}).get("scan_monitor", {}),
                    },
                    "width": rendered["width"],
                    "height": rendered["height"],
                    "normalization": prepared_candidate["normalization"],
                    "artifacts": artifacts,
                    "contract_mismatch": None,
                    "validation": {
                        "status": "candidate_ready",
                        "profile": "generation_structural",
                        "messages": validation_messages or [
                            tr('候选结构可解析；需求一致性不在生成阶段重复判定')
                        ],
                    },
                }

            st_text = parsed_json.get("st_code", "")
            if not st_text:
                st_text = json_str
            self._emit("progress", {"stage": "parsed", "message": tr('模型输出已解析为 ST 候选')})
            output_path = self.output_dir / "program.st"
            output_path.write_text(st_text.strip(), encoding="utf-8")
            return {
                "target_mode": "st",
                "validation_profile": "generation_structural",
                "width": 0,
                "height": 0,
                "artifacts": {"st": output_path.name},
                "validation": {
                    "status": "candidate_ready",
                    "profile": "generation_structural",
                    "messages": [tr('ST 输出结构可解析')],
                },
            }

        except (GenerationError, JobCancelled):
            raise
        except (PLCJsonValidationError, PLCIRValidationError) as error:
            try:
                persist_repair_candidate()
            except (NameError, OSError):
                pass
            raise GenerationValidationError(
                [error], attempts=0, max_attempts=0, language=self.response_language,
                stop_reason="final_validation",
            ) from error
        except Exception as error:
            raise GenerationError(tr('线程运行期异常: {v0}', v0=str(error))) from error
