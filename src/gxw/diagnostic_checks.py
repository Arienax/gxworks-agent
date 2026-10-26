"""Interpret native checker inputs and independent completion evidence."""
from __future__ import annotations
from .diagnostic_events import EventIndex
from collections import defaultdict
from gxworks2.workspace_observations import (native_code_reader_scope, check_input_kind,
    check_input_layout_bound, check_input_framing_bound, workspace_code_read_origin,
    source_reference_modules_bound, structured_input_reader_bound, public_report_converter_bound,
    source_named_check_diagnostics, check_input_versions, workspace_read_scope_verified)
from gxw.diagnostic_scope import (
    NATIVE_COMPILATION_OPERATIONS,
    _id,
)
from gxw.diagnostic_bindings import (
    bind_native_check_input,
    _public_source_audit_binding,
)


def _owned_input_evidence(events, check_sessions, generated, owned_snapshots, planned):
    owned_inputs = []
    modules = tuple(events.rows('OwnedBackendModule'))
    versions = {}
    for session in check_sessions:
        copies = session['owned_inputs']
        if not copies:
            continue
        backend = copies[0].get('input_backend')
        sfc = isinstance(backend, dict) and backend.get('module') == 'DZDataABS_Compiler.dll'
        if sfc not in versions:
            versions[sfc] = check_input_versions(modules, copies[0])
        owner_version, checker_version = versions[sfc]
        submitted = [row for row in copies if row.get('stage') == 'submitted']
        completed = [row for row in copies if row.get('stage') == 'completed']
        code = generated.get(session['target'])
        proof = bind_native_check_input(submitted[0] if len(submitted) == 1 else None,
            completed[0] if len(completed) == 1 else None,
            generated={session['name']: code[0]} if session['name'] and code is not None else {},
            snapshots=owned_snapshots or {}, native_version=owner_version, checker_version=checker_version)
        if not session['started'] or any(_id(row.get('target')) != session['target'] for row in copies):
            proof = {'status': 'unresolved', 'reason': 'owned input is not bound to this original check start',
                     'public_check_promoted': False}
        if session['public'] and (len(copies) != 2 or [row.get('stage') for row in copies] != ['submitted', 'completed']
                or len(session['terminal_indices']) != 1
                or not session['context_index'] < session['copy_indices'][0]
                    < session['poll_indices'][0]
                or not session['terminal_indices'][0] < session['copy_indices'][-1]):
            proof = {'status': 'unresolved', 'reason': 'owned copies do not bracket the original public check',
                     'public_check_promoted': False}
        owned_inputs.append({'target': list(session['target']) if session['target'] else None, **proof})
    owned_correspondence = ('not_observed' if not owned_inputs else
        'unresolved' if any(row['status'] == 'unresolved' for row in owned_inputs) else
        'stale' if any(row['status'] == 'stale' for row in owned_inputs) else
        'partial_targets' if {tuple(row['target']) for row in owned_inputs} != set(planned) else 'current')
    return owned_inputs, owned_correspondence


