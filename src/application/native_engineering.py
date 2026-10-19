"""One bounded visible engineering run on an explicitly selected native project.

The approved desktop execution owner supplies the driver and holds its resource
lock. This controller never grants consent, starts another workspace, or retries
an operation after a possible native side effect.
"""
from __future__ import annotations

import copy
import json
import ntpath
import re
from collections.abc import Mapping

from gxw.models import GXWFormatError
from gxw.visible_engineering import (AUTHORING_OPERATIONS, prepare_authoring,
    prepare_assignment_cross_references, prepare_fbd_call, saved_assignments, source_projection,
    verify_authoring_readback, verify_fbd_call, verify_native_cross_references)
from gxw.visible_graph import prepare_graph_edit, validate_graph_actions, verify_graph_edit


_IDENTITY_FIELDS = ('process_id', 'window_handle', 'process_started', 'executable',
                    'software_version', 'project_path', 'cpu_menu')
_AUXILIARY_OPERATIONS = frozenset({'compile', 'show_assignments', 'save', 'reopen'})

# The controller can be used by bounded research callers. Product dispatch is
# narrower: only the native CPU/menu and controls witnessed in this module.
_PRODUCT_FIELDS = {
    'create_global_labels': {'operation', 'source_name', 'declarations'},
    'edit_global_labels': {'operation', 'source_name', 'declarations'},
    'edit_pou_labels': {'operation', 'source_name', 'declarations'},
    'create_st_fb': {'operation', 'source_name', 'declarations', 'st_text'},
    'edit_st_fb': {'operation', 'source_name', 'declarations', 'st_text'},
    'create_st_program': {'operation', 'source_name', 'declarations', 'st_text', 'resource', 'task', 'scope_mode'},
    'edit_st_program': {'operation', 'source_name', 'declarations', 'st_text'},
    'create_structure': {'operation', 'source_name', 'members'},
    'configure_project': {'operation', 'source_name', 'options'},
    'create_fbd_call': {'operation', 'caller', 'instance', 'definition', 'arguments'},
    'edit_fbd_graph': {'operation', 'caller', 'actions'},
    'compile': {'operation'}, 'show_assignments': {'operation', 'queries'},
    'save': {'operation'}, 'reopen': {'operation'},
}


def normalize_native_plan(plan, *, plc_model=None):
    """Freeze a domain batch, with no process, path or driver option surface."""
    if plc_model is not None and str(plc_model).upper() != 'FX3U':
        raise ValueError('Visible native engineering is verified only for FX3U (FX3U/FX3UC native menu)')
    return _normalize_native_syntax(plan, plc_model=plc_model, live=False)


