"""Native validation orchestration and public diagnostic entry points."""
from __future__ import annotations
from .diagnostic_events import EventIndex

from gxworks2.workspace_observations import (native_code_reader_scope, check_input_kind,
    check_input_layout_bound, check_input_framing_bound, workspace_code_read_origin,
    source_reference_modules_bound, structured_input_reader_bound, public_report_converter_bound,
    source_named_check_diagnostics, check_input_versions, workspace_read_scope_verified)
from gxw.diagnostic_scope import (
    NATIVE_SOURCE_CAPACITY,
    NATIVE_COMPILATION_OPERATIONS,
    PUBLIC_FBD_COMPILE_POSITION_CPUS,
    PUBLIC_DECLARATION_COMPILE_POSITION_CPUS,
    PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS,
    PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS,
    PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS,
    SFC_CHILD_INSTRUCTION_POSITION_CPUS,
    _id,
    PUBLIC_CHECK_REPORT_CONVERSION_CPUS,
)
from gxw.diagnostic_sources import (
    _native_validation_sources,
    _native_validation_source_body,
    _native_compilation_method,
    _native_validation_sfc_children,
    sfc_child_source_scope,
    _native_validation_library_sources,
    native_validation_source_plan,
    _native_assignment_sources,
    native_inspection_queries,
    bind_native_ladder_candidate,
    native_import_selection,
)
from gxw.diagnostic_inspection import (
    project_native_inspection,
)
from gxw.diagnostic_bindings import (
    bind_native_code_lexical_read,
    bind_native_check_input,
    bind_native_source_snapshot,
    project_native_source_identity,
    project_native_source_diagnostic,
    _debug_st_interval,
    bind_native_instance_interval,
    _public_source_audit_binding,
    compare_public_source_diagnostic,
)
from gxw.diagnostic_positions import (
    _native_network_members,
    correlate_native_compile_position,
    correlate_st_diagnostic,
)
from gxw.diagnostic_sfc import (
    bind_native_sfc_child_snapshots,
    bind_native_source_chain,
    bind_native_sfc_referenced_check,
    project_native_sfc_child_diagnostic,
    project_native_sfc_instruction_position,
    project_native_sfc_graph_diagnostics,
)
from gxw.diagnostic_declarations import (
    _native_declaration_facts,
    _native_declaration_parent,
    _native_validation_declaration_owners,
    bind_native_compile_declaration_owners,
    correlate_native_compile_declaration_owner,
    bind_native_compile_declarations,
    correlate_native_compile_declaration,
    bind_native_compile_source_context,
    bind_native_source_references,
    _native_task_source_bindings,
)
from gxw.diagnostic_reports import (
    _public_report_text,
    _validate_public_report_layout,
    _public_diagnostic_identity,
    _project_public_diagnostic_conversion,
)
from gxw.diagnostic_checks import (
    analyze_native_check,
)
from gxw.diagnostic_outputs import (
    correlate_fbd_instance_output,
    correlate_fbd_bool_output,
)


from .diagnostic_projection import _project_native_validation


def project_native_validation(raw, observation, snapshots):
    """Interpret original native checks, retaining any distinct diagnostic recheck."""
    validation = observation.get('validation') if isinstance(observation, dict) else None
    rows = validation.get('events', []) if isinstance(validation, dict) else None
    events = None
    if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
        try:
            events = EventIndex.of(rows)
        except TypeError:
            # Preserve protocol/CPU validation before errors in event keys.
            pass
    if isinstance(validation, dict) and validation.get('diagnostic_recheck') is True:
        result = _project_native_diagnostic_recheck(raw, observation, snapshots, event_index=events)
    else:
        result = _project_native_validation(raw, observation, snapshots, event_index=events)
    identity = _public_diagnostic_identity(observation, events=events)
    if identity and identity['mismatches']:
        result['public_diagnostic_identity'] = identity
        result['current_source_check'] = 'not_established'
        result['diagnostic_projection']['status'] = 'identity_mismatch'
        result['diagnostic_projection']['empty_public_list_means_no_errors'] = False
    if (validation.get('public_diagnostic_conversion_requested') is True
            or events.has('NativePublicDiagnosticConversion')):
        result['public_diagnostic_conversion'] = _project_public_diagnostic_conversion(result, observation, events=events)
        if result['public_check'].get('completed') is True and not result['public_diagnostic_conversion']['completed']:
            result['diagnostic_projection']['status'] = 'conversion_incomplete'
            result['diagnostic_projection']['empty_public_list_means_no_errors'] = False
    return result