def _session_outcomes(check_sessions):
    raw_outcomes, public_outcomes = [], []
    for session in check_sessions:
        terminal = any(row.get('percent') == 100 and row.get('hresult') == row.get('code') == 0
            for row in session['polls'].values())
        markers = [row for row in session['reports'] if row.get('kind') == 1 and row.get('code') == 0x23
            and session['name'] and row.get('name', {}).get('text') == session['name']]
        end_marker = len(markers) == 1
        expected_counts = None
        if end_marker:
            arguments = [argument.get('text') for argument in markers[0].get('arguments', [])]
            if (len(arguments) == 2 and all(isinstance(value, str) and value.isascii() and value.isdigit()
                    and len(value) <= 10 for value in arguments)):
                expected_counts = [int(value) for value in arguments]
        # Original compilers can name POUs or SFC children in a resource's
        # report stream. Its resource end marker counts all of those rows.
        source_named_diagnostics = session['public'] and any(
            source_named_check_diagnostics(row, resource=session['name'], reports=session['reports'])
            for row in session['owned_inputs'])
        observed_counts = [sum(row.get('kind') == kind and (source_named_diagnostics
            or row.get('name', {}).get('text') == session['name'])
            for row in session['reports']) for kind in (2, 3)]
        diagnostics_complete = expected_counts is not None and expected_counts == observed_counts
        completed = (session['started'] and session['valid'] and terminal and end_marker and diagnostics_complete
            and session['reported_status'] in ('completed-accepted', 'completed-rejected')
            and all(poll in session['report_polls'] or row.get('count') == 0
                for poll, row in session['polls'].items()))
        status = ('completed-rejected' if any(row.get('kind') == 2 for row in session['reports'])
            else 'completed-accepted') if completed else 'failed' if session['poll_failed'] else 'incomplete'
        outcomes = public_outcomes if session['public'] else raw_outcomes
        outcomes.append({'target': list(session['target']) if session['target'] else None,
            'status': status, 'reported_status': session['reported_status'],
            'terminal_progress': terminal, 'resource_end_marker': end_marker,
            'diagnostics_complete': diagnostics_complete, 'reported_diagnostic_counts': expected_counts,
            'observed_diagnostic_counts': observed_counts,
            **({'diagnostic_count_scope': 'selected-resource-report-stream'} if source_named_diagnostics else {}),
            'completed_resource': session['name'] if end_marker else None})
    return raw_outcomes, public_outcomes


def _check_sessions(events):
    check_sessions, active, pending_target, check_started = [], None, None, False
    native_names = {}
    for index, row in events.numbered('NativeObject', 'ProgramCheckTarget', 'Compiler.ProgramCheck',
            'ProgramCheckReportContext', 'OwnedCheckInput', 'ProgramCheckOriginalProgress',
            'ProgramCheckRawProgress', 'ProgramCheckOriginalReports', 'ProgramCheckRawReports',
            'ProgramCheckTargetOutcome'):
        operation = row.get('operation')
        if operation == 'NativeObject' and row.get('name_hresult') == row.get('name_code') == 0:
            native_names[_id(row.get('id'))] = row.get('name')
        elif operation == 'ProgramCheckTarget':
            pending_target, check_started = _id(row.get('id')), False
            active = None
        elif operation == 'Compiler.ProgramCheck':
            check_started = row.get('hresult') == row.get('code') == 0
        elif operation == 'ProgramCheckReportContext':
            active = None
            if row.get('path') in ('owned-native-backend', 'original-public-get-progress'):
                identity = _id(row.get('target'))
                public = row['path'] == 'original-public-get-progress'
                active = {'target': identity, 'name': native_names.get(identity), 'public': public,
                    'started': identity is not None and identity == pending_target and check_started,
                    'polls': {}, 'report_polls': set(), 'reports': [], 'valid': True,
                    'poll_failed': False, 'reported_status': None, 'owned_inputs': [],
                    'context_index': index, 'poll_indices': [], 'terminal_indices': [], 'copy_indices': []}
                check_sessions.append(active)
        elif active is not None:
            progress_operation = 'ProgramCheckOriginalProgress' if active['public'] else 'ProgramCheckRawProgress'
            reports_operation = 'ProgramCheckOriginalReports' if active['public'] else 'ProgramCheckRawReports'
            outcome_key = 'public_check' if active['public'] else 'backend_check'
            if operation == 'OwnedCheckInput':
                active['owned_inputs'].append(row)
                active['copy_indices'].append(index)
            elif operation == progress_operation:
                active['poll_indices'].append(index)
                active['valid'] &= row.get('poll') not in active['polls']
                active['polls'][row.get('poll')] = row
                active['valid'] &= row.get('hresult') == row.get('code') == 0
                active['poll_failed'] |= row.get('hresult') != 0 or row.get('code') != 0
                if row.get('percent') == 100:
                    active['terminal_indices'].append(index)
                if active['public']:
                    active['valid'] &= (row.get('target') == list(active['target'] or ())
                        and row.get('report_interface') == 'public-compiler'
                        and type(row.get('record_size')) is int and row['record_size'] == 100
                        and type(row.get('percent')) is int and 0 <= row['percent'] <= 100
                        and type(row.get('count')) is int and row['count'] >= 0)
            elif operation == reports_operation:
                poll = active['polls'].get(row.get('poll'), {})
                reports = row.get('reports', [])
                active['valid'] &= (bool(poll) and poll.get('count') == len(reports)
                    and poll.get('percent') == row.get('percent') and row.get('poll') not in active['report_polls'])
                if active['public']:
                    active['valid'] &= (row.get('target') == list(active['target'] or ())
                        and row.get('report_interface') == 'public-compiler'
                        and type(row.get('record_size')) is int and row['record_size'] == 100
                        and all(report.get('report_interface') == 'public-compiler'
                            and type(report.get('record_size')) is int and report['record_size'] == 100
                            and report.get('poll') == row.get('poll') and report.get('report_index') == report_index
                            for report_index, report in enumerate(reports)))
                active['report_polls'].add(row.get('poll'))
                active['reports'].extend(reports)
            elif operation == 'ProgramCheckTargetOutcome' and outcome_key in row:
                active['reported_status'] = row[outcome_key]
                if active['public']:
                    active['valid'] &= row.get('target') == list(active['target'] or ())
                active = None
    return check_sessions