def _normalize_native_syntax(plan, *, plc_model=None, live=False):
    """Check command syntax separately from target selection and native evidence.

    The live controller resolves current objects and operation capabilities at
    execution time. Passing no CPU performs no family or model substitution.
    """
    profile = None
    if plc_model is not None:
        from plc.target_support import require_native_program_target
        profile = require_native_program_target(plc_model)
    if (not isinstance(plan, dict) or set(plan) != {'schema_version', 'commands'}
            or type(plan['schema_version']) is not int or plan['schema_version'] != 1
            or not isinstance(plan['commands'], list) or not 1 <= len(plan['commands']) <= 256):
        raise ValueError('A native engineering plan requires schema_version 1 and 1 to 256 commands')
    try:
        encoded = json.dumps(plan, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError('Native engineering commands must be JSON values') from error
    if len(encoded.encode('utf-8')) > 4 * 1024 * 1024:
        raise ValueError('Native engineering plan is too large')
    compiled, last_edit, last_layers = False, -1, {}
    for index, command in enumerate(plan['commands']):
        if (not isinstance(command, dict) or not isinstance(command.get('operation'), str)
                or command['operation'] not in _PRODUCT_FIELDS):
            raise ValueError('This native engineering operation is not verified for product execution')
        operation = command['operation']
        if set(command) - _PRODUCT_FIELDS[operation]:
            raise ValueError('Unknown native engineering command fields')
        required = _PRODUCT_FIELDS[operation] - ({'declarations'} if operation in ('edit_st_fb', 'edit_st_program') else set())
        if not required <= command.keys():
            raise ValueError('Native engineering command is missing required fields')
        for key in ('source_name', 'caller', 'instance', 'definition', 'st_text', 'resource', 'task'):
            if key in command and (not isinstance(command[key], str) or not command[key].strip()):
                raise ValueError('Native engineering source identities and ST text must be nonempty strings')
        if operation == 'create_st_program' and command['scope_mode'] != 'existing_task':
            raise ValueError('Visible ST Program creation requires an explicit existing_task scope')
        if operation == 'edit_fbd_graph':
            validate_graph_actions(command['actions'])
        rows = command.get('members') if operation == 'create_structure' else command.get('declarations')
        if 'declarations' in command or 'members' in command:
            if (not isinstance(rows, list) or len(rows) > 4096
                    or operation == 'create_structure' and not rows):
                raise ValueError('Native declarations must be a bounded list with nonempty structure members')
            fields = ({'name', 'data_type', 'initial_value', 'comment'} if operation == 'create_structure' else
                      {'name', 'data_type', 'class_name', 'kind', 'initial_value', 'comment', 'device', 'iec_address', 'member_devices'})
            if any(not isinstance(row, dict) or set(row) - fields or not {'name', 'data_type'} <= row.keys()
                    or not isinstance(row['name'], str) or not isinstance(row['data_type'], str) for row in rows):
                raise ValueError('Invalid explicit native declaration fields')
            from gxw.visible_engineering import validate_visible_initial_values
            validate_visible_initial_values(rows)
        if operation == 'create_fbd_call' and (not isinstance(command['arguments'], dict)
                or not command['arguments'] or len(command['arguments']) > 256
                or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v
                       for k, v in command['arguments'].items())):
            raise ValueError('FBD call arguments must bind explicit formal names to symbols')
        if operation == 'configure_project':
            options = command['options']
            if (not isinstance(options, dict) or set(options) != {'system_variable_ranges'}
                    or not isinstance(options['system_variable_ranges'], dict)
                    or not options['system_variable_ranges']
                    or (not live and set(options['system_variable_ranges']) - {'D', 'M'})):
                raise ValueError('Visible allocation requires explicit device ranges; program capacity is unverified')
            for pair in options['system_variable_ranges'].values():
                if not isinstance(pair, list) or len(pair) != 2 or any(type(value) is not int for value in pair):
                    raise ValueError('Visible allocation requires explicit integer bounds')
            if any(not isinstance(device, str) or device not in {'D', 'R', 'W', 'T', 'ST', 'C', 'M', 'B', 'P', 'S', 'T10'}
                   for device in options['system_variable_ranges']):
                raise ValueError('Visible allocation requires explicit native device classes')
            from gxw.fb_authoring import validate_configuration_ranges
            validate_configuration_ranges(options['system_variable_ranges'],
                cpu=profile.cpu if profile else '', known_devices=options['system_variable_ranges'])
            if live and profile is not None:
                from gxw.project_metadata import validate_project_allocation_ranges
                validate_project_allocation_ranges(options['system_variable_ranges'], cpu=profile.cpu)
        if operation == 'show_assignments':
            queries = command['queries']
            if not compiled or not isinstance(queries, list) or not 1 <= len(queries) <= 32:
                raise ValueError('Address queries require an earlier current compile and 1 to 32 explicit global queries')
            seen = set()
            for query in queries:
                if (not isinstance(query, dict) or set(query) != {'scope', 'source', 'name'}
                        or query['scope'] != 'global' or any(not isinstance(query[k], str) or not query[k]
                                                           for k in ('source', 'name'))):
                    raise ValueError('Visible address queries require explicit global source and name')
                identity = (query['source'], query['name'])
                if identity in seen:
                    raise ValueError('Duplicate native address query')
                seen.add(identity)
        elif operation == 'compile':
            compiled = True
        elif operation not in _AUXILIARY_OPERATIONS:
            compiled = False
            last_edit = index
        if operation in {'compile', 'show_assignments', 'save', 'reopen'}:
            last_layers[operation] = index
    # Earlier observations retain their own revision. Only the latest requested
    # layer must still describe the final source delivered by this batch.
    if any(index < last_edit for index in last_layers.values()):
        raise ValueError('Requested final verification layers must follow the last source edit; repeat compile, address display, save or reopen after editing')
    return copy.deepcopy(plan)


