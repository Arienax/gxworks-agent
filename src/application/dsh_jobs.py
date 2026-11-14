# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.
"""Web jobs hosted by DSH; engineering transactions stay in WorkbenchService.

This module owns no model loop or message replay. DSH owns both, including
provider reasoning and tool history. The bridge only binds tools to the selected
project, turns host input into deltas and translates progress into Web events.
"""
from __future__ import annotations

import base64
import copy
import json
import secrets
import threading
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_runtime.messages import ToolCall
from application.context_units import ContextUnits, RESTORE_TOOL, restore_tool_schema, without_knowledge
from application.instruction_research import new_candidate
from application.read_delivery import PAGED_READ_TOOLS, REVIEWED_READ_TOOLS, collect_host_read, serialized_size
from application.workspace import ConflictError, atomic_json, read_json


SYSTEM_PROMPT = """你在 GXWorks Agent 工作台中协助用户完成 PLC 工程。
工程和版本由工作台绑定，不要新建其他工程或绕过工程工具直接写工程文件。
Design/单方案阶段：按工作台提供的分析资料提交 propose_design_plan。
保存有效草稿后结束本轮，等待工作台用户确认；不要替用户确认或生成程序。
Direct 阶段不需要额外方案确认。生成、确认后实施、修改均使用工程工具完成；
按工作台提供的型号规则和输出契约实施，必要时查询 search_plc_manual，
提交 create_program_candidate 或 patch_program。不要调用其他模型代写。
工程工具的诊断是继续修正的反馈，不能把一次工具错误当成整个任务结束。
修正必须保留用户要求和修改范围；资料未知不等于不支持，不自行编造操作数或工艺条件。
前轮的用户输入、工具结果和 RAG 留在同一会话中；续轮只处理新输入和实际变化，
不要要求用户复述原文，不要重复查询已经拿到且仍适用的资料。
new_knowledge 是本轮新增资料。knowledge_version 变化时使用与当前版本相符的资料。
资料正文由工作台组装。$conversation_ref 指向前文资料，需要时可按 ID 批量恢复。
只补查实际使用的指令缺项。确认后沿用已选方案。
必须追问时使用 gxworks_request_input 后结束本轮，不能自行回答。仅在有实际保存结果时
宣称程序已生成；原生编译、行为检查和未验证分别说明。回复使用本轮指定语言。
"""

TOOL_NAMES = frozenset({
    'get_design_context', 'propose_design_plan', 'get_generation_context',
    'get_current_project', 'get_project_spec', 'get_current_program_info',
    'read_network', 'read_gxw_source', 'search_plc_manual', 'get_diagnostics',
    'validate_project', 'validate_current_program', 'compile_project',
    'create_program_candidate', 'patch_program', 'get_operation_status',
    'get_project_artifacts', 'read_native_device_assignments',
    'compare_native_compiled_code', 'locate_native_code_steps',
})
def _schema(name, description, properties, required):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties, 'required': required,
                       'additionalProperties': False}}}


def json_changes(previous, current, path=''):
    """Exact host-state changes, not a model-written summary of the specification."""
    if previous == current:
        return []
    if isinstance(previous, dict) and isinstance(current, dict):
        result = []
        for key in sorted(previous.keys() | current.keys()):
            location = path + '/' + key.replace('~', '~0').replace('/', '~1')
            if key not in current:
                result.append({'op': 'remove', 'path': location})
            elif key not in previous:
                result.append({'op': 'add', 'path': location, 'value': current[key]})
            else:
                result.extend(json_changes(previous[key], current[key], location))
        return result
    return [{'op': 'replace', 'path': path, 'value': current}]