def _publication_evidence(events, generated, published, planned):
    published_reads = []
    reads_by_target = events.grouped('PublishedResourceCodeRead', key=lambda row: _id(row.get('target')))
    for index, row in events.numbered('PublishedResourceCodeCorrespondence'):
        identity = _id(row.get('target'))
        expected, actual = generated.get(identity), (published or {}).get(identity)
        facts = row.get('resource', {})
        successful_read = any(event.get('operation') == 'PublishedResourceCodeRead'
            and _id(event.get('target')) == identity and event.get('hresult') == event.get('code') == 0
            and event.get('name') == facts.get('read_name', {}).get('name')
            and actual is not None and event.get('sizes') == [len(value) for value in actual]
            for read_index, event in reads_by_target.get(identity, ()) if read_index < index)
        bound = (identity is not None and _id(facts.get('id')) == identity
            and facts.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': 8}
            and facts.get('read_name', {}).get('hresult') == facts.get('read_name', {}).get('code') == 0
            and bool(facts.get('read_name', {}).get('name')) and successful_read)
        if not bound or actual is None or expected is None or len(actual) != 3 or len(expected) != 3:
            status = 'not_established'
        elif tuple(actual) != tuple(expected):
            status = 'stale'
        else:
            status = 'current' if any(actual) else 'empty'
        published_reads.append({'resource': list(identity) if identity else None,
            'name': facts.get('read_name', {}).get('name'), 'status': status,
            'channel_sizes': [len(value) for value in actual] if actual is not None else []})
    published_correspondence = ('not_observed' if not published_reads else
        'stale' if any(row['status'] == 'stale' for row in published_reads) else
        'not_established' if any(row['status'] != 'current' for row in published_reads) else
        'partial_targets' if not planned or {tuple(row['resource']) for row in published_reads} != set(planned) else 'current')
    return published_reads, published_correspondence