def verified_native_data(result, plan):
    """Require completed revision-bound steps and current final layers to persist."""
    if (not isinstance(result, dict) or result.get('status') != 'completed' or result.get('passed') is not True
            or result.get('data_status') != 'last_verified_source'
            or not isinstance(result.get('data'), bytes) or not result['data']
            or 'unverified_data' in result):
        raise ValueError('Native engineering did not complete with verified saved source')
    commands, steps = plan['commands'], result.get('steps')
    revision = result.get('revision')
    if (type(revision) is not int or revision < 0 or not isinstance(steps, list) or len(steps) != len(commands)
            or any(not isinstance(step, dict) or step.get('operation') != command['operation']
                   or step.get('status') != 'completed' or (step.get('verification') or {}).get('status') != 'matched'
                   or type(step.get('revision')) is not int or not 0 <= step['revision'] <= revision
                   for command, step in zip(commands, steps))):
        raise ValueError('Native engineering step verification is incomplete')
    requested = {command['operation'] for command in commands}
    last_steps = {step['operation']: step for step in steps}
    for operation in ('compile', 'save', 'reopen'):
        if operation in requested:
            layer = result.get(operation) or {}
            if (layer.get('status') != 'passed' or layer.get('revision') != revision
                    or last_steps[operation]['revision'] != revision):
                raise ValueError('A requested native engineering layer is stale or incomplete')
    if 'compile' in requested and (result['compile'].get('errors') != 0
            or type(result['compile'].get('errors')) is not int):
        raise ValueError('The current native compile has no verified zero-error result')
    if 'show_assignments' in requested:
        layer = result.get('assignments') or {}
        if (layer.get('status') != 'current' or layer.get('revision') != revision
                or layer.get('origin') != 'current_gui_compile_saved_snapshot'
                or (layer.get('native_cross_references') or {}).get('status') != 'matched'):
            raise ValueError('The requested native address mapping is not current')
        compiled_revision, previous_revision, last_references = None, 0, None
        for command, step in zip(commands, steps):
            current_revision = step['revision']
            if current_revision < previous_revision:
                raise ValueError('Native engineering step revisions are out of order')
            operation = command['operation']
            if operation == 'compile':
                compiled_revision = current_revision
            elif operation not in _AUXILIARY_OPERATIONS:
                compiled_revision = None
            if operation == 'show_assignments':
                references = (step['verification'].get('native_cross_references') or {})
                if (current_revision != compiled_revision or references.get('status') != 'matched'
                        or [row.get('query') for row in references.get('queries', [])] != command['queries']):
                    raise ValueError('A requested native cross-reference query was not verified')
                last_references = references
            previous_revision = current_revision
        if (last_steps['show_assignments']['revision'] != revision
                or layer['native_cross_references'] != last_references):
            raise ValueError('The requested final native address mapping is stale or inconsistent')
    return result['data']