def runtime_config(provider, model):
    from model_runtime.catalog import endpoint_id
    from model_runtime.request_policy import resolve_request, contract_capability_available
    runtime = provider._runtime_profile(model.get('model'))
    responses = endpoint_id(runtime.endpoint) == 'https://api.openai.com/v1'
    transport_defaults = {}
    if not responses:
        for capability, name, value in (('thinking_required', 'thinking', {'type': 'enabled'}),
                                         ('tool_stream', 'tool_stream', True)):
            if contract_capability_available(runtime.contract, capability, options={'stream': True}):
                transport_defaults.setdefault('extra_body', {})[name] = value
    resolved = resolve_request(runtime, transport_defaults=transport_defaults)
    capacity = runtime.contract.capabilities.get('context_window')
    return {'adapter': runtime.adapter, 'base_url': runtime.endpoint, 'model': runtime.model,
            'api_key': provider.api_key,
            'api': 'openai-responses' if responses else 'openai-completions',
            **({'context_window': capacity.value} if capacity and type(capacity.value) is int else {}),
            'supports_images': contract_capability_available(runtime.contract, 'vision', options={}),
            'reasoning_effort': resolved.values.get('reasoning_effort'),
            'request_parameters': copy.deepcopy(dict(resolved.options)),
            'system_prompt': SYSTEM_PROMPT}


