"""A revision-bound native engineering session spanning approved calls.

The desktop coordinator owns this object on its fixed COM thread. Tokens
identify that owned session; proposal approval still authorizes every run.
Native observations, stage saves and saved application versions stay distinct.
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
import json
import re
import threading

from .native_engineering import _PRODUCT_FIELDS, _identity


def _exclusive_operation(method):
    """One native writer; status/pause never wait for a vendor call to return."""
    @wraps(method)
    def execute(self, *args, **kwargs):
        if not self._operation_guard.acquire(blocking=False):
            raise RuntimeError('A native session operation is already in progress')
        try:
            return method(self, *args, **kwargs)
        finally:
            self._operation_guard.release()
    return execute


class LiveCapabilityError(ValueError):
    """No native action was authorized by an exact operation evidence record."""
    def __init__(self, message, authorization):
        super().__init__(message)
        self.authorization = deepcopy(authorization)


def is_live_engineering_receipt(value):
    """Distinguish operational results from plans and generation contracts."""
    if not isinstance(value, dict) or value.get('mode') != 'live' or not isinstance(value.get('status'), str):
        return False
    session_id = value.get('session_id')
    return (isinstance(session_id, str) and re.fullmatch(r'[a-f0-9]{32}', session_id) is not None
            or type(value.get('passed')) is bool)


def live_engineering_summary(result):
    """Publish source/receipt data without native handles, files or saved bytes."""
    if not isinstance(result, dict):
        return {'mode': 'live', 'status': 'unconfirmed', 'passed': False}
    from shared.public_results import public_result_field
    fields = {'mode', 'session_id', 'project_id', 'base_version_id', 'version_id', 'session_status', 'status', 'passed', 'revision',
        'saved_revision', 'version_revision', 'version_matches_active_source', 'native_disk_save',
        'dirty', 'pending_commands', 'pause_requested', 'compile', 'assignments', 'save', 'steps',
        'executed_steps', 'stage_saves', 'data_revision', 'error', 'identity', 'current_context',
        'operation_in_progress', 'pending_save_transaction', 'controller_closed', 'ide_policy', 'replayed', 'message',
        'native_ui_language', 'project_codepage', 'operation_capabilities', 'capability_authorization'}
    def public(value):
        if isinstance(value, dict):
            return {key: public(item) for key, item in value.items()
                    if isinstance(key, str) and public_result_field(key)
                    and key not in {'raw', 'record', 'records', 'data_id', 'control_id', 'hwnd', 'pointer', 'ptr',
                        'bytes_base64', 'data_base64', 'native_bytes', 'snapshot', 'observation', 'unverified_observation',
                        'image', 'executable', 'project_path', 'summary_handle', 'editor_handle',
                        'document_handle', 'window_handle', 'process_started'}
                    and not key.lower().endswith(('_path', '_dir', '_file', 'handle', 'pointer'))
                    and not (key.lower().startswith('raw_') and key != 'raw_fields')
                    and not any(part in key.lower() for part in ('api_key', 'apikey', 'credential', 'password', 'secret', 'access_token', 'refresh_token'))
                    and not (key == 'address' and type(item) is int)
                    and not isinstance(item, (bytes, bytearray, memoryview))}
        if isinstance(value, (list, tuple)):
            return [public(item) for item in value if not isinstance(item, (bytes, bytearray, memoryview))]
        if isinstance(value, str):
            return re.sub(r'(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|tmp|var|etc)/)[^\s\"<>|]*', '[private path]', value)
        if value is None or isinstance(value, (bool, int, float)):
            return deepcopy(value)
        return None
    summary = public({key: value for key, value in result.items() if key in fields})
    if summary.get('mode') == 'live':
        summary['gx_compile_status'] = (summary.get('compile') or {}).get('status', 'not_run')
        summary['simulation_status'] = 'not_run'
    return summary


def verified_live_data(result, baseline):
    """Recheck the actual saved stage before the application version transaction."""
    from gxw.live_context import verify_active_saved_context
    if (not isinstance(result, dict) or result.get('passed') is not True
            or result.get('data_status') != 'verified_stage_save' or not isinstance(result.get('data'), bytes)
            or not 0 < len(result['data']) <= 30 * 1024 * 1024
            or type(result.get('data_revision')) is not int or result['data_revision'] < 0
            or not isinstance(result.get('_stage_context'), dict)):
        raise ValueError('The native call has no verified stage-save data')
    stages = result.get('stage_saves')
    if not isinstance(stages, list) or not stages:
        raise ValueError('The native stage save has no operation receipt')
    stage = stages[-1]
    matching = [step for step in result.get('executed_steps', [])
                if step.get('index') == stage.get('sequence') and step.get('operation') == 'save'
                and step.get('status') == 'completed' and step.get('revision') == result['data_revision']]
    if (len(matching) != 1 or stage.get('revision') != result['data_revision']
            or not isinstance(stage.get('validation'), dict) or not stage['validation']
            or matching[0].get('verification', {}).get('validation') != stage['validation']):
        raise ValueError('Saved native bytes are not bound to this call and source revision')
    identity = result['_stage_context'].get('identity', {})
    if any(identity.get(key) != result.get('identity', {}).get(key)
           for key in ('process_id', 'software_version', 'cpu_menu', 'native_ui_language', 'project_codepage')):
        raise ValueError('Saved native stage belongs to another process, CPU/version, language or project codepage')
    verify_active_saved_context(result['data'], result['_stage_context'], baseline,
                                compilation=result.get('_stage_compile'))
    return result['data']


def normalize_live_plan(plan, *, plc_model=None):
    """Validate syntax first; a selected CPU binds only exact menu metadata.

    Acceptance here is not evidence that any native page operation is verified.
    The bound driver supplies current version/language operation capabilities.
    """
    from .native_engineering import _normalize_native_syntax
    from gxw.visible_graph import validate_graph_actions
    if plc_model is not None:
        from plc.target_support import require_native_program_target
        require_native_program_target(plc_model)
    fields = {'schema_version', 'mode', 'session_action', 'session_id', 'commands', 'controls'}
    if (not isinstance(plan, dict) or set(plan) - fields or plan.get('mode') != 'live'
            or type(plan.get('schema_version')) is not int or plan['schema_version'] != 1
            or not isinstance(plan.get('commands'), list) or len(plan['commands']) > 256):
        raise ValueError('A live plan requires schema_version 1, mode live and bounded commands')
    action = plan.get('session_action', 'start')
    if action not in ('start', 'continue', 'end', 'rebind'):
        raise ValueError('Unknown native session action')
    if action == 'start':
        if 'session_id' in plan:
            raise ValueError('A new session receives its ID from the application')
    elif not isinstance(plan.get('session_id'), str) or not re.fullmatch(r'[a-f0-9]{32}', plan['session_id']):
        raise ValueError('Continuing, ending or rebinding requires the actual native session ID')
    if action in ('end', 'rebind') and plan['commands']:
        raise ValueError('Session lifecycle actions cannot include engineering mutations')
    controls = plan.get('controls', {})
    limits = {'dwell_ms': (0, 60000), 'max_steps': (1, 256), 'expected_revision': (0, 2**31-1)}
    flags = {'step_mode', 'resume_pending'}
    if not isinstance(controls, dict) or set(controls) - set(limits) - flags:
        raise ValueError('Unknown native pacing or revision controls')
    for name, value in controls.items():
        if name in flags and type(value) is not bool:
            raise ValueError('Native step controls require booleans')
        if name in limits and (type(value) is not int or not limits[name][0] <= value <= limits[name][1]):
            raise ValueError('Native pacing/revision control is outside its supported bounds')
    if action == 'continue' and 'expected_revision' not in controls:
        raise ValueError('Continuing a native session requires its current expected_revision')
    if controls.get('resume_pending') and plan['commands']:
        raise ValueError('Resume the existing pending steps before supplying a new command list')
    for command in plan['commands']:
        if (not isinstance(command, dict)
                or command.get('operation') not in (set(_PRODUCT_FIELDS) - {'create_structure'}) | {'delete_label'}):
            raise ValueError('Unsupported live engineering operation')
        operation = command['operation']
        if operation == 'delete_label':
            if (set(command) != {'operation', 'scope', 'source_name', 'name'}
                    or command.get('scope') != 'program'
                    or any(not isinstance(command[key], str) or not command[key].strip()
                           or '\0' in command[key] for key in ('source_name', 'name'))):
                raise ValueError('Live label deletion requires an explicit Program owner and one unused ordinary label')
            from gxw.fb_authoring import _name
            _name(command['source_name'], 65001)
            _name(command['name'], 65001)
        elif operation == 'show_assignments':
            if set(command) != {'operation', 'queries'} or not isinstance(command['queries'], list) or not 1 <= len(command['queries']) <= 32:
                raise ValueError('Address display needs 1 to 32 explicit current-scope queries')
            seen = set()
            for query in command['queries']:
                if (not isinstance(query, dict) or set(query) != {'scope', 'source', 'name'}
                        or query['scope'] not in ('global', 'local', 'instance')
                        or any(not isinstance(query[key], str) or not query[key].strip() for key in ('source', 'name'))):
                    raise ValueError('Native address queries need explicit scope, owner and variable name')
                key = tuple(query[name] for name in ('scope', 'source', 'name'))
                if key in seen:
                    raise ValueError('Duplicate current native address query')
                seen.add(key)
        elif operation == 'edit_fbd_graph':
            if set(command) != {'operation', 'caller', 'actions'} or not isinstance(command['caller'], str):
                raise ValueError('A live graph edit needs its explicit caller and actions')
            if not isinstance(command['actions'], list) or not 1 <= len(command['actions']) <= 128:
                raise ValueError('A live graph command needs 1 to 128 actions')
            for graph_action in command['actions']:
                if (not isinstance(graph_action, dict) or type(graph_action.get('network')) is not int
                        or not 0 <= graph_action['network'] <= 511):
                    raise ValueError('Each live graph action requires an explicit network')
                if graph_action.get('action') in ('move_node', 'add_wire') and graph_action['network'] != 0:
                    raise ValueError('Coordinate graph editing is verified only for Program network zero')
                if graph_action.get('action') == 'add_wire' and any(
                        not isinstance(graph_action.get(key), dict) or set(graph_action[key]) != {'node', 'port'}
                        for key in ('from', 'to')):
                    raise ValueError('Live wire endpoints require explicit current node ports')
                if graph_action.get('action') == 'add_terminal':
                    from gxw.active_graph import validate_add_terminal_action
                    validate_add_terminal_action(graph_action)
                else:
                    validate_graph_actions([{key: value for key, value in graph_action.items() if key != 'network'}])
        else:
            # This shared validator does not resolve objects or require a
            # prior call's compilation. Dependencies are read in that step.
            _normalize_native_syntax({'schema_version': 1, 'commands': [command]},
                                     plc_model=plc_model, live=True)
    try:
        encoded = json.dumps(plan, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError('Native plans must contain valid JSON values') from error
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError('Native engineering plan is too large')
    return {**deepcopy(plan), 'session_action': action, 'controls': deepcopy(controls)}


class LiveEngineeringSession:
    def __init__(self, driver, expected_cpu, baseline, session_id, project_id, base_version_id,
                 progress=None, *, live_driver=None, operation_authorizer=None):
        if operation_authorizer is not None and not callable(operation_authorizer):
            raise ValueError('An internal native operation authorizer must be callable')
        if live_driver is None:
            from gxworks2.live_driver import LiveEngineeringDriver
            live_driver = LiveEngineeringDriver(driver, baseline, expected_cpu)
        self.driver, self.live = driver, live_driver
        self.expected_cpu, self.baseline = expected_cpu, bytes(baseline)
        self.session_id, self.project_id, self.base_version_id = session_id, project_id, base_version_id
        self._raw_identity = deepcopy(driver.inspect())
        self.progress, self._identity = progress, _identity(self._raw_identity, expected_cpu)
        self._guard, self._pause = threading.RLock(), threading.Event()
        self._operation_guard = threading.Lock()
        self._context, self._pending, self._inflight = None, [], None
        self.revision, self.saved_revision, self.sequence = 0, 0, 0
        self.version_revision, self._pending_version_revision = 0, None
        self.native_disk_save = {'status': 'baseline', 'revision': 0}
        self.state, self.closed = 'ready', False
        self.compile = {'status': 'not_run', 'revision': None, 'execution': 'not_run'}
        self.assignments = {'status': 'not_run', 'rows': []}
        self.save = {'status': 'not_run', 'revision': None}
        self.history, self.last_error = [], None
        self._compiled_save = None
        self._operation_authorizer = operation_authorizer
        self._capability_authorization = {'status': 'not_checked', 'operations': []}

    def _check_identity(self):
        if _identity(self.live.inspect(), self.expected_cpu) != self._identity:
            raise RuntimeError('The bound native process, document project, CPU, software version, language or project codepage changed')

    def _operation_capabilities(self):
        from gxw.native_locale import native_language
        from plc.target_support import DEFAULT_TARGET_SUPPORT
        return DEFAULT_TARGET_SUPPORT.live_capabilities(self._identity['cpu_menu'],
            software_version=self._identity['software_version'], native_ui_language=native_language(self._identity))

    def _authorize_operation(self, command):
        """A private acceptance policy permits a trial without creating evidence.

        Product construction supplies no callback. A scoped acceptance harness
        may inject this synchronous policy in Python; domain plans and MCP have
        no such option. Policy inputs are detached from the actual bound state.
        """
        from gxw.native_locale import native_language
        from plc.target_support import LIVE_OPERATIONS
        operation = command.get('operation')
        authorization = dict(operation=operation, cpu_menu=self._identity['cpu_menu'],
            software_version=self._identity['software_version'], native_ui_language=native_language(self._identity))
        if operation not in LIVE_OPERATIONS:
            authorization.update(status='rejected', reason='unknown_live_operation')
            raise LiveCapabilityError('Unknown native live operation; no command was dispatched', authorization)
        if self._operation_authorizer is not None:
            try:
                accepted = self._operation_authorizer(deepcopy(self._identity), deepcopy(command))
            except Exception as error:
                authorization.update(status='rejected', reason='internal_acceptance_authorizer_failed')
                raise LiveCapabilityError('The isolated native acceptance policy failed before authorizing this trial',
                                          authorization) from error
            if (not isinstance(accepted, dict) or set(accepted) != {'status', 'reason', 'scope'}
                    or accepted.get('status') != 'experimental_allowed'
                    or any(not isinstance(accepted.get(key), str) or not accepted[key].strip()
                           or len(accepted[key]) > 1024 or '\0' in accepted[key] for key in ('reason', 'scope'))):
                authorization.update(status='rejected', reason='invalid_internal_acceptance_authorization')
                raise LiveCapabilityError('The isolated native acceptance policy did not explicitly authorize this trial', authorization)
            return {**authorization, **deepcopy(accepted)}
        capability = self._operation_capabilities()[operation]
        if capability.get('status') != 'verified':
            authorization.update(status='rejected', capability_status=capability.get('status', 'unverified'),
                                 reason=capability.get('reason', 'exact_target_language_evidence_required'))
            raise LiveCapabilityError('Native operation ' + operation + ' requires verified evidence for this exact CPU, version and UI language',
                                      authorization)
        return {**authorization, 'status': 'verified', 'evidence': deepcopy(capability['evidence'])}

    def _authorize_work(self, work):
        records = []
        with self._guard:
            self._capability_authorization = {'status': 'not_required' if not work else 'checking', 'operations': []}
        try:
            for command in work:
                records.append(self._authorize_operation(command))
        except LiveCapabilityError as error:
            records.append(deepcopy(error.authorization))
            with self._guard:
                self._capability_authorization = {'status': 'rejected', 'operations': records}
            raise
        if records:
            with self._guard:
                self._capability_authorization = {'status': 'experimental_allowed' if self._operation_authorizer is not None else 'verified',
                                                  'operations': records}

    @staticmethod
    def _state(context):
        from gxw.live_context import live_context_state
        return live_context_state(context)

    @staticmethod
    def _meaning(context):
        from gxw.live_context import live_context_meaning
        return live_context_meaning(context)

    @staticmethod
    def _unchanged(before, after):
        from gxw.live_context import verify_live_observations
        return verify_live_observations(before, after)

    @staticmethod
    def _command_steps(commands):
        # Each graph action resolves identities again after its predecessor.
        # A whole action array never borrows the first action's native IDs.
        work = []
        for command in commands:
            if command.get('operation') == 'edit_fbd_graph':
                work.extend({**deepcopy(command), 'actions': [deepcopy(action)]}
                            for action in command['actions'])
            else:
                work.append(deepcopy(command))
        return work

    def _emit(self, value):
        if self.progress:
            self.progress(deepcopy(value))

    def request_pause(self):
        self._pause.set()
        return {'session_id': self.session_id, 'status': 'pause_requested', 'boundary': 'after_current_step'}

    def set_base_version(self, version_id):
        with self._guard:
            self.base_version_id = version_id
            if self._pending_version_revision is not None:
                self.version_revision = self._pending_version_revision
                self._pending_version_revision = None

    def status(self):
        with self._guard:
            result = {'mode': 'live', 'session_id': self.session_id, 'project_id': self.project_id,
                'base_version_id': self.base_version_id, 'status': self.state, 'revision': self.revision,
                'saved_revision': self.saved_revision, 'dirty': self.revision != self.saved_revision,
                'version_revision': self.version_revision,
                'version_matches_active_source': self.version_revision == self.revision,
                'native_disk_save': deepcopy(self.native_disk_save),
                'pending_commands': len(self._pending), 'pause_requested': self._pause.is_set(),
                'compile': deepcopy(self.compile), 'assignments': deepcopy(self.assignments),
                'save': deepcopy(self.save), 'steps': deepcopy(self.history[-256:]),
                'capability_authorization': deepcopy(self._capability_authorization),
                'identity': {key: self._identity[key] for key in ('process_id', 'software_version', 'cpu_menu',
                    'native_ui_language', 'project_codepage') if key in self._identity}}
            for key in ('native_ui_language', 'project_codepage'):
                if key in self._identity:
                    result[key] = self._identity[key]
            result['operation_capabilities'] = deepcopy(self._operation_capabilities())
            if self.last_error:
                result['error'] = deepcopy(self.last_error)
            if self._context is not None:
                result['current_context'] = self._state(self._context)
            return result

    def _changed(self):
        with self._guard:
            self.revision += 1
            self._compiled_save = None
            self.compile = {'status': 'stale' if self.compile['status'] != 'not_run' else 'not_run',
                            'revision': self.compile.get('revision'), 'execution': 'not_run'}
            self.assignments = {'status': 'stale' if self.assignments['status'] != 'not_run' else 'not_run', 'rows': []}
            self.save = {'status': 'stale' if self.save['status'] == 'passed' else 'not_run',
                         'revision': self.save.get('revision')}

    @_exclusive_operation
    def run(self, commands, controls=None):
        from gxw.live_context import prepare_live_command, verify_live_change
        controls = controls or {}
        result = {'mode': 'live', 'session_id': self.session_id, 'steps': [], 'passed': False}
        if self.closed or self.state == 'unconfirmed':
            raise RuntimeError('This native session is closed or needs reconciliation before another write')
        if controls.get('expected_revision', self.revision) != self.revision:
            raise ValueError('The native source revision changed; read the current session before editing')
        if self._pending:
            if not controls.get('resume_pending') or commands:
                raise ValueError('The native session has paused commands; explicitly resume that existing sequence')
            work = deepcopy(self._pending)
        else:
            if controls.get('resume_pending'):
                raise ValueError('There is no paused native sequence to resume')
            work = self._command_steps(commands)
        self._pause.clear()
        limit = 1 if controls.get('step_mode') else controls.get('max_steps', 256)
        with self._guard:
            self.state, self.last_error = 'running', None
        completed, saved_data, stage_context, stage_saves = 0, None, None, []
        try:
            self._check_identity()
            self._authorize_work(work)
            if hasattr(self.live, 'finish_completion'):
                self.live.finish_completion()
            if not work:
                observed = self.live.read_context()
                observed['_revision'] = self.revision
                if self._context is not None:
                    self._unchanged(self._context, observed)
                else:
                    from gxw.live_context import verify_active_saved_context
                    verify_active_saved_context(self.baseline, observed, self.baseline)
                with self._guard:
                    self._context = observed
            with self._guard:
                self._pending = deepcopy(work)
            for index, command in enumerate(work):
                if completed >= limit or self._pause.is_set():
                    with self._guard:
                        self.state = 'paused'
                    break
                operation = command['operation']
                with self._guard:
                    self.sequence += 1
                step = {'index': self.sequence, 'operation': operation, 'status': 'executing', 'revision': self.revision,
                        'target': {key: deepcopy(command[key]) for key in
                            ('source_name', 'scope', 'name', 'caller', 'instance', 'definition', 'actions', 'queries')
                            if key in command}}
                result['steps'].append(step)
                self._emit(step)
                self._check_identity()
                step['capability_authorization'] = self._authorize_operation(command)
                if hasattr(self.live, 'finish_completion'):
                    self.live.finish_completion()
                before = (self.live.read_target_context(self._context, command)
                          if self._context is not None and hasattr(self.live, 'read_target_context')
                          else self.live.read_context())
                before['_revision'] = self.revision
                if self._context is not None:
                    self._unchanged(self._context, before)
                else:
                    from gxw.live_context import verify_active_saved_context
                    verify_active_saved_context(self.baseline, before, self.baseline)
                    with self._guard:
                        self._context = deepcopy(before)
                if hasattr(self.live, 'focus_target'):
                    focused = self.live.focus_target(command, before)
                    self._unchanged(before, focused)
                    before = focused
                prepared = (prepare_live_command(self.baseline, before, command, device_profile=self.expected_cpu)
                            if operation not in ('compile', 'show_assignments', 'save', 'reopen') else None)
                if operation == 'show_assignments' and (self.compile.get('status') != 'passed'
                        or self.compile.get('revision') != self.revision):
                    raise ValueError('Actual addresses require a successful compilation of this current revision')
                if operation == 'reopen' and (self.revision != self.saved_revision or self.save['status'] != 'passed'):
                    raise ValueError('Explicitly save this source revision before a representative reload')
                if operation == 'compile':
                    from gxw.live_context import live_compile_plan
                    native_compile_plan = live_compile_plan(self.baseline, self.revision)
                with self._guard:
                    self._inflight = {'command': deepcopy(command), 'prepared': prepared, 'before': before,
                                      'step': deepcopy(step)}
                observation = None
                if operation == 'compile':
                    with self._guard:
                        self._compiled_save = None
                        self.compile = dict(status='running', revision=self.revision, execution='not_run')
                        if self.assignments['status'] != 'not_run':
                            self.assignments = dict(status='stale', rows=[])
                    observation = self.driver.compile_current(native_compile_plan)
                    with self._guard:
                        self._inflight['observation'] = deepcopy(observation)
                    after = self.live.read_context()
                    from .native_engineering import compile_observation
                    self._unchanged(before, after)
                    compilation = compile_observation({**observation, 'source_unchanged': True},
                        process_id=self._identity['process_id'], revision=self.revision, require_saved=False)
                    compilation['compile_run_id'] = observation.get('compile_run_id')
                    compilation['origin'] = 'current_native_compile_and_active_source'
                    compilation.update(identity=deepcopy(after['identity']), source_unchanged=True,
                        public_checks_requested=observation.get('public_checks_requested'),
                        diagnostics=deepcopy(observation.get('diagnostics', {})))
                    with self._guard:
                        self.compile = compilation
                    verification = deepcopy(compilation)
                    if hasattr(self.live, 'read_disk_change'):
                        native_saved = self.live.read_disk_change()
                        if native_saved is not None:
                            after['_revision'] = self.revision
                            disk_validation = self.live.acknowledge_native_save(native_saved, after, compilation=self.compile)
                            with self._guard:
                                self.saved_revision = self.revision
                                self.native_disk_save = {'status': 'verified', 'revision': self.revision,
                                    'origin': 'native_compile', 'validation': disk_validation}
                                self._compiled_save = dict(data=native_saved, revision=self.revision,
                                    compile_run_id=self.compile.get('compile_run_id'))
                    # Compiler errors are a result of this source revision;
                    # they do not undo prior successful native edits.
                elif operation == 'show_assignments':
                    allocation_context = None
                    if (self._compiled_save is not None and self._compiled_save['revision'] == self.revision
                            and self._compiled_save['compile_run_id'] == self.compile.get('compile_run_id')):
                        from gxw.live_assignments import prepare_live_assignments
                        allocation_context = prepare_live_assignments(self._compiled_save['data'], command['queries'],
                            revision=self.revision, compile_run_id=self.compile['compile_run_id'],
                            identity=before['identity'])
                    observation = self.driver.read_assignments(command['queries'], revision=self.revision,
                        compilation=self.compile, allocation_context=allocation_context)
                    with self._guard:
                        self._inflight['observation'] = deepcopy(observation)
                    after = self.live.read_context()
                    self._unchanged(before, after)
                    with self._guard:
                        self.assignments = {**deepcopy(observation), 'revision': self.revision,
                                            'compile_run_id': self.compile['compile_run_id']}
                    verification = deepcopy(self.assignments)
                elif operation == 'save':
                    from uuid import uuid4
                    save_operation_id = uuid4().hex
                    with self._guard:
                        self._inflight['save_operation_id'] = save_operation_id
                    save_options = dict(operation_id=save_operation_id, compilation=self.compile)
                    if hasattr(self.live, 'read_target_context'):
                        save_options['_current_before'] = before
                    saved = self.live.stage_save(before, **save_options)
                    if (saved.get('operation_id') != save_operation_id or saved.get('save_dispatched') is not True):
                        raise ValueError('The stage save has no dispatch receipt for this operation')
                    with self._guard:
                        self._inflight['saved'] = saved
                    saved_data = saved['data']
                    stage_context = deepcopy(before)
                    after = deepcopy(saved['_stage_context'])
                    self._unchanged(before, after)
                    if hasattr(self.live, 'read_disk_change') and self.live.read_disk_change() is not None:
                        raise ValueError('The native Save file changed during final readback; reconcile its original dispatch')
                    with self._guard:
                        self.saved_revision = self.revision
                        self.native_disk_save = {'status': 'verified', 'revision': self.revision, 'origin': 'stage_save'}
                        self.save = {'status': 'passed', 'revision': self.revision, 'operation_id': save_operation_id,
                                     'validation': deepcopy(saved['validation'])}
                        if self.compile.get('status') == 'passed' and self.compile.get('revision') == self.revision:
                            self._compiled_save = dict(data=saved_data, revision=self.revision,
                                                      compile_run_id=self.compile['compile_run_id'])
                    stage_saves.append({'revision': self.revision, 'sequence': self.sequence,
                                        'operation_id': save_operation_id,
                                        'validation': deepcopy(saved['validation'])})
                    verification = deepcopy(self.save)
                elif operation == 'reopen':
                    self.driver.reopen()
                    with self._guard:
                        self._inflight['reopened'] = True
                    after = self.live.read_context()
                    self._unchanged(before, after)
                    native_saved = self.live.read_disk_change() if hasattr(self.live, 'read_disk_change') else None
                    if native_saved is not None:
                        after['_revision'] = self.revision
                        validation = self.live.acknowledge_native_save(native_saved, after, compilation=self.compile)
                        with self._guard:
                            self.native_disk_save = dict(status='verified', revision=self.revision,
                                origin='explicit_reopen', validation=validation)
                    verification = {'status': 'matched', 'reopen': 'same_process', 'revision': self.revision}
                else:
                    observation = self.live.apply(command, prepared)
                    after = self.live.read_context()
                    verification = verify_live_change(before, after, command, prepared)
                    if verification.get('status') not in ('matched', 'verified'):
                        raise RuntimeError('The requested native edit has no confirmed Core readback')
                self._check_identity()
                changed = prepared is not None and self._meaning(before) != self._meaning(after)
                with self._guard:
                    if changed:
                        self._changed()
                    after['_revision'] = self.revision
                    self._context, self._inflight = after, None
                    self._pending = deepcopy(work[index+1:])
                    step.update(status='completed', revision=self.revision, verification=deepcopy(verification))
                    self.history.append(deepcopy(step))
                if hasattr(self.live, 'show_completion'):
                    step['visible_completion'] = self.live.show_completion(command, prepared,
                        observation if operation != 'save' else verification)
                    with self._guard:
                        self.history[-1] = deepcopy(step)
                completed += 1
                self._emit(step)
                dwell = controls.get('dwell_ms', 0) / 1000
                if dwell:
                    self._pause.wait(dwell)
            else:
                with self._guard:
                    self.state = 'ready'
            executed = deepcopy(result['steps'])
            result.update(self.status(), passed=True, status='paused' if self._pending else 'completed',
                          executed_steps=executed, stage_saves=stage_saves)
            if saved_data is not None:
                data_revision = stage_saves[-1]['revision']
                with self._guard:
                    self._pending_version_revision = data_revision
                result.update(data=saved_data, data_status='verified_stage_save', data_revision=data_revision,
                              _stage_context=stage_context, _stage_compile=deepcopy(saved.get('_stage_compile')))
        except Exception as error:
            # Never send an engineering action again to discover whether the
            # first one completed. Rebinding inspects the same current target.
            with self._guard:
                self.state = 'unconfirmed' if self._inflight is not None else 'failed'
                self.last_error = {'type': type(error).__name__, 'message': str(error)}
                if isinstance(error, LiveCapabilityError):
                    self._capability_authorization['status'] = 'rejected'
                    self.last_error['capability_authorization'] = deepcopy(error.authorization)
                    if result['steps']:
                        result['steps'][-1]['capability_authorization'] = deepcopy(error.authorization)
                self._private_native_error = deepcopy(getattr(error, 'observation', None))
                if result['steps'] and result['steps'][-1]['status'] == 'executing':
                    result['steps'][-1].update(status='unconfirmed' if self._inflight else 'failed',
                                               message=str(error))
                    self.history.append(deepcopy(result['steps'][-1]))
            executed = deepcopy(result['steps'])
            result.update(self.status(), passed=False, executed_steps=executed, stage_saves=stage_saves)
        return result

    @_exclusive_operation
    def rebind(self):
        pending = self.live.pending_operations()
        if any(row.get('status') == 'running' for row in pending):
            return {**self.status(), 'status': 'unconfirmed', 'native_operations': pending, 'replayed': False}
        self._check_identity()
        current = self.live.rebind(self._raw_identity)
        current['_revision'] = self.revision
        recovered_data = None
        applied = changed = False
        if self._inflight is not None:
            from gxw.live_context import verify_live_change
            inflight = self._inflight
            operation = inflight['command']['operation']
            try:
                if inflight['prepared'] is None:
                    verification, recovered_data = self._reconcile_auxiliary(inflight, current)
                else:
                    verification = verify_live_change(inflight['before'], current, inflight['command'], inflight['prepared'])
                if verification.get('status') not in ('matched', 'verified', 'passed', 'failed', 'current', 'partial', 'unavailable'):
                    raise ValueError('The interrupted operation has no confirmed result')
            except Exception:
                if inflight['prepared'] is None:
                    return {**self.status(), 'status': 'unconfirmed', 'passed': False, 'replayed': False,
                            'current_context': self._state(current),
                            'error': {'type': 'ReconciliationRequired',
                                      'message': 'Source is observable; this interrupted operation still lacks its actual completion receipt'}}
                try:
                    self._unchanged(inflight['before'], current)
                except ValueError:
                    return {**self.status(), 'status': 'unconfirmed', 'replayed': False,
                            'current_context': self._state(current)}
                verification = {'status': 'not_applied', 'replayed': False}
            else:
                applied = True
                changed = self._meaning(current) != self._meaning(inflight['before'])
        elif self._context is not None:
            try:
                self._unchanged(self._context, current)
            except ValueError:
                return {**self.status(), 'status': 'unconfirmed', 'passed': False, 'replayed': False}
        with self._guard:
            if self._inflight is not None:
                if changed:
                    self._changed()
                if applied and self._pending:
                    self._pending.pop(0)
                reconciled = {**inflight['step'], 'status': 'completed' if recovered_data is not None else 'reconciled',
                              'revision': self.revision, 'verification': verification, 'replayed': False}
                self.history.append(reconciled)
                self._inflight = None
            current['_revision'] = self.revision
            self._context, self.state, self.last_error = current, 'paused' if self._pending else 'ready', None
            if recovered_data is not None:
                self._pending_version_revision = self.revision
            result = {**self.status(), 'passed': True, 'replayed': False}
        if recovered_data is not None:
            result.update(data=recovered_data, data_status='verified_stage_save', data_revision=self.revision,
                _stage_context=deepcopy(current), executed_steps=[deepcopy(reconciled)],
                _stage_compile=deepcopy(self.compile),
                stage_saves=[dict(revision=self.revision, sequence=reconciled['index'],
                                  validation=deepcopy(self.save['validation']))])
        return result

    def _reconcile_auxiliary(self, inflight, current):
        """Recover observed completion without sending another native command."""
        self._unchanged(inflight['before'], current)
        operation, observed = inflight['command']['operation'], inflight.get('observation')
        if operation == 'compile':
            from .native_engineering import compile_observation
            if observed is None and hasattr(self.driver, 'read_current_compile_result'):
                observed = self.driver.read_current_compile_result(revision=self.revision)
            if not isinstance(observed, dict) or not observed.get('compile_run_id'):
                raise ValueError('The original native compile has no completed run receipt')
            verified = compile_observation({**observed, 'source_unchanged': True},
                process_id=self._identity['process_id'], revision=self.revision, require_saved=False)
            if verified['status'] not in ('passed', 'failed'):
                raise ValueError('The original native compile has not completed')
            with self._guard:
                self.compile = {**verified, 'compile_run_id': observed['compile_run_id'],
                                'origin': 'current_native_compile_and_active_source',
                                'identity': deepcopy(current['identity']), 'source_unchanged': True,
                                'public_checks_requested': observed.get('public_checks_requested'),
                                'diagnostics': deepcopy(observed.get('diagnostics', {}))}
            raw = self.live.read_disk_change()
            if raw is not None:
                validation = self.live.acknowledge_native_save(raw, current, compilation=self.compile)
                with self._guard:
                    self.saved_revision = self.revision
                    self.native_disk_save = dict(status='verified', revision=self.revision,
                        origin='native_compile', validation=validation)
                    self._compiled_save = dict(data=raw, revision=self.revision,
                        compile_run_id=observed['compile_run_id'])
            return deepcopy(self.compile), None
        if operation == 'show_assignments':
            if not isinstance(observed, dict) or self.compile.get('revision') != self.revision:
                raise ValueError('The interrupted address query has no bound observation')
            with self._guard:
                self.assignments = {**deepcopy(observed), 'revision': self.revision,
                                    'compile_run_id': self.compile.get('compile_run_id')}
            return deepcopy(self.assignments), None
        if operation == 'save':
            from gxw.live_context import verify_active_saved_context
            operation_id = inflight.get('save_operation_id')
            if not isinstance(operation_id, str) or not re.fullmatch(r'[a-f0-9]{32}', operation_id):
                raise ValueError('The interrupted Save has no bound operation identity')
            saved = inflight.get('saved') or {}
            returned = (saved.get('operation_id') == operation_id and saved.get('save_dispatched') is True
                        and isinstance(saved.get('data'), bytes) and isinstance(saved.get('validation'), dict))
            progress = self.live.stage_save_status(operation_id)
            if not returned and (progress.get('operation_id') != operation_id
                                 or progress.get('save_dispatched') is not True):
                raise ValueError('The interrupted Save has no native dispatch for this operation')
            # Native Save can finish after returning an initial snapshot.
            # Reconcile the latest stable bytes against the same current
            # source and original dispatch, never send Save a second time.
            latest = self.live.read_disk_change()
            raw = latest if latest is not None else saved['data'] if returned else None
            if not isinstance(raw, bytes):
                raise ValueError('The interrupted Save has no independently observed saved bytes')
            validation = verify_active_saved_context(raw, current, self.baseline, compilation=self.compile)
            self.live.acknowledge_native_save(raw, current, compilation=self.compile)
            with self._guard:
                self.saved_revision = self.revision
                self.native_disk_save = dict(status='verified', revision=self.revision, origin='reconciled_stage_save')
                self.save = dict(status='passed', revision=self.revision, operation_id=operation_id, validation=validation)
                if self.compile.get('status') == 'passed' and self.compile.get('revision') == self.revision:
                    self._compiled_save = dict(data=raw, revision=self.revision,
                        compile_run_id=self.compile['compile_run_id'])
            return deepcopy(self.save), raw
        if operation == 'reopen' and inflight.get('reopened'):
            raw = self.live.read_disk_change()
            if raw is not None:
                self.live.acknowledge_native_save(raw, current, compilation=self.compile)
            return dict(status='matched', revision=self.revision, reopen='same_process'), None
        raise ValueError('The interrupted native operation has no completion receipt')

    @_exclusive_operation
    def close(self):
        pending = self.live.pending_operations()
        if any(row.get('status') == 'running' for row in pending):
            raise RuntimeError('The original native child is still running; inspect it before ending the controller')
        self.live.close()
        self.driver.close()
        with self._guard:
            self.closed, self.state = True, 'ended'
        return {**self.status(), 'passed': True}