def native_engineering_summary(result):
    """Whitelist public states and counts; never publish native observations."""
    result = result if isinstance(result, dict) else {}
    statuses = {'not_run', 'unverified', 'stale', 'passed', 'failed', 'current', 'unavailable'}
    findings = {row.get('kind'): row.get('status') for row in result.get('findings', [])
                if isinstance(row, dict)} if isinstance(result.get('findings'), list) else {}
    def status(layer):
        observed = result.get(layer)
        if isinstance(observed, dict):
            value = observed.get('status')
            if (value in {'passed', 'current'} and type(result.get('revision')) is int
                    and observed.get('revision') != result['revision']):
                return 'stale'
        else:
            value = (result.get('gx_compile_status') if layer == 'compile'
                     else findings.get({'save': 'native_save', 'reopen': 'cold_reopen'}.get(layer)))
        return value if value in statuses else 'unverified'
    steps = result.get('steps') if isinstance(result.get('steps'), list) else result.get('changes', [])
    steps = steps if isinstance(steps, list) else []
    changes = [{'operation': row['operation'], 'status': row.get('status')
                if row.get('status') in {'executing', 'completed', 'failed', 'interrupted'} else 'unverified'}
               for row in steps[:256] if isinstance(row, dict) and row.get('operation') in _PRODUCT_FIELDS]
    public_counts = result.get('counts') if isinstance(result.get('counts'), dict) else {}
    compilation = result.get('compile') if isinstance(result.get('compile'), dict) else {
        source: public_counts.get(target) for source, target in
        (('errors', 'error'), ('warnings', 'warning'), ('check_warnings', 'info'))}
    counts = {'completed': sum(row['status'] == 'completed' for row in changes), 'total': len(changes)}
    for source, target in (('errors', 'error'), ('warnings', 'warning'), ('check_warnings', 'info')):
        value = compilation.get(source)
        if type(value) is int and value >= 0:
            counts[target] = value
    passed = result.get('status') == 'completed' and result.get('passed') is True
    assignments = result.get('assignments')
    references = (assignments.get('native_cross_references') or {}) if isinstance(assignments, dict) else {}
    reference_status = references.get('status') if isinstance(assignments, dict) else findings.get('native_cross_references')
    if isinstance(assignments, dict) and status('assignments') != 'current':
        reference_status = 'unverified'
    return {'status': 'completed' if passed else 'interrupted', 'passed': passed,
        'operation': 'native_engineering', 'counts': counts, 'changes': changes,
        'gx_compile_status': status('compile'), 'simulation_status': 'not_run',
        'message': '原生工程步骤已完成并核对保存源码。' if passed else '原生工程会话未完成；当前活动版本保持不变，请核对原生工程后重新提出方案。',
        'findings': [{'kind': 'native_save', 'status': status('save')},
                     {'kind': 'cold_reopen', 'status': status('reopen')},
                     {'kind': 'native_cross_references', 'status': 'matched' if reference_status == 'matched' else 'unverified'},
                     {'kind': 'execution_correspondence', 'status': 'unverified'}]}


def _project_projection(raw):
    """Compose existing Core source, GUI configuration and task observations."""
    from gxw.native_write import native_task_scope_context
    from gxw.project_metadata import project_gui_configuration
    result = source_projection(raw)
    result['gui_configuration'] = project_gui_configuration(raw)
    result['task_scopes'] = native_task_scope_context(raw)
    if result['task_scopes']['status'] != 'available':
        raise GXWFormatError('The current native resource/task context is unavailable')
    return result


def validate_native_baseline(raw, *, plc_model, live=False):
    """Bind a saved baseline before choosing live observation or offline writes.

    A live owner will re-read actual native allocation pages. An unobserved
    non-FX3U GUI mirror therefore stays explicitly unverified at this structural
    boundary. Offline writes still require a verified saved mirror, and a known
    FX3U mirror must be initialized and consistent in either mode.
    """
    from plc.target_support import require_native_program_target
    if type(live) is not bool:
        raise ValueError('The native baseline observation mode must be boolean')
    profile = require_native_program_target(plc_model)
    projection = _project_projection(raw)
    actual = require_native_program_target(projection['cpu'])
    if actual.cpu != profile.cpu:
        raise ValueError('The saved engineering CPU differs from the selected native CPU menu')
    gui = projection['gui_configuration']
    unobserved_live_mirror = (live and actual.cpu != 'FX3U/FX3UC'
        and gui.get('status') == 'not_verified'
        and gui.get('gui_mirror_status') in ('unverified', 'inactive_host_values_observed'))
    if gui.get('status') != 'matched' and not unobserved_live_mirror:
        raise ValueError('The saved GUI allocation configuration is uninitialized or inconsistent; apply and save it in GX Works2 before submitting this session')
    return projection