class ToolBridge:
    def __init__(self, service, session_id, directory):
        self.service, self.session_id = service, session_id
        self.directory = directory
        self.token = secrets.token_urlsafe(32)
        self.ctx = self.snapshot = self.session = None
        self.saved = None
        self.proposed = False
        self.questions = None
        self.last_error = None
        self.checked_write = False
        self.units = ContextUnits(directory / 'context-units.json')
        self._events_lock = threading.RLock()
        self.request_count = 0
        self._usage_seen = set()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.dispatch()

            def do_POST(self):
                self.dispatch()

            def dispatch(self):
                if self.headers.get('Authorization') != 'Bearer ' + bridge.token:
                    self.send_error(403)
                    return
                try:
                    if self.command == 'GET' and self.path == '/tools':
                        data = {'tools': bridge.tools()}
                    elif self.command == 'POST' and self.path in {'/invoke', '/event'}:
                        payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                        if payload.get('session_id') != bridge.session_id:
                            raise ValueError('Tool call belongs to another conversation')
                        if self.path == '/invoke':
                            data = bridge.invoke(payload)
                        else:
                            bridge.events(payload.get('frames', [payload.get('frame') or {}]))
                            data = {'ok': True}
                    else:
                        self.send_error(404)
                        return
                except Exception as error:
                    if isinstance(error, ConflictError):
                        bridge.binding_error = error
                        data = {'ok': False, 'cancelled': True}
                    else:
                        data = {'ok': False, 'error': {'code': 'WEB_TOOL_ERROR', 'message': str(error)}}
                except BaseException:
                    data = {'ok': False, 'cancelled': True}
                raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='dsh-plc-tools')
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def bind(self, ctx, snapshot):
        from application.engineering_session import EngineeringSession
        self.ctx, self.snapshot = ctx, snapshot
        from application.model_progress import ModelProgressReporter
        self.progress = ModelProgressReporter(ctx)
        if self.session:
            self.session.close()
        self.session = EngineeringSession.attach(self.service, project_id=snapshot['project_id'],
            version_id=snapshot.get('version_id'), change_scope=snapshot.get('change_scope'),
            program=snapshot.get('program'), before_write=self.before_write, design_job_id=ctx.job_id,
            direct_input=({'facts': snapshot.get('direct_user_facts'),
                           'requirements': snapshot.get('direct_requirements'),
                           'answers': snapshot.get('direct_answers', snapshot.get('clarification_answers'))}
                          if snapshot['kind'] == 'direct_generation' else None))
        self.session.design.analysis_mode = snapshot.get('analysis_mode', 'direct')
        self.session.host_direct = snapshot['kind'] == 'direct_generation'
        self.saved = self.questions = self.last_error = None
        self.last_candidate_error = None
        self.generation_document = None
        self.generation_receipt = None
        self.binding_error = None
        self.manual_documents = set()
        self.initial_context = None
        self.proposed = False
        self.checked_write = False
        self.request_count = 0
        self._usage_seen.clear()
        # Confirmation happens in Web, never via a model-authored tool call.
        if snapshot['kind'] == 'generation' and snapshot['project'].get('confirmed_spec'):
            path = self.session.design._path(snapshot['project_id'])
            state = read_json(path) if path.is_file() else {}
            state['stage'] = 'confirmed'
            atomic_json(path, state)

    def before_write(self):
        self.ctx.checkpoint()
        if not self.checked_write:
            self.service.validate_snapshot(self.snapshot)

    def tools(self):
        result = []
        for row in self.session.list_tools():
            tool = row['function']
            name = tool['name']
            if name not in TOOL_NAMES:
                continue
            schema = tool['parameters']
            if name in PAGED_READ_TOOLS:
                for field in ('read_format', 'read_budget_tokens', 'cursor'):
                    schema['properties'].pop(field, None)
                if name in {'get_design_context', 'get_generation_context'}:
                    tool['description'] = '本轮工程资料已由工作台提供。此工具可查看同一任务的资料。'
                elif name == 'search_plc_manual':
                    tool['description'] = tool['description'].split(' 默认直接读取 data.document')[0]
            for field in ('analysis_id', 'repair_id', 'generation_context_id'):
                schema['properties'].pop(field, None)
            review = schema['properties'].get('implementation_review')
            if review:
                review.pop('required', None)
                for field in ('context_document', 'manual_documents'):
                    review['properties'].pop(field, None)
            if name == 'propose_design_plan':
                tool['description'] = ('提交分析草稿。若返回 repair_required，只提交列出的局部 repairs。'
                                       '工作台自动关联当前草稿和修复。')
            result.append(row)
        result.append(_schema('gxworks_request_input', '提交需要用户回答的问题，然后结束本轮。不能代用户作答。',
            {'questions': {'type': 'array', 'minItems': 1, 'items': {'type': 'object',
                'properties': {'id': {'type': 'string'}, 'question': {'type': 'string'},
                    'options': {'type': 'array', 'items': {'type': 'string'}}, 'required': {'type': 'boolean'}},
                'required': ['id', 'question'], 'additionalProperties': False}}}, ['questions']))
        restore = restore_tool_schema()
        restore['function']['parameters']['properties'].pop('read_budget_tokens', None)
        result.append(restore)
        return result

    def prepare_context(self, capacity):
        # This is a transport limit, independent of the RAG retrieval budget.
        self.read_capacity = min(128000, max(1000, capacity // 2)) if capacity else 64000
        name = 'get_design_context' if self.snapshot['kind'] == 'analysis' else 'get_generation_context'
        envelope = self.invoke({'name': name, 'call_id': 'host-context:' + self.ctx.job_id,
                                'host_context': True})
        if not envelope.get('ok'):
            raise ValueError(str(envelope.get('error', {}).get('message') or '无法准备工程资料。'))
        self.initial_context = envelope
        return envelope['data']

    def _bind_submission(self, name, args):
        if name == 'propose_design_plan':
            # The host loaded this task's context. Let EngineeringSession bind
            # the current draft and repair while holding the workspace lock.
            if self.initial_context and ('analysis' in args or 'repairs' in args):
                args['analysis_id'] = self.initial_context['data']['document']['analysis_id']
            args.pop('repair_id', None)
        if new_candidate(name, args) and self.generation_document:
            review = args.pop('implementation_review', None)
            if review is None:
                review = {}
            if isinstance(review, dict):
                review.update(context_document=self.generation_document,
                              manual_documents=sorted(self.manual_documents))
            args['implementation_review'] = review
            args.pop('generation_context_id', None)
        if name == 'patch_program':
            args.pop('generation_context_id', None)
            if self.generation_receipt:
                args['generation_context_id'] = self.generation_receipt

    def _remember_generation(self, data):
        self.generation_document = data['delivery']['document_id']
        self.generation_receipt = data['document'].get('generation_context_id')

    def _refresh_read(self, name, args, original, call_id):
        result = self._read(name, dict(args), call_id)
        if not result.get('ok'):
            raise ConflictError('无法恢复当前任务的资料，请重新运行任务。')
        refreshed = result['data']
        previous = dict(original['document'])
        current = dict(refreshed['document'])
        for document in (previous, current):
            document.pop('generation_context_id', None)
        if (original['delivery']['fact_version'] != refreshed['delivery']['fact_version']
                or previous != current):
            raise ConflictError('当前任务所用资料已变化，请重新运行任务。')
        return refreshed

    def _refresh_generation_binding(self, call_id):
        self.service.validate_snapshot(self.snapshot)
        name = 'get_generation_context'
        refreshed = self._refresh_read(name, self._context_arguments(name, {}),
                                       self.initial_context['data'], call_id)
        self._remember_generation(refreshed)

    def _context_arguments(self, name, args):
        if name not in {'get_design_context', 'get_generation_context'}:
            return args
        args['user_requirement'] = (self.snapshot.get('user_fact_text') or
            self.snapshot.get('raw_requirement') or self.snapshot.get('text') or '')
        if name == 'get_generation_context':
            if self.snapshot.get('program'):
                args['program'] = self.snapshot['program']
            if not self.snapshot.get('version_id'):
                args['workflow'] = ('detailed' if self.snapshot['kind'] != 'direct_generation'
                                    and self.snapshot['project'].get('confirmed_spec') else 'direct_generation')
        return args

    def _read(self, name, args, call_id, *, hold_for_turn=False):
        # The Web host owns pagination. An old cursor cannot redirect it.
        args.pop('cursor', None)
        capacity = getattr(self, 'read_capacity', 64000)
        args.update(read_format='resolved', read_budget_tokens=capacity)
        deferred = name in REVIEWED_READ_TOOLS

        def read_page(read_id, arguments):
            self.ctx.checkpoint()
            return self.session.invoke(ToolCall(read_id, name, arguments, defer_read=deferred)).data

        envelope = collect_host_read(name, args, call_id, read_page,
            before_restart=lambda: self.service.validate_snapshot(self.snapshot))
        code = (envelope.get('error') or {}).get('code')
        if code == 'ENGINEERING_CONTEXT_CHANGED':
            raise ConflictError('无法完成资料交付，请从工作台重新发起任务。')
        oversize = (envelope.get('ok') and serialized_size(envelope)['estimated_tokens'] > capacity)
        if deferred and envelope.get('ok') and (oversize or not hold_for_turn):
            finished = self.session.finish_host_read(name, envelope['data']['delivery']['document_id'], not oversize)
            if not finished and not oversize:
                raise ConflictError('当前任务的资料交付记录无法确认，请从工作台重新发起任务。')
        if oversize or code in {'READ_BUDGET_TOO_SMALL', 'READ_OBJECT_TOO_LARGE'}:
            return {'ok': False, 'tool': name, 'error': {'code': 'HOST_CONTEXT_TOO_LARGE',
                'message': '完整资料超出本轮可交付容量，请查具体缺项或选择更大上下文的模型。'}}
        return envelope

    def invoke(self, payload):
        self.ctx.checkpoint()
        name = payload['name']
        args = copy.deepcopy(payload.get('arguments') or {})
        if name == RESTORE_TOOL:
            from agent_runtime.tool_errors import ToolPreconditionError
            try:
                args['read_budget_tokens'] = getattr(self, 'read_capacity', 64000)
                result = self.units.restore(args, hosted=True)
                result.pop('delivery', None)
                return result
            except ToolPreconditionError as error:
                return {'ok': False, 'tool': name, 'error': error.payload()}
            except ValueError as error:
                return {'ok': False, 'tool': name, 'error': {'code': 'INVALID_ARGUMENTS', 'message': str(error)}}
        if name == 'gxworks_request_input':
            from agent_runtime.tool_registry import ToolRegistry
            schema = next(row['function']['parameters'] for row in self.tools() if row['function']['name'] == name)
            ToolRegistry._validate_arguments(schema, args)
            questions = args['questions']
            if len({q['id'] for q in questions}) != len(questions) or any(not q['id'].strip() or not q['question'].strip() for q in questions):
                raise ValueError('问题需要唯一的非空 id 和具体问题')
            self.questions = questions
            return {'ok': True, 'status': 'waiting_for_user', 'instruction': '结束本轮，等待工作台答复。'}
        if name not in TOOL_NAMES:
            raise ValueError('Tool is not available in this bound Web project')
        if self.snapshot['kind'] == 'analysis' and name in {'create_program_candidate', 'patch_program'}:
            raise ValueError('先提交方案草稿，等待用户在工作台确认后再生成。')
        if self.snapshot['kind'] != 'analysis' and name in {'get_design_context', 'propose_design_plan'}:
            raise ValueError('本轮是实施或修改；不要重新创建 Design 任务。')
        if self.initial_context and name == self.initial_context['tool']:
            return self.units.project(self.initial_context)
        args = self._context_arguments(name, args)
        self._bind_submission(name, args)
        call_id = self.session_id + ':' + str(payload['call_id'])
        self.ctx.emit('progress', {'stage': 'tools', 'message': '正在执行工程工具：' + name})
        envelope = (self._read(name, args, call_id, hold_for_turn=payload.get('host_context') is True)
                    if name in PAGED_READ_TOOLS else
                    self.session.invoke(ToolCall(call_id, name, args)).data)
        error = envelope.get('error') or {}
        binding_errors = {'INSTRUCTION_REVIEW_REQUIRED', 'INSTRUCTION_READ_INCOMPLETE'}
        if (name in {'create_program_candidate', 'patch_program'} and self.initial_context
                and self.generation_document and error.get('code') in binding_errors):
            self._refresh_generation_binding(call_id + ':refresh')
            self._bind_submission(name, args)
            envelope = self.session.invoke(ToolCall(call_id + ':rebound', name, args)).data
            error = envelope.get('error') or {}
            if error.get('code') in binding_errors:
                raise ConflictError('当前任务的资料引用无法恢复，请从工作台重新发起任务。')
        if error.get('code') == 'CONFLICT' or (self.initial_context and name == 'propose_design_plan'
                and error.get('code') in {'DESIGN_CONTEXT_REQUIRED', 'DESIGN_PROJECT_CHANGED'}):
            raise ConflictError('任务期间工程状态已变化，请从工作台重新发起任务。')
        if not envelope.get('ok'):
            self.last_error = envelope.get('error')
            if name in {'create_program_candidate', 'patch_program'}:
                self.last_candidate_error = self.last_error
            self.ctx.emit('progress', {'stage': 'tool_error', 'message':
                name + '：' + str((self.last_error or {}).get('message', '工具未完成，模型可根据返回诊断继续修正。'))})
        data = envelope.get('data') or {}
        if name == 'get_generation_context' and envelope.get('ok'):
            self._remember_generation(data)
        if name == 'search_plc_manual' and (data.get('delivery') or {}).get('complete'):
            document = data['document']
            if (not args.get('plc_model') or args['plc_model'] == self.snapshot['project']['plc_model']) and (
                    not document.get('language') or document['language'] == self.snapshot['project']['target_mode']):
                self.manual_documents.add(data['delivery']['document_id'])
        if name == 'propose_design_plan' and envelope.get('ok') and data.get('draft_id'):
            self.proposed = True
        version_id = envelope.get('version_id') or data.get('version_id')
        if version_id and envelope.get('ok') and name in {'create_program_candidate', 'patch_program'}:
            self.saved = envelope
            self.session.version_id = version_id
            self.checked_write = True
        return envelope if payload.get('host_context') else self.units.project(envelope)

    def events(self, frames):
        """Deliver one stream batch without re-reading the job for every token.

        The SDK retains the original frames. Web only needs contiguous text,
        while request boundaries and metering must keep their original order.
        """
        with self._events_lock:
            pending = None
            parts = []

            def flush():
                nonlocal pending, parts
                if pending is not None:
                    self.event({**pending, 'chunk': {**pending['chunk'], 'text': ''.join(parts)}})
                    pending, parts = None, []

            for frame in frames:
                chunk = frame.get('chunk') or {}
                if frame.get('type') == 'chunk' and chunk.get('type') in {'text-delta', 'reasoning-delta'}:
                    if pending is not None and (pending.get('attemptId') != frame.get('attemptId')
                            or pending['chunk']['type'] != chunk['type']):
                        flush()
                    if pending is None:
                        pending = frame
                    parts.append(chunk.get('text') or chunk.get('delta') or '')
                else:
                    flush()
                    self.event(frame)
            flush()

    def event(self, frame):
        with self._events_lock:
            self.ctx.checkpoint()
            if frame.get('type') == 'request':
                from model_runtime.usage import public_usage
                parameters = []
                effort = frame.get('reasoning_effort')
                if effort in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}:
                    parameters.append({'name': 'reasoning_effort', 'value': effort,
                        'source': 'user_selection', 'sent': True})
                self.ctx.emit('model_request', public_usage({**self.snapshot['model'],
                    'parameters': parameters, 'features': {
                        name: frame.get(name) is True
                        for name in ('tools', 'streaming', 'structured_output', 'images')}}))
            elif frame.get('type') == 'start':
                self.request_count += 1
                self.progress(SimpleNamespace(phase='waiting', received_characters=0))
                self.progress.preview(SimpleNamespace(kind='start'))
            elif frame.get('type') == 'chunk':
                chunk = frame.get('chunk') or {}
                kind = chunk.get('type')
                if kind in {'text-delta', 'reasoning-delta'}:
                    text = chunk.get('text') or chunk.get('delta') or ''
                    event = 'reasoning' if kind == 'reasoning-delta' else 'content'
                    self.progress.preview(SimpleNamespace(kind=event, text=text))
                    self.ctx.emit(event, {'text': text})
                elif kind == 'usage':
                    self._usage(frame.get('attemptId'), chunk.get('usage') or {})
            elif frame.get('type') == 'end':
                self.progress.preview(SimpleNamespace(kind='complete'))
                self.ctx.flush()

    def _usage(self, identity, usage):
        if not usage or identity in self._usage_seen:
            return
        self._usage_seen.add(identity)
        cached = usage.get('cacheReadTokens', 0) or 0
        uncached = (usage.get('inputTokens', 0) or 0) + (usage.get('cacheWriteTokens', 0) or 0)
        self.ctx.emit('model_token_usage', {'input_tokens': uncached + cached,
            'output_tokens': usage.get('outputTokens', 0) or 0,
            **({'cached_input_tokens': cached} if 'cacheReadTokens' in usage else {})})

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if self.session:
            self.session.close()


class DshJobs:
    def __init__(self, service, runtime_factory=None):
        self.service = service
        self.runtime_factory = runtime_factory
        self._active = set()
        self._lock = threading.RLock()
        self._closed = False

    def close(self):
        with self._lock:
            self._closed = True
            active = tuple(self._active)
        for runtime in active:
            runtime.close()

    def run(self, ctx, snapshot, context, images, provider):
        from integrations.dsh_runtime import DshRuntime
        project_id = snapshot['project_id']
        conversation = snapshot.get('conversation_id') or (ctx.job_id if snapshot.get('start_conversation') else project_id)
        # Same project/conversation means the same DSH history, including after restart.
        session_id = 'session-gxworks-' + project_id + '-' + conversation
        directory = self.service.state_dir / 'dsh' / session_id
        directory.mkdir(parents=True, exist_ok=True)
        state_path = directory / 'web-turn.json'
        state = read_json(state_path) if state_path.is_file() else {}
        bridge = ToolBridge(self.service, session_id, directory)
        bridge.bind(ctx, snapshot)
        runtime = None
        watcher_done = threading.Event()
        cancelled = []
        try:
            config = runtime_config(provider, snapshot['model'])
            factory = self.runtime_factory or DshRuntime
            runtime = factory(config, bridge.url, bridge.token,
                self.service.state_dir / 'dsh' / 'home', self.service.projects.workspace)
            with self._lock:
                if self._closed:
                    raise RuntimeError('Workbench DSH runtime is closing')
                self._active.add(runtime)
            ctx.emit('progress', {'stage': 'harness', 'message': '正在连接 DSH 会话与工程工具'})
            runtime.start()
            if state.get('started'):
                try:
                    runtime.resume(session_id)
                except Exception:
                    ctx.emit('progress', {'stage': 'harness_error', 'message':
                        '无法恢复原 DSH 会话；已有工程已保留，本轮尚未向模型发送新请求。'})
                    raise
            from application.confirmed_generation_context import project_confirmed_specification
            spec = snapshot['project'].get('confirmed_spec')
            host = {'project_id': project_id, 'plc_model': context.plc_model,
                'version_id': snapshot.get('version_id'),
                'confirmed_spec': project_confirmed_specification(spec) if spec else None,
                'program': snapshot.get('program'), 'change_scope': snapshot.get('change_scope')}
            prompt = {'stage': snapshot['kind'], 'analysis_mode': snapshot.get('analysis_mode'),
                'response_language': snapshot['response_language'], 'user_request': snapshot.get('text', ''),
                **({'host_changes': json_changes(state.get('host'), host)} if state.get('started') else {'host': host})}
            if snapshot.get('clarification_answers'):
                prompt['user_answers'] = snapshot['clarification_answers']
            prepared = bridge.prepare_context(config.get('context_window'))
            fact_additions = bridge.units.new_facts(prepared)
            knowledge_version = prepared['delivery']['fact_version']
            engineering = copy.deepcopy(prepared['document'])
            prior_input = engineering.get('prior_user_input')
            if prior_input and prior_input == state.get('direct_requirements'):
                engineering.pop('prior_user_input')
            from shared.model_context import technical_value
            confirmed_for_model = technical_value(host['confirmed_spec'])
            for key in ('analysis_id', 'generation_context_id', 'generation_request'):
                engineering.pop(key, None)
            if engineering.get('confirmed_spec') == confirmed_for_model:
                engineering.pop('confirmed_spec', None)
            if 'generation_input' in engineering:
                inputs = engineering['generation_input']
                inputs.pop('request', None)
                if inputs.get('confirmed_spec') == confirmed_for_model:
                    inputs.pop('confirmed_spec', None)
            engineering_fields = without_knowledge(engineering)
            if 'engineering' in state:
                prompt['engineering_changes'] = json_changes(without_knowledge(state['engineering']), engineering_fields)
                if fact_additions:
                    prompt['new_knowledge'] = list(fact_additions.values())
            else:
                prompt['engineering_context'] = engineering
            if state.get('knowledge_version') != knowledge_version:
                prompt['knowledge_version'] = knowledge_version
            if snapshot['kind'] == 'analysis':
                prompt['action'] = '根据本轮分析资料提交有效方案草稿，完成后等待用户确认。'
            else:
                prompt['action'] = '接着本会话的资料实施用户要求，通过工程工具保存结果；失败时根据具体诊断修正。'
            blocks = [{'type': 'text', 'text': json.dumps(prompt, ensure_ascii=False)}]
            blocks.extend({'type': 'image', 'mimeType': image.media_type,
                           'data': base64.b64encode(image.data).decode('ascii')} for image in images)
            accepted = False

            def notification(item):
                nonlocal accepted
                event = item.payload.get('event') or {}
                if (not accepted and item.method == 'session.event'
                        and item.payload.get('sessionId') == session_id
                        and event.get('type') == 'agent/inbox/spliced'):
                    if not bridge.session.finish_host_read(bridge.initial_context['tool'],
                            prepared['delivery']['document_id'], True):
                        raise ConflictError('当前任务的资料交付记录无法确认，请从工作台重新发起任务。')
                    accepted = True
                    bridge.units.remember(fact_additions)
                    atomic_json(state_path, {**state, 'started': True, 'host': host,
                                             'engineering': engineering_fields, 'knowledge_version': knowledge_version,
                                             'direct_requirements': (snapshot.get('direct_requirements') or prior_input
                                                                     or state.get('direct_requirements')),
                                             'job_id': ctx.job_id})

            def watch():
                while not watcher_done.wait(.25):
                    try:
                        ctx.checkpoint()
                    except BaseException as error:
                        cancelled.append(error)
                        runtime.close()
                        return

            watcher = threading.Thread(target=watch, daemon=True, name='dsh-job-cancel')
            watcher.start()
            try:
                result = runtime.run(blocks, session_id=session_id, on_notification=notification)
            except BaseException:
                if bridge.binding_error:
                    raise bridge.binding_error
                if cancelled:
                    raise cancelled[0]
                raise
            finally:
                watcher_done.set()
                watcher.join(timeout=2)
            # A completed model turn is not yet a durable resumable session.
            # Surface SDK persistence errors before claiming a finished Web job.
            try:
                runtime.close()
            except Exception:
                ctx.emit('progress', {'stage': 'harness_error', 'message':
                    'DSH 会话保存失败，不能标记为可续接；已保存的工程版本仍然保留。'})
                raise
            ctx.checkpoint()
            if bridge.binding_error:
                raise bridge.binding_error
            output = self._output(bridge, snapshot, context)
            output.update(runtime='dsh', harness_session_id=session_id, message=result.final_response)
            atomic_json(self.service.state_dir / 'outputs' / (ctx.job_id + '.json'), output)
            return {key: output[key] for key in ('version_id', 'proposal_id', 'status', 'runtime') if key in output}
        finally:
            watcher_done.set()
            try:
                if runtime:
                    runtime.close()
            finally:
                with self._lock:
                    self._active.discard(runtime)
                bridge.close()

    def _output(self, bridge, snapshot, context):
        if bridge.questions:
            return {'status': 'needs_input', 'generation': {'missing_info': bridge.questions}}
        if snapshot['kind'] == 'analysis':
            path = bridge.session.design._path(snapshot['project_id'])
            state = read_json(path) if path.is_file() else {}
            if not bridge.proposed or state.get('stage') != 'confirm_design_plan' or not state.get('draft'):
                raise ValueError('DSH 未提交有效需求草稿：' + str(bridge.last_error or state.get('stage', '无草稿')))
            draft = state['draft']
            analysis = state['analysis']
            document = snapshot['project'].get('_spec_document')
            if document:
                from application.spec_document import merge_analysis
                draft = merge_analysis(document['draft'], draft, analysis,
                                       snapshot.get('user_fact_text', snapshot.get('text', '')))
                with self.service.lock.thread_lock:
                    bridge.ctx.checkpoint()
                    self.service.validate_snapshot(snapshot)
                    draft = self.service.project_commands.save_analysis_draft(snapshot['project_id'], draft,
                        document['draft_revision'])['draft']
            return {'analysis_mode': snapshot.get('analysis_mode', 'direct'), 'analysis': analysis,
                'base_program': snapshot.get('program') or (context.version or {}).get('program_name'),
                'edit_text': snapshot.get('user_fact_text', snapshot.get('text', '')),
                'spec_draft': draft, 'spec_base_hash': state['base_hash'], 'base_version_id': snapshot.get('version_id')}
        if bridge.saved:
            data = bridge.saved.get('data') or {}
            version_id = bridge.saved.get('version_id') or data['version_id']
            return {'version_id': version_id,
                'generation': self.service.projects.raw_version(snapshot['project_id'], version_id),
                **({'generation_action': snapshot['generation_action']} if snapshot.get('generation_action') else {}),
                'proposal_id': bridge.saved.get('proposal_id') or data.get('proposal_id'),
                'status': bridge.saved.get('status', 'saved')}
        error = bridge.last_candidate_error or {}
        if error.get('code') == 'CANDIDATE_BEHAVIOR_MISMATCH':
            return {'status': 'behavior_mismatch', 'generation': {'behavior_result': error['behavior_result']}}
        raise ValueError('DSH 本轮未保存程序：' + str(bridge.last_error or '请查看会话输出和工具记录。'))
