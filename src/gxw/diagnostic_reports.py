"""Project original public diagnostic reports without promoting completion."""
from __future__ import annotations
from .diagnostic_events import EventIndex
from gxworks2.workspace_observations import (native_code_reader_scope, check_input_kind,
    check_input_layout_bound, check_input_framing_bound, workspace_code_read_origin,
    source_reference_modules_bound, structured_input_reader_bound, public_report_converter_bound,
    source_named_check_diagnostics, check_input_versions, workspace_read_scope_verified)
from gxw.diagnostic_scope import (
    _id,
    PUBLIC_CHECK_REPORT_CONVERSION_CPUS,
)


def _public_report_text(value):
    if not isinstance(value, dict) or not isinstance(value.get('text'), str):
        raise ValueError('public report text is missing')
    data = bytes.fromhex(value['raw_hex_utf16le'])
    if len(data) % 2 or data.decode('utf-16le') != value['text']:
        raise ValueError('public report text bytes differ')
    if type(value.get('is_null')) is not bool or value['is_null'] and data:
        raise ValueError('public report BSTR extent differs')
    return value['text']


def _validate_public_report_layout(report, interface):
    """Validate the observed public ABI without assigning source semantics."""
    if (not isinstance(report, dict) or report.get('report_interface') != interface
            or type(report.get('record_size')) is not int or report['record_size'] != 100):
        raise ValueError('public report ABI differs')
    numeric = ('kind', 'code', 'instance_kind', 'program_kind', 'step', 'network', 'left', 'top', 'right', 'bottom')
    if any(type(report.get(field)) is not int or not -(1 << 31) <= report[field] < (1 << 31) for field in numeric):
        raise ValueError('public report integer extent differs')
    identity = report.get('source_object_id')
    if (not isinstance(identity, list) or len(identity) != 12
            or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in identity)):
        raise ValueError('public report object ID extent differs')
    _public_report_text(report.get('name'))
    _public_report_text(report.get('instance'))
    arguments = report.get('arguments')
    if not isinstance(arguments, list) or len(arguments) > 128:
        raise ValueError('public report argument extent differs')
    for value in arguments:
        _public_report_text(value)


def _public_diagnostic_identity(observation, *, events=None):
    """Check resource-named public reports against independently read resource IDs."""
    if (observation.get('cpu') not in PUBLIC_CHECK_REPORT_CONVERSION_CPUS
            or observation.get('native_version') != '1.635.0.1'):
        return None
    events = EventIndex.of(observation.get('validation', {}).get('events', []) if events is None else events)
    resources, iec_targets = {}, set()
    for row in events.rows('OwnedCheckInput', 'NativeTaskSelection'):
        if (row.get('operation') == 'OwnedCheckInput' and row.get('check_pass') in (None, 'public')
                and row.get('stage') == 'submitted'):
            target = _id(row.get('target'))
            inputs = row.get('rows')
            if (target is not None and all(type(word) is int and 0 <= word <= 0xffffffff for word in target)
                    and isinstance(inputs, list) and inputs
                    and all(structured_input_reader_bound(item) for item in inputs)):
                iec_targets.add(target)
        if row.get('operation') != 'NativeTaskSelection':
            continue
        facts = row.get('resource') or {}
        name = facts.get('read_name') or {}
        target = _id(facts.get('id'))
        if (target is not None and all(type(word) is int and 0 <= word <= 0xffffffff for word in target) and any(target)
                and facts.get('read_type') == {'hresult': 0, 'code': 0, 'data_type': 8}
                and name.get('hresult') == name.get('code') == 0 and isinstance(name.get('name'), str)):
            resources[target] = name['name']
    mismatches, checked = [], 0
    for index, row in events.numbered('ProgramCheckOriginalReports'):
        if row.get('operation') != 'ProgramCheckOriginalReports' or row.get('check_pass') not in (None, 'public'):
            continue
        target = _id(row.get('target'))
        if target is None or any(type(word) is not int or not 0 <= word <= 0xffffffff for word in target) or target not in iec_targets:
            continue
        name = resources.get(target)
        for report in row.get('reports', []):
            if (report.get('kind') not in (2, 3) or report.get('instance_kind') not in (3, 6)
                    or report.get('program_kind') != 1 or name is None
                    or report.get('name', {}).get('text') != name or report.get('instance', {}).get('text') != name):
                continue
            checked += 1
            actual = _id(report.get('source_object_id'))
            if report['instance_kind'] == 6 or actual != target:
                mismatches.append(dict(target=list(target), original=report, event_index=index,
                    expected_object_id=list(target), reported_object_id=list(actual) if actual else None,
                    reason='resource diagnostic uses POU-body scope' if report['instance_kind'] == 6
                        else 'resource diagnostic names a different object'))
    return dict(status='mismatched' if mismatches else 'verified' if checked else 'not_observed',
                checked_report_count=checked, mismatches=mismatches)