def _identity(value, expected_cpu):
    from gxworks2.project_profiles import native_project_profile
    if not isinstance(value, Mapping) or any(key not in value for key in _IDENTITY_FIELDS):
        raise ValueError('The visible engineering target identity is incomplete')
    result = {key: value[key] for key in _IDENTITY_FIELDS}
    if (any(type(result[key]) is not int or result[key] <= 0 for key in ('process_id', 'window_handle'))
            or not result['process_started']
            or any(not isinstance(result[key], str) or not result[key]
                   for key in ('executable', 'software_version', 'project_path', 'cpu_menu'))
            or ntpath.basename(result['executable']).casefold() != 'gd2.exe'
            or not ntpath.isabs(result['executable']) or not ntpath.isabs(result['project_path'])):
        raise ValueError('The native process, project path or software identity is unconfirmed')
    if result['software_version'] != '1.635.0.1':
        raise ValueError('Visible engineering requires the verified GX Works2 1.635.0.1 profile')
    expected, actual = native_project_profile(expected_cpu), native_project_profile(result['cpu_menu'])
    if expected is None or actual is None or expected.cpu != actual.cpu:
        raise ValueError('The selected native CPU differs from the requested engineering target')
    for key in ('executable', 'project_path'):
        result[key] = ntpath.normcase(ntpath.normpath(result[key]))
    metadata = {'native_ui_language', 'project_codepage'}
    if metadata & value.keys():
        from plc.target_support import NATIVE_UI_LANGUAGES
        import codecs
        if (not metadata <= value.keys() or value['native_ui_language'] not in NATIVE_UI_LANGUAGES
                or type(value['project_codepage']) is not int or not 0 < value['project_codepage'] <= 65535):
            raise ValueError('The native UI language and project codepage identity is incomplete')
        try:
            codecs.lookup('cp' + str(value['project_codepage']))
        except LookupError as error:
            raise ValueError('The native project codepage is unknown') from error
        result.update({key: value[key] for key in metadata})
    return result


def compile_observation(observation, *, process_id, revision, require_saved=True):
    """Project one completed GUI rebuild; command dispatch alone proves nothing."""
    result = {'status': 'unverified', 'errors': None, 'warnings': None,
              'check_warnings': None, 'output': observation.get('output', ''),
              'revision': revision, 'execution': 'not_run'}
    output = observation.get('output')
    if not isinstance(output, str):
        return result
    counts = {}
    for field, key in (('Error', 'errors'), ('Warning', 'warnings'), ('CheckWarning', 'check_warnings')):
        values = re.findall(r'(?<![A-Za-z])' + field + r'\s*:\s*([0-9]+)', output, re.IGNORECASE)
        if not values:
            if field == 'CheckWarning' and observation.get('public_checks_requested') is False:
                counts[key] = None
                continue
            return result
        counts[key] = int(values[-1])
    result.update(counts)
    if (observation.get('command') != 'rebuild_all' or observation.get('completed') is not True
            or observation.get('source_unchanged') is not True
            or require_saved and observation.get('saved') is not True
            or type(observation.get('pid')) is not int or observation['pid'] != process_id
            or type(observation.get('revision')) is not int or observation['revision'] != revision):
        return result
    result['status'] = 'passed' if counts['errors'] == 0 else 'failed'
    return result


def _require_current_assignments(raw, result):
    """Keep displayed compiler mappings bound to every later saved snapshot."""
    previous = result['assignments']
    if previous['status'] != 'current':
        return
    matched = False
    try:
        current = saved_assignments(raw)
        matched = current['status'] == 'stored' and current['rows'] == previous['rows']
    finally:
        if not matched:
            result['assignments'] = {'status': 'unverified', 'rows': [],
                'source_usage': 'unverified', 'execution_correspondence': 'unverified'}
    if not matched:
        raise GXWFormatError('The saved snapshot changed or removed the verified compiler address mapping')