def _compilation_evidence(events):
    compile_completed = any(row.get('operation') == 'Progress' and row.get('percent') == 100
        and row.get('hresult', 0) == row.get('code', 0) == 0 for row in events.rows('Progress'))
    compile_diagnostics = [report for row in events.rows(*(('Progress', 'CompileRawReports', 'CompilePublicReports'))) if row.get('operation') in ('Progress', 'CompileRawReports', 'CompilePublicReports')
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    compile_failures = [row for row in events.rows(*(NATIVE_COMPILATION_OPERATIONS + ('Progress.GetProgress', 'Compiler.BuildProgress'))) if row.get('operation') in NATIVE_COMPILATION_OPERATIONS + ('Progress.GetProgress', 'Compiler.BuildProgress')
        and (row.get('hresult', 0) < 0 or row.get('code', 0) != 0)]
    compile_started = any(row.get('operation') in NATIVE_COMPILATION_OPERATIONS
        and row.get('hresult') == row.get('code') == 0 for row in events.rows(*NATIVE_COMPILATION_OPERATIONS))
    conversion_failure = any(row.get('operation') in ('CompileRawReports', 'CompilePublicReports')
        and any(report.get('kind') == 1 and report.get('code') == 0x20 for report in row.get('reports', [])) for row in events.rows('CompileRawReports', 'CompilePublicReports'))
    compile_polls = [row for row in events.rows('Progress') if row.get('operation') == 'Progress' and 'poll' in row]
    public_compile = any(row.get('report_interface') == 'public-compiler' for row in compile_polls)
    public_compile_failures = [row for row in compile_polls if row.get('report_interface') == 'public-compiler'
        and (row.get('hresult') != 0 or row.get('code') != 0)]
    reports_by_poll = defaultdict(list)
    for row in events.rows('CompileRawReports', 'CompilePublicReports'):
        try:
            reports_by_poll[row.get('poll')].append(row)
        except TypeError:
            # An invalid report poll cannot match the required integer poll.
            continue
    compile_reports_complete = len({row.get('poll') for row in compile_polls}) == len(compile_polls) and all(
        poll.get('hresult') == poll.get('code') == 0 and type(poll.get('poll')) is int and poll['poll'] > 0
        and type(poll.get('count')) is int and 0 <= poll['count'] <= 10000
        and type(poll.get('percent')) is int and 0 <= poll['percent'] <= 100
        and len(matching := reports_by_poll.get(poll.get('poll'), ())) == 1
        and matching[0].get('operation') == ('CompilePublicReports' if public_compile else 'CompileRawReports')
        and matching[0].get('percent') == poll.get('percent')
        and isinstance(matching[0].get('reports'), list) and len(matching[0]['reports']) == poll.get('count')
        and (not public_compile or (poll.get('report_interface') == matching[0].get('report_interface') == 'public-compiler'
            and matching[0].get('record_size') == 100
            and all(isinstance(report, dict) and report.get('report_interface') == 'public-compiler'
                and report.get('record_size') == 100 for report in matching[0]['reports']))) for poll in compile_polls)
    compile_conversion = {'status': 'failed' if public_compile_failures else
        'completed' if public_compile and compile_completed and compile_reports_complete else
        'incomplete' if public_compile else 'not_observed', 'failures': public_compile_failures}
    compile_acceptance = ('rejected' if conversion_failure or any(row['kind'] == 2 for row in compile_diagnostics)
        or (compile_failures and not public_compile)
        else 'accepted' if compile_started and compile_completed and compile_reports_complete and not compile_failures
        else 'not_established')
    return compile_completed, compile_diagnostics, compile_failures, compile_polls, compile_reports_complete, compile_conversion, compile_acceptance


def _checker_reads(trace, generated, planned, started, read_scope_verified, publication_targets):
    reads, queries, query_reads, check_starts = [], [], [], {}
    for row in trace:
        if row.get('event') == 'check-start':
            index = row.get('check_index')
            if type(index) is int and index >= 0:
                check_starts.setdefault(index, []).append(_id(row.get('target')))
            continue
        if row.get('event') != 'workspace-pcode-read' or row.get('phase') != 'check':
            continue
        caller, stack = row.get('caller') or {}, row.get('stack') or []
        identity = _id(row.get('target'))
        origin = workspace_code_read_origin(row, scope_verified=read_scope_verified, publication_target=identity in publication_targets)
        index = row.get('check_index')
        bound = (origin == 'native-checker' and identity is not None
            and identity == _id(row.get('check_target')) and identity in planned and identity in started
            and type(index) is int and check_starts.get(index) == [identity])
        buffers = row.get('buffers', [])
        record = {'serial': row.get('serial'), 'check_index': index, 'check_target': row.get('check_target'),
                  'resource': row.get('target'), 'channel_sizes': [b.get('size') for b in buffers],
                  'origin': origin, 'native_check_target_bound': bound, 'caller': caller, 'stack': stack}
        query = (row.get('hresult') == row.get('code') == 0 and len(buffers) == 3
                 and row.get('requested_sizes') == [0, 0, 0]
                 and any(b.get('size') and b.get('raw_hex') is None for b in buffers))
        if query:
            queries.append(row.get('serial'))
            query_reads.append(record)
            continue
        actual = []
        for buffer in buffers:
            raw = buffer.get('raw_hex')
            # A successful zero-length channel has no bytes even with NULL.
            if raw is None and buffer.get('size') == 0 and row.get('hresult') == row.get('code') == 0:
                raw = ''
            try:
                actual.append(bytes.fromhex(raw) if raw is not None else None)
            except (TypeError, ValueError):
                actual.append(None)
        expected = generated.get(identity)
        if (row.get('hresult') != 0 or row.get('code') != 0 or len(actual) != 3 or any(b is None for b in actual)
                or any(len(data) != buffer.get('size') for data, buffer in zip(actual, buffers) if data is not None)):
            status = 'unreadable'
        elif expected is None:
            status = 'unbound_resource'
        else:
            status = 'current' if tuple(actual) == tuple(expected) else 'stale'
        reads.append({**record, 'status': status})
    return reads, queries, query_reads


def _planned_checks(events):
    planned, collection = [], None
    collecting, explicit_targets = False, None
    for row in events.rows('Workspace.GetProgramCheckCollection', 'InventoryCount', 'NativeObject',
            'ProgramCheckTarget', 'ProgramCheckTargetOrder'):
        if row.get('operation') == 'Workspace.GetProgramCheckCollection':
            collecting = True
        elif collecting and row.get('operation') == 'InventoryCount':
            collection = _id(row.get('id'))
        elif collecting and row.get('operation') == 'NativeObject' and _id(row.get('parent')) == collection:
            planned.append(_id(row['id']))
        elif row.get('operation') == 'ProgramCheckTarget':
            collecting = False
        elif row.get('operation') == 'ProgramCheckTargetOrder':
            explicit_targets = [_id(target) for target in row.get('targets', [])]
    started = [_id(row['id']) for row in events.rows('ProgramCheckTarget') if row.get('operation') == 'ProgramCheckTarget']
    planned = list(dict.fromkeys(explicit_targets if explicit_targets is not None else planned or started))
    return planned, started


def analyze_native_check(events, observations, generated, *, selection=None, reopened=None, source_audits=None,
                  published=None, owned_snapshots=None):
    """generated maps native resource IDs to three exact GetPCode buffers."""
    events = EventIndex.of(events)
    trace = [row.get('payload', row) for row in observations if row.get('type') != 'error']
    observation_errors = [row for row in observations if row.get('type') == 'error']
    observation_errors += [row for row in trace if row.get('event') == 'observation-error']
    planned, started = _planned_checks(events)
    # The observer's phase and last check target survive a completed call.
    # Later managed publication guards can therefore still be tagged 'check'.
    # Only the measured native caller path can establish checker input bytes.
    read_scope_verified = workspace_read_scope_verified(events.rows('OwnedBackendModule'))
    publication_targets = {_id(row.get('target')) for row in events.rows('PublishedResourceCodeCorrespondence')
        if row.get('operation') == 'PublishedResourceCodeCorrespondence' and _id(row.get('target')) is not None}
    reads, queries, query_reads = _checker_reads(trace, generated, planned, started, read_scope_verified, publication_targets)
    completed_backend = list(dict.fromkeys(_id(row.get('target')) for row in trace
        if row.get('event') == 'backend-check-progress' and row.get('percent') == 100
        and row.get('hresult') == row.get('code') == 0))
    public_completion = [row for row in events.rows('ProgramCheckCompleted') if row.get('operation') == 'ProgramCheckCompleted']
    drained_completion = [row for row in events.rows('ProgramCheckPollingFinished') if row.get('operation') == 'ProgramCheckPollingFinished']
    target, drained_targets = None, []
    for row in events.rows('ProgramCheckReportContext', 'ProgramCheckTargetOutcome'):
        if row.get('operation') == 'ProgramCheckReportContext':
            target = _id(row.get('target'))
        elif row.get('operation') == 'ProgramCheckTargetOutcome' and 'public_check' in row:
            drained_targets.append((target, row['public_check']))
    projection_failures = [row for row in trace if row.get('event') == 'diagnostic-projection-failure']
    failed_public_poll = any(row.get('operation') in ('ProgramCheckProgress.GetProgress', 'ProgramCheckOriginalProgress')
        and (row.get('hresult', 0) < 0 or row.get('code', 0) != 0) for row in events.rows('ProgramCheckProgress.GetProgress', 'ProgramCheckOriginalProgress'))
    public_diagnostics = [report for row in events.rows(*events.matching('ProgramCheckProgress'), 'ProgramCheckOriginalReports') if (row.get('operation', '').startswith('ProgramCheckProgress')
        or row.get('operation') == 'ProgramCheckOriginalReports')
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    backend_diagnostics = [report for row in trace if row.get('event') == 'backend-check-progress'
        for report in row.get('reports', []) if report.get('kind') in (2, 3)]
    legacy_completed = bool(public_completion) and public_completion[-1].get('targets') == len(planned)
    drained_completed = (bool(drained_completion) and drained_completion[-1].get('targets') == len(planned)
        and len(drained_targets) == len(planned) and {t for t, _ in drained_targets} == set(planned)
        and all(status in ('completed-accepted', 'completed-rejected') for _, status in drained_targets))
    whole_completed = (bool(planned) and set(started) == set(planned)
        and (legacy_completed or drained_completed) and not projection_failures and not failed_public_poll)
    public_status = ('rejected' if (public_completion and public_completion[-1].get('rejected'))
                     or any(status == 'completed-rejected' for _, status in drained_targets)
                     or any(d['kind'] == 2 for d in public_diagnostics)
                     else 'passed') if whole_completed else 'incomplete'
    source_binding = _public_source_audit_binding(source_audits, public_diagnostics)
    requested = events.has('Workspace.GetProgramCheckCollection') or bool(started)
    if not requested:
        public_status = 'not_requested'
    not_applicable = any(row.get('reason') == 'no_executable_resources'
                         for row in events.rows('ProgramCheckNotApplicable')) and not planned and not generated
    if not_applicable:
        public_status = 'not_applicable'
    observed_reads = reads + query_reads
    checker_reads = [row for row in reads if row['origin'] == 'native-checker']
    if not observed_reads or all(row['origin'] == 'auxiliary-publication' for row in observed_reads):
        correspondence = 'not_observed'
    elif (observation_errors or any(row['origin'] == 'unknown' or
            (row['origin'] == 'native-checker' and not row['native_check_target_bound']) for row in observed_reads)
            or not checker_reads or any(row['status'] not in ('current', 'stale') for row in checker_reads)):
        correspondence = 'not_established'
    elif any(row['status'] == 'stale' for row in checker_reads):
        correspondence = 'stale'
    elif (not planned or set(started) != set(planned)
            or events.has('ProgramCheckTargetSkipped')
            or any(not any(_id(row['check_target']) == target for row in checker_reads) for target in planned)):
        correspondence = 'partial_targets'
    else:
        correspondence = 'current'
    compile_completed, compile_diagnostics, compile_failures, compile_polls, compile_reports_complete, compile_conversion, compile_acceptance = _compilation_evidence(events)
    published_reads, published_correspondence = _publication_evidence(events, generated, published, planned)
    skipped_targets = [row for row in events.rows('ProgramCheckTargetSkipped') if row.get('operation') == 'ProgramCheckTargetSkipped']
    check_sessions = _check_sessions(events)
    raw_outcomes, public_outcomes = _session_outcomes(check_sessions)
    raw_completed = (bool(planned) and len(raw_outcomes) == len(planned) and not skipped_targets
        and {tuple(row['target']) if row['target'] else None for row in raw_outcomes} == set(planned)
        and all(row['status'] in ('completed-accepted', 'completed-rejected') for row in raw_outcomes)
        and bool(drained_completion) and drained_completion[-1].get('targets') == len(planned))
    raw_status = ('completed-rejected' if any(row['status'] == 'completed-rejected' for row in raw_outcomes)
        else 'completed-accepted') if raw_completed else 'incomplete' if raw_outcomes or skipped_targets else 'not_observed'
    if public_outcomes or events.has('ProgramCheckOriginalProgress', 'ProgramCheckOriginalReports'):
        whole_completed &= (bool(public_outcomes)
            and all(row['status'] in ('completed-accepted', 'completed-rejected') for row in public_outcomes)
            and len(public_outcomes) == len({tuple(row['target']) if row['target'] else None for row in public_outcomes}))
        if not whole_completed:
            public_status = 'incomplete'
    owned_inputs, owned_correspondence = _owned_input_evidence(events, check_sessions, generated, owned_snapshots, planned)
    selected = selection or {'status': 'not_observed'}
    result = {
        'selection': selected,
        'compilation': {'completed': compile_completed, 'acceptance': compile_acceptance,
            'diagnostics_complete': compile_reports_complete if compile_polls else None,
            'diagnostic_conversion': compile_conversion,
            'diagnostics': compile_diagnostics, 'failures': compile_failures,
            'returned_resources': events.count('Resource'), 'bound_resources': len(generated)},
        'publication': {'completed': any(row.get('operation') == 'Workspace.UpdatePCodeBeforeProgramCheck'
            and row.get('hresult') == row.get('code') == 0 for row in events.rows('Workspace.UpdatePCodeBeforeProgramCheck'))},
        'checker_resources': {'correspondence': correspondence, 'reads': reads, 'size_queries': queries,
                              'size_query_reads': query_reads, 'scope_verified': read_scope_verified,
                              'observation_errors': observation_errors},
        'owned_check_inputs': {'correspondence': owned_correspondence, 'targets': owned_inputs,
            'provenance': 'original retained task code and original envelope reader; no interception'},
        'published_resources': {'correspondence': published_correspondence, 'reads': published_reads,
            'provenance': 'original workspace read-back before checking; not an observed checker input'},
        'raw_backend_check': {'status': raw_status, 'completed': raw_completed,
            'targets': raw_outcomes, 'skipped_targets': skipped_targets,
            'public_adapter_projection': 'not_called' if raw_outcomes else 'not_observed'},
        'targets': {'planned': [list(t) for t in planned], 'started': [list(t) for t in started],
                    'backend_completed': [list(t) for t in completed_backend]},
        'public_check': {'status': public_status, 'completed': whole_completed,
                         **({'reason': 'no_executable_resources'} if not_applicable else {})},
        'diagnostic_projection': {'status': 'failed' if projection_failures or failed_public_poll else
            'completed' if whole_completed else 'not_completed', 'failures': projection_failures,
            'source_body_binding': source_binding, 'source_audits': source_audits or [],
            'target_checks': public_outcomes,
            'public_diagnostics': public_diagnostics, 'backend_diagnostics': backend_diagnostics,
            'empty_public_list_means_no_errors': whole_completed and not public_diagnostics and not backend_diagnostics},
        'save': {
            'completed': any(row.get('hresult') == row.get('code') == 0 for row in events.rows('SaveProject')),
            'exported': any(row.get('operation') == 'NativeExport'
                or (row.get('operation') == 'ExportOneFileProject' and row.get('hresult') == row.get('code') == 0)
                for row in events.rows('NativeExport', 'ExportOneFileProject')),
        },
        'reopen': reopened or {'status': 'not_observed'},
        'current_source_check': 'not_established',
    }
    return bind_native_check_selection(result, selected)



def bind_native_check_selection(result, selection):
    "Bind selection without replaying compilation, publication or event analysis."
    checker = result['checker_resources']
    selection_current = (selection.get('status') == 'verified'
        and selection.get('source_bytes_current') is True
        and selection.get('configuration_bytes_current') is True
        and bool(selection.get('selected_sources')) and not selection.get('empty_task'))
    retained_current = (result['owned_check_inputs']['correspondence'] == 'current'
        and result['published_resources']['correspondence'] == 'current'
        and checker['correspondence'] in ('current', 'not_observed')
        and not checker['observation_errors'])
    established = (selection_current and result['compilation']['acceptance'] == 'accepted'
        and (checker['correspondence'] == 'current' or retained_current)
        and result['public_check']['completed'])
    return {**result, 'selection': selection,
            'current_source_check': 'established' if established else 'not_established'}