def _project_public_diagnostic_conversion(result, observation, *, events=None):
    """Bind every original report to its original 68-to-100-byte conversion.

    Completion of this conversion does not change the vendor GetProgress
    result. The original check task, code and source bindings remain required.
    """
    events = EventIndex.of(observation.get('validation', {}).get('events', []) if events is None else events)
    converted = list(events.rows('NativePublicDiagnosticConversion'))
    unavailable = dict(status='not_observed', completed=False, current_source='not_established',
                       reports=[], public_check_promoted=False)
    if not converted:
        if result['public_check'].get('completed') is True:
            identity = _public_diagnostic_identity(observation, events=events)
            if identity and identity['mismatches']:
                return {**unavailable, 'status': 'incomplete', 'origin': 'original-public-get-progress',
                        'reason': 'original public diagnostic resource identity differs',
                        'identity_mismatches': identity['mismatches']}
            reports = [report for row in events.rows('ProgramCheckOriginalReports')
                       if row.get('check_pass') in (None, 'public') for report in row.get('reports', [])]
            try:
                for report in reports:
                    _validate_public_report_layout(report, 'public-compiler')
            except (KeyError, ValueError, TypeError, UnicodeError) as error:
                return {**unavailable, 'status': 'incomplete', 'origin': 'original-public-get-progress',
                        'reason': str(error), 'observed_report_count': len(reports)}
            return dict(status='completed', completed=True,
                        current_source=result['current_source_check'], reports=reports,
                        origin='original-public-get-progress', public_check_promoted=False)
        return unavailable

    def reject(reason):
        return {**unavailable, 'status': 'incomplete', 'reason': reason,
                'observed_conversions': len(converted), 'origin': 'original-native-converter'}

    if (result.get('cpu') not in PUBLIC_CHECK_REPORT_CONVERSION_CPUS
            or result.get('native_version') != '1.635.0.1'):
        return reject('converter scope is unverified for this CPU or version')
    if (result.get('current_raw_source_check') != 'established'
            or result.get('diagnostic_recheck', {}).get('status') != 'current'
            or result['raw_backend_check'].get('completed') is not True):
        return reject('complete original task and current source/code bindings are not established')
    project = _id(observation['validation'].get('project'))
    planned = {_id(row.get('target')) for row in result['raw_backend_check'].get('targets', [])}
    if project is None or None in planned or not planned:
        return reject('selected resource identities are unavailable')
    native_names = {}
    for row in events.rows('NativeObject'):
        if row.get('name_hresult') == row.get('name_code') == 0:
            try:
                native_names[_id(row.get('id'))] = row.get('name')
            except TypeError:
                continue
    raw_reports, stream, names = {}, None, {}
    for row in events.rows('ProgramCheckReportContext', 'ProgramCheckRawReports', 'ProgramCheckTargetOutcome'):
        if row.get('check_pass') != 'raw-recheck':
            continue
        if row.get('operation') == 'ProgramCheckReportContext':
            stream = _id(row.get('target'))
        elif row.get('operation') == 'ProgramCheckRawReports':
            for report in row.get('reports', []):
                key = (stream, row.get('poll'), report.get('report_index'))
                if stream not in planned or key in raw_reports or report.get('poll') != row.get('poll'):
                    return reject('raw report identity is missing or duplicated')
                raw_reports[key] = report
        elif row.get('operation') == 'ProgramCheckTargetOutcome':
            target = _id(row.get('target'))
            # Resource names also have independent original Workspace reads.
            if target in native_names:
                names[target] = native_names[target]

    def original_text(value):
        if not isinstance(value, dict) or value.get('codepage') != result.get('codepage'):
            raise ValueError('original text codepage differs')
        data = bytes.fromhex(value['raw_hex'])
        if data and data[-1] != 0:
            raise ValueError('original text has no terminator')
        text = (data[:-1] if data else data).decode('cp' + str(result['codepage']))
        if text != value.get('text'):
            raise ValueError('original text bytes differ')
        return text

    public_text = _public_report_text

    output, seen = [], set()
    numeric = ('kind', 'code', 'program_kind', 'step', 'network', 'left', 'top', 'right', 'bottom')
    try:
        for row in converted:
            original, report = row.get('original'), row.get('report')
            target = _id(row.get('target'))
            if not isinstance(original, dict) or not isinstance(report, dict):
                return reject('original or converted report is missing')
            _validate_public_report_layout(report, 'native-converted-public')
            key = (target, original.get('poll'), original.get('report_index'))
            if key not in raw_reports or key in seen or original != raw_reports[key]:
                return reject('conversion does not match one unique original report')
            if (row.get('check_pass') != 'raw-recheck' or row.get('cpu') != result['cpu']
                    or not public_report_converter_bound(row) or row.get('hresult') != 0 or row.get('code') != 0
                    or row.get('original_report_unchanged') is not True
                    or report.get('report_interface') != 'native-converted-public' or report.get('record_size') != 100
                    or (report.get('poll'), report.get('report_index')) != key[1:]):
                return reject('native conversion failed or its ABI/record origin differs')
            if any(type(report.get(field)) is not int or report[field] != original.get(field) for field in numeric):
                return reject('conversion changed a diagnostic code or position')
            if (public_text(report.get('name')) != original_text(original.get('name'))
                    or public_text(report.get('instance')) != original_text(original.get('instance'))
                    or not isinstance(report.get('arguments'), list) or not isinstance(original.get('arguments'), list)
                    or [public_text(value) for value in report['arguments']] != [original_text(value) for value in original['arguments']]):
                return reject('conversion changed a diagnostic name or argument')
            resource_named = (original.get('kind') in (2, 3) and original.get('instance_kind') == 6
                              and original.get('program_kind') == 1
                              and original_text(original.get('library')) == ''
                              and names.get(target) == original_text(original['name'])
                              and names.get(target) == original_text(original['instance']))
            if resource_named and row.get('scope_adapted') is not True:
                return reject('resource-named diagnostic still uses the original incompatible body scope')
            if original.get('kind') in (2, 3) and _id(report.get('source_object_id')) in (None, (0,) * 12):
                return reject('converted diagnostic has no native source object identity')
            if row.get('scope_adapted') is True:
                lookup = row.get('resource_lookup') or {}
                if (original.get('kind') not in (2, 3) or original.get('instance_kind') != 6
                        or original.get('program_kind') != 1 or report.get('instance_kind') != 3
                        or original_text(original.get('library')) != ''
                        or names.get(target) != original_text(original['name'])
                        or names.get(target) != original_text(original['instance'])
                        or lookup.get('hresult') != 0 or lookup.get('code') != 0
                        or lookup.get('requested_type') != 8 or _id(lookup.get('parent')) != project
                        or lookup.get('name') != names[target] or _id(lookup.get('id')) != target
                        or _id(report.get('source_object_id')) != target):
                    return reject('resource-scope adaptation has no exact native resource identity')
            elif row.get('scope_adapted') is not False or report.get('instance_kind') != original.get('instance_kind'):
                return reject('unexpected scope change')
            seen.add(key)
            output.append({'target': list(target), 'original': original, 'report': report,
                           'resource_scope_adapted': row['scope_adapted']})
    except (KeyError, ValueError, TypeError, UnicodeError):
        return reject('native report text or layout cannot be verified')
    if seen != set(raw_reports):
        return reject('conversion is missing original reports')
    return dict(status='completed', completed=True, current_source='established', reports=output,
                origin='original-native-converter', original_report_count=len(raw_reports),
                converted_report_count=len(output), resource_count=len(planned), public_check_promoted=False)