class NativeEngineeringSession:
    """Keep all visible steps on one process, project and latest saved source."""

    def __init__(self, driver, expected_cpu, progress=None):
        self.driver = driver
        self.expected_cpu = expected_cpu
        self.progress = progress
        self._started = False
        self._target = None
        self._observed_data = None
        self._observed_sequence = 0

    def _inspect(self):
        current = _identity(self.driver.inspect(), self.expected_cpu)
        if self._target is None:
            self._target = current
        elif current != self._target:
            raise RuntimeError('The native process, window, project, software version or CPU changed during the session')
        return current

    def _observe_saved(self, *, verify_current=True):
        self._inspect()
        raw = self.driver.read_saved_project()
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise ValueError('The active native document did not return saved GXW bytes')
        raw = bytes(raw)
        self._observed_data = raw
        self._observed_sequence += 1
        self._inspect()
        projection = _project_projection(raw)
        from gxworks2.project_profiles import native_project_profile
        if native_project_profile(projection['cpu']).cpu != native_project_profile(self.expected_cpu).cpu:
            raise GXWFormatError('The saved active project CPU differs from the selected native target')
        if verify_current and hasattr(self.driver, 'verify_saved_source'):
            # A file read must not hide an unsaved external source edit. The
            # native driver compares current pages without dispatching Save.
            self.driver.verify_saved_source(raw)
        return raw, projection

    def _emit(self, step):
        if self.progress:
            self.progress(copy.deepcopy(step))

    @staticmethod
    def _visible(observation):
        visible = observation.get('visible_completion')
        if (not isinstance(visible, dict) or visible.get('completed') is not True
                or not isinstance(visible.get('page'), str) or not visible['page'].strip()):
            raise RuntimeError('The driver did not confirm completion on a visible native page')
        return copy.deepcopy(visible)

    def run(self, commands, baseline=None):
        if self._started:
            raise RuntimeError('A native engineering run cannot be replayed; reconcile the saved target before a new run')
        self._started = True
        result = {'status': 'interrupted', 'passed': False, 'steps': [],
            'compile': {'status': 'not_run', 'execution': 'not_run'},
            'assignments': {'status': 'not_run', 'rows': []},
            'save': {'status': 'not_run'}, 'reopen': {'status': 'not_run'},
            'execution': 'not_run', 'revision': 0}
        current, projection, latest_verified = None, None, None
        verified_sequence = 0
        try:
            if (not isinstance(commands, (list, tuple)) or not 1 <= len(commands) <= 256
                    or any(not isinstance(command, Mapping) for command in commands)):
                raise ValueError('A visible engineering run requires 1 to 256 domain commands')
            commands = copy.deepcopy(list(commands))
            for command in commands:
                if command.get('operation') not in AUTHORING_OPERATIONS | _AUXILIARY_OPERATIONS | {'create_fbd_call', 'edit_fbd_graph'}:
                    raise ValueError('Unsupported visible engineering operation')
            self._inspect()
            initial, initial_projection = self._observe_saved(verify_current=False)
            if baseline is not None and _project_projection(bytes(baseline)) != initial_projection:
                raise GXWFormatError('The active native project does not match the selected engineering baseline')
            current, projection = initial, initial_projection
            latest_verified, verified_sequence = current, self._observed_sequence
            compiled_projection = None
            for index, command in enumerate(commands):
                operation = command['operation']
                step = {'index': index, 'operation': operation, 'status': 'executing',
                        'revision': result['revision']}
                result['steps'].append(step)
                self._emit(step)
                self._inspect()
                observed, latest_projection = self._observe_saved()
                if latest_projection != projection:
                    raise GXWFormatError('The active native source changed outside this engineering step')
                _require_current_assignments(observed, result)
                current = observed
                latest_verified, verified_sequence = current, self._observed_sequence
                if operation in AUTHORING_OPERATIONS:
                    prepared = prepare_authoring(current, command, device_profile=self.expected_cpu)
                elif operation == 'create_fbd_call':
                    prepared = prepare_fbd_call(current, command)
                elif operation == 'edit_fbd_graph':
                    prepared = prepare_graph_edit(current, command, device_profile=self.expected_cpu)
                else:
                    allowed = {'operation', 'queries'} if operation == 'show_assignments' else {'operation'}
                    if set(command) - allowed:
                        raise ValueError('Unknown visible engineering command fields')
                    prepared = {'kind': operation, 'plan': {'operation': operation,
                        'revision': result['revision'], 'process_id': self._target['process_id']}}
                    if operation == 'show_assignments':
                        if result['compile']['status'] != 'passed' or compiled_projection != projection:
                            raise GXWFormatError('Address mapping requires this session\'s completed current GUI compile')
                        assignments = saved_assignments(current)
                        selected = prepare_assignment_cross_references(current, command.get('queries'), assignments=assignments)
                        prepared['plan'].update(queries=copy.deepcopy(command['queries']),
                            assignments=assignments, queried_assignments=selected)
                if operation == 'reopen':
                    saved = self.driver.reopen()
                    if not isinstance(saved, (bytes, bytearray)) or not saved:
                        raise ValueError('Cold reopen did not return the saved native project bytes')
                    saved = bytes(saved)
                    self._observed_data = saved
                    self._observed_sequence += 1
                    self._inspect()
                    after_projection = _project_projection(saved)
                    if after_projection != projection:
                        raise GXWFormatError('Declarations, code, graph or configuration changed after cold reopen')
                    _require_current_assignments(saved, result)
                    actual, actual_projection = self._observe_saved()
                    if actual_projection != after_projection:
                        raise GXWFormatError('Cold reopened document differs from its returned saved bytes')
                    _require_current_assignments(actual, result)
                    current, projection = actual, actual_projection
                    latest_verified, verified_sequence = actual, self._observed_sequence
                    step.update(status='completed', verification={'status': 'matched'})
                    result['reopen'] = {'status': 'passed', 'revision': result['revision']}
                else:
                    observation = self.driver.execute(copy.deepcopy(command), copy.deepcopy(prepared))
                    if not isinstance(observation, dict):
                        raise ValueError('The native driver did not return an observation object')
                    step['observation'] = copy.deepcopy(observation)
                    step['visible_completion'] = self._visible(observation)
                    self._inspect()
                    saved, after_projection = self._observe_saved()
                    if operation in AUTHORING_OPERATIONS:
                        verification = verify_authoring_readback(current, command, saved, prepared)
                    elif operation == 'create_fbd_call':
                        verification = verify_fbd_call(current, command, saved, prepared)
                    elif operation == 'edit_fbd_graph':
                        verification = verify_graph_edit(current, command, saved, prepared)
                    else:
                        if after_projection != projection:
                            raise GXWFormatError('The native compile, address display or save changed current source')
                        verification = {'status': 'matched'}
                    if (operation != 'create_st_program'
                            and after_projection['task_scopes'] != projection['task_scopes']):
                        raise GXWFormatError('The visible operation changed current resource/task configuration')
                    if operation == 'configure_project':
                        if after_projection['gui_configuration']['status'] != 'matched':
                            raise GXWFormatError('The saved GUI allocation configuration in Gppw2.gpj does not match the current project')
                        for selector in ('word_device', 'bit_device'):
                            previous = projection['gui_configuration'].get(selector)
                            if previous is not None and after_projection['gui_configuration'].get(selector) != previous:
                                raise GXWFormatError('The saved Gppw2.gpj changed an unrelated allocation device selector')
                        # A disabled device can retain a GUI backup range. It
                        # must not disappear from drift detection merely because
                        # that backup is excluded from effective allocation.
                        allowed_fields = {name + bound for edit in prepared['plan']['ranges']
                            for name in [('100T' if edit['device'] == 'T' else
                                          '10T' if edit['device'] == 'T10' else edit['device'])]
                            for bound in ('Min', 'Max')}
                        previous_fields = projection['gui_configuration'].get('retained_fields', {})
                        current_fields = after_projection['gui_configuration'].get('retained_fields', {})
                        if ({k: v for k, v in previous_fields.items() if k not in allowed_fields}
                                != {k: v for k, v in current_fields.items() if k not in allowed_fields}):
                            raise GXWFormatError('The saved Gppw2.gpj changed unrelated retained allocation fields')
                        verification['gui_configuration'] = copy.deepcopy(after_projection['gui_configuration'])
                    elif after_projection['gui_configuration'] != projection['gui_configuration']:
                        raise GXWFormatError('The visible operation changed unrelated GUI allocation configuration')
                    if after_projection == projection:
                        _require_current_assignments(saved, result)
                    if operation == 'compile':
                        if after_projection['gui_configuration']['status'] != 'matched':
                            raise GXWFormatError('The saved GUI allocation configuration is not verified for this compilation')
                        # Source agreement is separate from whether compilation
                        # succeeded. A failed build may still save these exact
                        # verified declarations, bodies and configurations.
                        current, projection = saved, after_projection
                        latest_verified, verified_sequence = saved, self._observed_sequence
                        result['compile'] = compile_observation(observation,
                            process_id=self._target['process_id'], revision=result['revision'])
                        if result['compile']['status'] != 'passed':
                            step.update(status='failed', verification=copy.deepcopy(result['compile']))
                            raise RuntimeError('The current GUI compilation failed or did not provide complete native evidence')
                        compiled_projection = after_projection
                    elif operation == 'show_assignments':
                        assignments = saved_assignments(saved)
                        cross_references = verify_native_cross_references(saved, command['queries'], observation,
                            selected=prepared['plan']['queried_assignments'])
                        verification['native_cross_references'] = copy.deepcopy(cross_references)
                        result['assignments'] = {**assignments,
                            'status': 'current' if assignments['status'] == 'stored' else 'unavailable',
                            'origin': 'current_gui_compile_saved_snapshot', 'revision': result['revision'],
                            'source_usage': 'unverified',
                            'native_cross_references': cross_references,
                            'execution_correspondence': 'unverified'}
                    elif operation == 'save':
                        result['save'] = {'status': 'passed', 'revision': result['revision']}
                    if after_projection != projection:
                        result['revision'] += 1
                        compiled_projection = None
                        result['compile'] = {'status': 'stale' if result['compile']['status'] != 'not_run' else 'not_run',
                            'execution': 'not_run'}
                        result['assignments'] = {'status': 'stale' if result['assignments']['status'] != 'not_run' else 'not_run', 'rows': []}
                        result['save'] = {'status': 'not_run'}
                        result['reopen'] = {'status': 'not_run'}
                    current, projection = saved, after_projection
                    latest_verified, verified_sequence = saved, self._observed_sequence
                    step.update(status='completed', verification=verification,
                                revision=result['revision'])
                self._emit(step)
            result.update(status='completed', passed=True)
        except Exception as error:
            if result['steps'] and result['steps'][-1]['status'] == 'executing':
                result['steps'][-1].update(status='interrupted', message=str(error))
            result['error'] = {'type': type(error).__name__, 'message': str(error)}
            observation = getattr(error, 'observation', None)
            if isinstance(observation, dict) and observation:
                result['error']['observation'] = copy.deepcopy(observation)
        finally:
            if latest_verified is not None:
                result['data'] = latest_verified
                result['data_status'] = 'last_verified_source'
            if self._observed_data is not None and self._observed_sequence > verified_sequence:
                result['unverified_data'] = self._observed_data
                result['unverified_data_status'] = 'observed_source_not_verified'
            if self._target is not None:
                result['identity'] = copy.deepcopy(self._target)
            try:
                self.driver.close()
            except Exception as error:
                result.update(status='interrupted', passed=False,
                    close_error={'type': type(error).__name__, 'message': str(error)})
        return result
