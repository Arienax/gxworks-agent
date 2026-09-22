"""Bounded diagnostic metadata plus operator-only detailed trace sidecars.

Structured metadata remains small and sanitized. Detailed job inputs, model I/O
and operator actions are stored separately under private application state with
credential/binary redaction. Logging never changes workflow behavior.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections import deque
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import zipfile
import shared.tracing as runtime_trace

_SCHEMA = 1
_MAX_FILE = 512 * 1024
_MAX_EXPORT_LINES = 512
_ROOT = Path(__file__).resolve().parents[1]
_active = ContextVar('runtime_diagnostics', default=None)
_attempt = ContextVar('diagnostic_attempt', default=0)
_ALLOWED_EVENTS = {'job_started', 'job_finished', 'model_request', 'provider_request', 'provider_result', 'attempt_finished',
                   'model_response', 'response_rejected', 'model_accepted', 'context_audit',
                   'provider_exception', 'workflow_exception', 'logging_limit', 'retrieval_failed'}
_NUMBERS = {'request_index', 'attempt_index', 'message_count', 'message_chars', 'tool_count',
            'content_chars', 'reasoning_chars', 'chunk_count', 'choice_count', 'input_tokens',
            'output_tokens', 'total_tokens', 'reasoning_tokens', 'max_tokens',
            'max_completion_tokens', 'elapsed_ms', 'status_code', 'line', 'column',
            'position', 'original_line', 'original_column', 'original_position',
            'exception_count', 'event_count', 'system_messages', 'user_messages',
            'assistant_messages', 'tool_messages', 'system_chars', 'user_chars',
            'assistant_chars', 'tool_chars', 'image_count', 'raw_chars',
            'distance_from_end', 'decoded_prefix_chars', 'suffix_chars', 'key_count',
            'rung_count', 'device_comment_count', 'violation_count', 'attempt_count',
            'max_attempts', 'context_request_index', 'context_section_count',
            'included_section_count', 'excluded_section_count', 'included_section_chars',
            'excluded_section_chars', 'retrieval_section_count', 'retrieval_context_chars',
            'dropped_sections'}
_BOOLEANS = {'stream', 'refusal_present', 'finish_seen', 'at_or_near_end', 'fenced',
             'bom', 'traceback_truncated', 'content_present', 'prefix_complete_object',
             'punctuation_only_tail', 'allowed_by_registry'}
_IDS = {'model', 'provider', 'contract', 'error_type', 'code', 'function',
        'stop_reason', 'tail_class', 'dependency'}
_ENUMS = {
    'stage': {'workflow', 'model_request', 'provider_transport', 'response_acceptance', 'publication'},
    'status': {'completed', 'failed', 'cancelled', 'interrupted', 'running'},
    'kind': {'analysis', 'generation', 'agent', 'review', 'test_plan', 'debug_plan',
             'execution', 'gx_read', 'gx_inspect'},
    'policy': {'legacy', 'minimal', 'manual', 'examples', 'combined', 'adaptive'},
    'format': {'text', 'json'},
    'response_format': {'text', 'json_object', 'json_schema', 'unspecified'},
    'content_type': {'str', 'list', 'dict', 'NoneType', 'int', 'bool'},
    'reasoning_type': {'str', 'list', 'dict', 'NoneType', 'int', 'bool'},
    'finish_reason': {'stop', 'length', 'content_filter', 'tool_calls', 'function_call',
                      'insufficient_system_resource', 'end_turn', 'max_tokens'},
    'root_type': {'dict', 'list', 'str', 'int', 'float', 'bool', 'NoneType'},
    'envelope': {'empty', 'object', 'array', 'fenced', 'bom', 'markup', 'other_text'},
    'json_status': {'valid_object', 'non_object', 'syntax_error', 'too_deep', 'not_checked', 'empty'},
    'json_error': {'Illegal trailing comma before end of object', 'Illegal trailing comma before end of array',
                   'Expecting value', "Expecting ',' delimiter", "Expecting ':' delimiter",
                   'Expecting property name enclosed in double quotes', 'Extra data',
                   'Unterminated string starting at', 'Invalid control character at',
                   'Invalid \\escape', 'Invalid \\uXXXX escape', 'Unexpected UTF-8 BOM (decode using utf-8-sig)'},
    'reason': {'invalid_json_object', 'invalid_prose_field', 'invalid_code_field',
               'unsupported_script', 'non_english_script', 'japanese_script',
               'latin_prose', 'ambiguous_han_only', 'invalid_shared_input',
               'invalid_ladder_structure', 'field_too_long', 'repair_base_invalid',
               'repair_identity_invalid', 'repair_shape_invalid', 'repair_scope_violation',
               'repair_no_progress'},
}


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_./:\-]{1,100}', value):
        return 'redacted'
    if any(word in value.lower() for word in ('sk-', 'bearer', 'secret', 'api_key', 'token=')):
        return 'redacted'
    return value


def _number(value):
    return max(0, min(value, 10**12)) if type(value) is int else None


def _safe_opcode(value):
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_.$@+\-]{1,64}", token):
        return "redacted"
    lowered = token.lower()
    if any(marker in lowered for marker in ("sk-", "bearer", "secret", "private", "api_key", "token", "password")):
        return "redacted"
    return token


def _safe_fields(fields):
    result = {}
    for key, value in fields.items():
        if key in _NUMBERS:
            result[key] = _number(value)
        elif key in _BOOLEANS and type(value) is bool:
            result[key] = value
        elif key in ('baseline_opcode', 'observed_opcode'):
            safe_opcode = _safe_opcode(value)
            if safe_opcode is not None:
                result[key] = safe_opcode
        elif key in _IDS:
            result[key] = _identifier(value)
        elif key in _ENUMS:
            result[key] = value if isinstance(value, str) and value in _ENUMS[key] else 'unknown'
        elif key in ('job_id', 'project_id', 'version_id'):
            if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value):
                result[key] = value
        elif key == 'content_sha256' and isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value):
            result[key] = value
        elif key == 'path' and isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_$.[\]-]{1,180}', value):
            result[key] = value
        elif key == 'json' and isinstance(value, dict):
            result[key] = _safe_fields(value)
        elif key in ('exceptions', 'frames', 'violations') and isinstance(value, (list, tuple)):
            result[key] = [_safe_fields(item) for item in value[:32] if isinstance(item, dict)]
        elif key == 'file' and isinstance(value, str):
            if re.fullmatch(r'(src|external)/[A-Za-z0-9_./-]{1,180}', value) and '..' not in value:
                result[key] = value
        elif key == 'timestamp' and isinstance(value, str) and re.fullmatch(r'[0-9T:.+Z-]{10,40}', value):
            result[key] = value
    return result


def json_diagnostic(content):
    """Inspect JSON shape and coordinates without recording any source text."""
    text = content if isinstance(content, str) else ''
    raw = text.strip()
    offset = len(text) - len(text.lstrip())
    fenced = raw.startswith('```') and raw.endswith('```') and '\n' in raw
    envelope = ('empty' if not raw else 'bom' if raw.startswith('\ufeff') else
                'fenced' if raw.startswith('```') else 'object' if raw.startswith('{') else
                'array' if raw.startswith('[') else 'markup' if raw.startswith('<') else 'other_text')
    if fenced:
        inner = raw.split('\n', 1)[1].rsplit('```', 1)[0]
        offset += raw.index('\n') + 1 + len(inner) - len(inner.lstrip())
        raw = inner.strip()
    result = {'envelope': envelope, 'fenced': fenced, 'bom': raw.startswith('\ufeff'),
              'raw_chars': len(raw)}

    def add_object_shape(payload):
        if not isinstance(payload, dict):
            return
        result['key_count'] = len(payload)
        if isinstance(payload.get('rungs'), list):
            result['rung_count'] = len(payload['rungs'])
        if isinstance(payload.get('device_comments'), dict):
            result['device_comment_count'] = len(payload['device_comments'])

    try:
        payload = json.loads(raw)
        result.update(json_status='valid_object' if isinstance(payload, dict) else 'non_object',
                      root_type=type(payload).__name__)
        add_object_shape(payload)
    except json.JSONDecodeError as exc:
        original = min(len(text), offset + exc.pos)
        result.update(json_status='empty' if not raw else 'syntax_error', json_error=exc.msg,
                      line=exc.lineno, column=exc.colno, position=exc.pos,
                      original_position=original, original_line=text.count('\n', 0, original) + 1,
                      original_column=original - text.rfind('\n', 0, original),
                      distance_from_end=max(0, len(raw) - exc.pos),
                      at_or_near_end=exc.pos >= max(0, len(raw) - 8))
        if exc.msg == 'Extra data':
            try:
                prefix, end = json.JSONDecoder().raw_decode(raw)
            except (json.JSONDecodeError, RecursionError, ValueError):
                prefix, end = None, 0
            if isinstance(prefix, dict):
                suffix = raw[end:].strip()
                if suffix.startswith(('{', '[')):
                    tail_class = 'second_json'
                elif any(char.isalnum() or char in "\"'" for char in suffix):
                    tail_class = 'semantic'
                else:
                    tail_class = 'punctuation'
                result.update(prefix_complete_object=True, decoded_prefix_chars=end,
                              suffix_chars=len(suffix), tail_class=tail_class,
                              punctuation_only_tail=tail_class == 'punctuation')
                add_object_shape(prefix)
    except (RecursionError, ValueError):
        result.update(json_status='too_deep')
    return _safe_fields(result)


def _directory(state_dir):
    state = Path(state_dir).resolve()
    target = state / 'diagnostics'
    if target.is_symlink() or (target.exists() and target.resolve().parent != state):
        raise ValueError('Unsafe diagnostic directory')
    return target


def _path(state_dir, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r'job_[a-zA-Z0-9_-]{1,120}', job_id):
        raise ValueError('Invalid diagnostic job identifier')
    directory = _directory(state_dir)
    path = directory / (job_id + '.jsonl')
    if path.is_symlink() or (path.exists() and path.resolve().parent != directory.resolve()):
        raise ValueError('Unsafe diagnostic file')
    return path


class DiagnosticSession:
    def __init__(self, state_dir, job_id):
        self.state_dir, self.job_id = Path(state_dir), job_id
        self.request_index = 0
        self.started = time.monotonic()
        self.failed = False
        self.limited = False
        self.lock = threading.Lock()
        self._write_failed = False

    def write(self, event, **fields):
        if event not in _ALLOWED_EVENTS:
            return
        try:
            with self.lock:
                path = _path(self.state_dir, self.job_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                if os.name != 'nt':
                    path.parent.chmod(0o700)
                size = path.stat().st_size if path.exists() else 0
                if size >= _MAX_FILE - 48 * 1024 and event not in {'workflow_exception', 'job_finished'}:
                    self.limited = True
                    return
                if size >= _MAX_FILE:
                    return
                record = {'schema_version': _SCHEMA, 'event': event,
                          'timestamp': datetime.now(timezone.utc).isoformat(),
                          'job_id': self.job_id, 'request_index': self.request_index,
                          'attempt_index': _attempt.get(), **_safe_fields(fields)}
                data = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
                if size + len(data) > _MAX_FILE:
                    return
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
                descriptor = os.open(str(path), flags, 0o600)
                with os.fdopen(descriptor, 'ab') as stream:
                    stream.write(data)
        except Exception:
            if not self._write_failed:
                self._write_failed = True
                print('Diagnostic log unavailable; original job outcome is unchanged.', file=sys.stderr)


@contextmanager
def diagnostic_scope(state_dir, job_id, **metadata):
    session = DiagnosticSession(state_dir, job_id)
    token, attempt_token = _active.set(session), _attempt.set(0)
    try:
        emit('job_started', stage='workflow', **metadata)
        yield session
    finally:
        _attempt.reset(attempt_token)
        _active.reset(token)


def emit(event, **fields):
    session = _active.get()
    if session is not None:
        session.write(event, **fields)


def record_operator_action(state_dir, action, **fields):
    try:
        return runtime_trace.record_operator_action(state_dir, action, **fields)
    except Exception:
        return False


def begin_request(request, provider):
    session = _active.get()
    if session is None:
        return
    session.request_index += 1
    _attempt.set(0)
    try:
        runtime_trace.record_model_request(
            session.state_dir, session.job_id, session.request_index, _attempt.get(), request, provider
        )
    except Exception:
        pass
    stats = {'system_messages': 0, 'user_messages': 0, 'assistant_messages': 0,
             'tool_messages': 0, 'system_chars': 0, 'user_chars': 0,
             'assistant_chars': 0, 'tool_chars': 0, 'image_count': 0}
    roles = {'SystemMessage': 'system', 'UserMessage': 'user',
             'AssistantMessage': 'assistant', 'ToolResult': 'tool'}
    total = 0
    for message in request.messages:
        role = roles.get(type(message).__name__)
        content = getattr(message, 'content', None)
        chars = len(content) if isinstance(content, str) else 0
        total += chars
        if role:
            stats[role + '_messages'] += 1
            stats[role + '_chars'] += chars
        images = getattr(message, 'images', ())
        if isinstance(images, (list, tuple)):
            stats['image_count'] += len(images)
    emit('model_request', stage='model_request', model=request.model,
         provider=type(provider).__name__, contract=request.response_contract.name,
         format=request.response_contract.format, stream=request.stream,
         message_count=len(request.messages), message_chars=total,
         tool_count=len(request.tools), **stats)


def begin_attempt():
    if _active.get() is not None:
        _attempt.set(_attempt.get() + 1)


def response_received(raw, request):
    session = _active.get()
    if session is None:
        return
    try:
        runtime_trace.record_model_response(
            session.state_dir, session.job_id, session.request_index, _attempt.get(), raw, request
        )
    except Exception:
        pass
    content = raw.message.content
    emit('model_response', stage='response_acceptance', stream=raw.stream,
         content_chars=len(content), reasoning_chars=len(raw.message.reasoning),
         tool_count=len(raw.message.tool_calls),
         content_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(),
         json=json_diagnostic(content) if request.response_contract.format == 'json' else {'json_status':'not_checked'},
         input_tokens=getattr(raw.usage, 'input_tokens', None),
         output_tokens=getattr(raw.usage, 'output_tokens', None),
         total_tokens=getattr(raw.usage, 'total_tokens', None))


def exception_record(error, *, event='workflow_exception', stage='workflow'):
    if _active.get() is None:
        return
    chain, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(chain) < 8:
        seen.add(id(error))
        frames, tb = [], error.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            try:
                name = 'src/' + Path(frame.f_code.co_filename).resolve().relative_to(_ROOT).as_posix()
            except (ValueError, OSError):
                name = 'external/' + Path(frame.f_code.co_filename).name
            frames.append({'file': name, 'line': tb.tb_lineno, 'function': frame.f_code.co_name})
            tb = tb.tb_next
        item = {'error_type': type(error).__name__, 'code': getattr(error, 'code', ''),
                'status_code': getattr(error, 'status_code', None), 'frames': frames[-32:],
                'traceback_truncated': len(frames) > 32}
        observed_opcode = getattr(error, 'observed_opcode', None)
        if observed_opcode is not None:
            item['observed_opcode'] = observed_opcode
        detail = getattr(error, 'diagnostics', None)
        if isinstance(detail, dict):
            for key in ('violation_count', 'attempt_count', 'max_attempts', 'stop_reason'):
                item[key] = detail.get(key)
            if isinstance(detail.get('violations'), list):
                item['violations'] = detail['violations'][:32]
        raw_violations = getattr(error, 'violations', None)
        if isinstance(raw_violations, (list, tuple)):
            item['violation_count'] = len(raw_violations)
            item['violations'] = [
                {'path': getattr(value, 'path', ''), 'reason': getattr(value, 'reason', '')}
                for value in raw_violations[:32]
            ]
        if isinstance(error, json.JSONDecodeError):
            item.update(json_error=error.msg, line=error.lineno,
                        column=error.colno, position=error.pos)
        chain.append(item)
        error = error.__cause__ or error.__context__
    emit(event, stage=stage, exceptions=chain, exception_count=len(chain))


def _export_private_job_record(state_dir, job_id):
    """Read the persisted job record for an operator-authorized diagnostic export."""
    try:
        from application.workspace import contained, read_json, record_id
        state = Path(state_dir).resolve()
        directory = contained(state / "jobs", state)
        path = contained(directory / (record_id(job_id, "job") + ".json"), directory)
        if not path.is_file() or path.is_symlink():
            return None
        record = read_json(path)
        if not isinstance(record, dict) or record.get("id") != job_id:
            return None
        return record
    except (KeyError, ValueError, OSError, TypeError):
        return None


def _export_private_job_snapshot(state_dir, job_id):
    record = _export_private_job_record(state_dir, job_id)
    snapshot = record.get("snapshot") if isinstance(record, dict) else None
    return snapshot if isinstance(snapshot, dict) else None


def _export_job_snapshot(state_dir, job):
    snapshot = job.get("snapshot") if isinstance(job, dict) else None
    if isinstance(snapshot, dict):
        return snapshot
    job_id = job.get("id") if isinstance(job, dict) else None
    if not isinstance(job_id, str):
        return None
    return _export_private_job_snapshot(state_dir, job_id)


def _export_repair_baseline_app_instrs(snapshot):
    """Return bounded APP_INSTR identity metadata from the repair baseline only."""
    if not isinstance(snapshot, dict) or not snapshot.get("repair_mode"):
        return []
    baseline = snapshot.get("repair_baseline")
    if not isinstance(baseline, dict):
        return []
    allowed_ids = {
        int(value) for value in (snapshot.get("allowed_rung_ids") or [])
        if isinstance(value, int) and not isinstance(value, bool)
    }
    entries = []
    for rung in baseline.get("rungs") or []:
        if not isinstance(rung, dict):
            continue
        rung_id = rung.get("rung_id")
        if allowed_ids and rung_id not in allowed_ids:
            continue
        for branch in rung.get("branches") or []:
            if not isinstance(branch, dict):
                continue
            branch_id = branch.get("branch_id")
            for output_index, output in enumerate(branch.get("outputs") or []):
                if not isinstance(output, dict) or output.get("type") != "APP_INSTR":
                    continue
                opcode = _safe_opcode(output.get("opcode"))
                if opcode is None:
                    continue
                entry = {"output_index": output_index, "opcode": opcode}
                if isinstance(rung_id, int) and not isinstance(rung_id, bool):
                    entry["rung_id"] = rung_id
                if isinstance(branch_id, int) and not isinstance(branch_id, bool):
                    entry["branch_id"] = branch_id
                entries.append(entry)
                if len(entries) >= 32:
                    return entries
    return entries


def _enrich_export_opcode_context(records, state_dir, job):
    snapshot = _export_job_snapshot(state_dir, job)
    baseline_app_instrs = _export_repair_baseline_app_instrs(snapshot)
    baseline_opcode = (
        baseline_app_instrs[0].get("opcode")
        if len(baseline_app_instrs) == 1 else None
    )
    project = snapshot.get("project") if isinstance(snapshot, dict) else None
    plc_model = project.get("plc_model") if isinstance(project, dict) else None
    allowed = None
    try:
        from plc.instructions import generation_app_instr_mnemonics
        allowed = set(generation_app_instr_mnemonics(plc_model or "FX3U"))
    except Exception:
        allowed = None
    for record in records:
        if not isinstance(record, dict) or record.get("event") != "workflow_exception":
            continue
        for exception in record.get("exceptions") or []:
            if not isinstance(exception, dict):
                continue
            for violation in exception.get("violations") or []:
                if not isinstance(violation, dict):
                    continue
                observed = violation.get("observed_opcode")
                if not isinstance(observed, str) or not observed:
                    continue
                if baseline_app_instrs:
                    violation["baseline_app_instrs"] = [dict(item) for item in baseline_app_instrs]
                if baseline_opcode is not None:
                    violation["baseline_opcode"] = baseline_opcode
                if allowed is not None:
                    violation["allowed_by_registry"] = observed.upper() in allowed
    return records


def export_diagnostics(state_dir, job):
    """Export one operator-authorized job with metadata and detailed local evidence."""
    job_id = job['id']
    path = _path(state_dir, job_id)
    records, capture_status = deque(maxlen=_MAX_EXPORT_LINES), 'not_captured'
    valid_count = 0
    if path.is_file():
        if path.stat().st_size > _MAX_FILE:
            raise ValueError('Diagnostic file exceeds export limit')
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                    if (not isinstance(value, dict) or value.get('job_id') != job_id or
                        value.get('schema_version') != _SCHEMA or value.get('event') not in _ALLOWED_EVENTS):
                        continue
                    valid_count += 1
                    records.append({'schema_version':_SCHEMA, 'event':value['event'], **_safe_fields(value)})
                except (ValueError, RecursionError):
                    continue
            else:
                capture_status = ('export_truncated' if valid_count > _MAX_EXPORT_LINES else
                                  'captured' if records else 'unreadable')
    records = _enrich_export_opcode_context(records, state_dir, job)
    private_job = _export_private_job_record(state_dir, job_id)
    job_record = runtime_trace.sanitize(private_job or job)
    snapshot = private_job.get("snapshot") if isinstance(private_job, dict) else None
    project_id = (snapshot or {}).get("project_id") if isinstance(snapshot, dict) else job.get("project_id")
    frozen_project = snapshot.get("project") if isinstance(snapshot, dict) else None
    receipt_id = frozen_project.get("confirmed_decision_receipt_id") if isinstance(frozen_project, dict) else None
    history = frozen_project.get("decision_history") if isinstance(frozen_project, dict) else None
    decision_receipt = history.get(receipt_id) if isinstance(history, dict) and receipt_id else None
    decision_receipt = runtime_trace.sanitize(decision_receipt) if isinstance(decision_receipt, dict) else None
    transcript = runtime_trace.load_transcript(state_dir, job_id)
    operator_actions = runtime_trace.load_operator_actions(
        state_dir, project_id=project_id, job_id=job_id
    )

    def contains_observed_opcode(value):
        if isinstance(value, dict):
            return ('observed_opcode' in value or 'baseline_opcode' in value
                    or 'baseline_app_instrs' in value
                    or any(contains_observed_opcode(item) for item in value.values()))
        if isinstance(value, (list, tuple)):
            return any(contains_observed_opcode(item) for item in value)
        return False

    meta = {'schema_version':_SCHEMA, 'job_id':job_id, 'capture_status':capture_status,
            'job':_safe_fields({key: job.get(key) for key in ('kind', 'status', 'project_id', 'version_id')}),
            'event_count':len(records),
            'transcript_count':len(transcript), 'operator_action_count':len(operator_actions),
            'content_included':bool(job_record or transcript or operator_actions), 'keys_included':False,
            'validation_values_included':any(contains_observed_opcode(item) for item in records),
            'captured_after_upgrade_only':True,
            'decision_receipt_id':receipt_id,
            'decision_receipt_status':'available' if decision_receipt else 'not_recorded'}
    from application.job_errors import public_error_details
    meta['error_details'] = public_error_details(job.get('error_details'))
    meta['error_code'] = _identifier(job.get('error_code') or 'none')
    failure_analysis = {}
    for event_name in ('model_request', 'context_audit', 'provider_result', 'model_response',
                       'response_rejected', 'workflow_exception', 'retrieval_failed'):
        matched = next((item for item in reversed(records) if item.get('event') == event_name), None)
        if matched is not None:
            failure_analysis[event_name] = {
                key: value for key, value in matched.items()
                if key not in {'schema_version', 'event', 'timestamp', 'job_id'}
            }
    if failure_analysis:
        meta['failure_analysis'] = failure_analysis
    roots = [Path(sys.executable).parent] if getattr(sys, 'frozen', False) else [_ROOT.parent]
    for root in roots:
        info = root / 'build-info.json'
        if info.is_file() and info.stat().st_size <= 128*1024:
            try:
                source = json.loads(info.read_text(encoding='utf-8-sig'))
                meta['build'] = {key:value for key in ('base_commit','build_input_commit')
                                 if isinstance(value := source.get(key), str) and re.fullmatch('[0-9a-f]{40}', value)}
            except (ValueError, OSError):
                pass
    text = json.dumps(meta, ensure_ascii=False, indent=2) + '\n'
    guide = ('GXWorks task diagnostics\n\n'
             'This operator-only archive contains the job timeline and model I/O needed to reproduce failures.\n'
             'summary.json: compact failure summary and capture status.\n'
             'diagnostics.jsonl: sanitized structured metadata in chronological order.\n'
             'job.json: persisted job snapshot, status, result and event timeline, with credentials/binaries redacted.\n'
             'transcript.jsonl: actual model messages, reasoning, final content, tool calls, request options and usage.\n'
             'operator_actions.jsonl: relevant specification/job actions recorded for this project/job.\n'
             'decision_receipt.json: when available, the historical analysis/confirmation audit bound to this job.\n'
             'offline_replay.json: optional offline replay result added later by scripts/context_replay.py --archive.\n'
             'API credentials, Authorization values and image/binary bodies are not included.\n'
             'Model/user text is included because this export is explicitly operator-only and downloaded on demand.\n'
             'Output is not uploaded automatically. Inspect before sharing.\n')
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('summary.json', text)
        archive.writestr('diagnostics.jsonl', ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records))
        archive.writestr('job.json', json.dumps(job_record, ensure_ascii=False, indent=2) + '\n')
        archive.writestr('transcript.jsonl', ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in transcript))
        archive.writestr('operator_actions.jsonl', ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in operator_actions))
        if decision_receipt is not None:
            archive.writestr('decision_receipt.json', json.dumps(decision_receipt, ensure_ascii=False, indent=2) + '\n')
        archive.writestr('README.txt', guide)
    return target.getvalue()