def _project_native_diagnostic_recheck(raw, observation, snapshots, *, event_index=None):
    from copy import deepcopy
    from .models import GXWFormatError
    events = observation['validation'].get('events')
    phases = {'common', 'public', 'public-task-tail', 'raw-recheck'}
    if (not isinstance(events, list) or any(not isinstance(row, dict) or row.get('check_pass') not in phases for row in events)):
        raise GXWFormatError('native diagnostic recheck has an unbound event pass')
    events = EventIndex.of(events if event_index is None else event_index)
    failed = [(index, row) for index, row in events.numbered('ProgramCheckOriginalProgress')
        if row.get('check_pass') == 'public'
        and row.get('hresult') == -2147467259 and row.get('code') == 0x2d010025]
    identity = _public_diagnostic_identity(observation, events=events)
    mismatched = identity['mismatches'] if identity else []
    triggers = failed + [(row['event_index'], row) for row in mismatched]
    if not triggers:
        raise GXWFormatError('native diagnostic recheck has no original public conversion failure or resource identity mismatch')

    def interpret(phase):
        selected = {**observation, 'validation': {**observation['validation'],
            'events': [row for row in events if row['check_pass'] in ('common', phase)]}}
        selected['validation'].pop('diagnostic_recheck', None)
        return _project_native_validation(raw, selected, snapshots)

    public, recheck = interpret('public'), interpret('raw-recheck')
    raw_starts = [index for index, row in events.numbered('Compiler.ProgramCheck')
        if row['check_pass'] == 'raw-recheck'
        and row.get('hresult') == row.get('code') == 0]
    raw_start = min(raw_starts, default=len(events))
    completed_targets = set()
    active_tail = None
    for index, row in events.numbered('ProgramCheckReportContext', 'ProgramCheckRawProgress', 'ProgramCheckOriginalProgress'):
        if index >= raw_start:
            break
        if row['check_pass'] == 'public-task-tail' and row.get('operation') == 'ProgramCheckReportContext':
            active_tail = _id(row.get('target'))
        if (row['check_pass'] == 'public-task-tail' and row.get('operation') == 'ProgramCheckRawProgress'
                and row.get('hresult') == row.get('code') == 0 and row.get('percent') == 100):
            completed_targets.add(active_tail)
        if (row['check_pass'] == 'public' and row.get('operation') == 'ProgramCheckOriginalProgress'
                and row.get('percent') == 100):
            completed_targets.add(_id(row.get('target')))
    prior_tasks_terminal = (bool(raw_starts) and all(index < raw_start and _id(row.get('target')) in completed_targets
        for index, row in triggers))
    public_starts = [index for index, row in events.numbered('Compiler.ProgramCheck') if row['check_pass'] == 'public']
    mutations = {'Compiler.Build', 'Compiler.ChangeSFCProgram', 'Compiler.PrepareBuildData', 'Compiler.Reinitialize', 'Compiler.ToBackward',
        'Workspace.UpdatePCodeBeforeProgramCheck', 'Workspace.SetPOUBodyData', 'Workspace.SetLine', 'Workspace.CreateData'}
    same_compilation = (bool(public_starts and raw_starts) and max(public_starts) < raw_start
        and not any(index >= min(public_starts, default=0) for index, _ in events.numbered(*mutations)))

    def inputs(phase):
        copies = {}
        for row in events.rows('OwnedCheckInput', stage='submitted'):
            if row['check_pass'] != phase:
                continue
            target, items = _id(row.get('target')), row.get('rows')
            if target is None or target in copies or not isinstance(items, list) or not items:
                return None
            values = [(item.get('resource'), snapshots.get(item.get('file'))) for item in items if isinstance(item, dict)]
            if len(values) != len(items) or any(not isinstance(name, str) or not isinstance(data, bytes) for name, data in values):
                return None
            copies[target] = (row.get('mask'), values)
        return copies or None

    prior_inputs, retry_inputs = inputs('public'), inputs('raw-recheck')
    same_inputs = prior_inputs is not None and prior_inputs == retry_inputs
    bound = prior_tasks_terminal and same_compilation and same_inputs
    diagnostics = deepcopy(recheck['raw_diagnostics'])
    for row in diagnostics:
        row['report_origin']['check_pass'] = 'raw-recheck'
        if not bound:
            row['graph_projection'] = {'status': 'unresolved', 'public_check_promoted': False,
                'reason': 'recheck is not bound to the original public pass'}
    public.update(raw_backend_check={**recheck['raw_backend_check'], 'check_pass': 'raw-recheck'},
        raw_diagnostics=diagnostics,
        current_raw_source_check=recheck['current_raw_source_check'] if bound else 'not_established',
        native_source_locations=recheck['native_source_locations'], native_instance_locations=recheck['native_instance_locations'],
        native_code_lexical_reads=recheck['native_code_lexical_reads'],
        diagnostic_recheck={'status': 'current' if bound and recheck['current_raw_source_check'] == 'established' else 'incomplete',
            **({'trigger_code': 0x2d010025} if failed else {}),
            'trigger_reasons': (['public_conversion_failure'] if failed else [])
                + (['public_resource_identity_mismatch'] if mismatched else []),
            'previous_tasks_terminal': prior_tasks_terminal,
            'same_compilation': same_compilation, 'same_checker_input_bytes': same_inputs,
            'public_check': public['public_check'], 'raw_check': recheck['raw_backend_check'],
            'owned_check_inputs': recheck['owned_check_inputs'], 'published_resources': recheck['published_resources']})
    projection = public['diagnostic_projection']
    if (bound and recheck['raw_backend_check'].get('completed') is True
            and recheck['current_raw_source_check'] == 'established' and projection['source_diagnostics']):
        # Preserve the first pass as evidence; the complete recheck supplies
        # the current diagnostic list, without collapsing distinct instances.
        projection['original_public_source_diagnostics'] = projection['source_diagnostics']
        projection['source_diagnostics'] = diagnostics
    else:
        projection['source_diagnostics'].extend(diagnostics)
    public['diagnostic_projection']['backend_diagnostics'] = [row['original'] for row in diagnostics]
    public['diagnostic_projection']['empty_public_list_means_no_errors'] = False
    return public
