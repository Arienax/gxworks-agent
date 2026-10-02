"""Freeze the completed native FB diagnostic module and isolated skill trial.

The local archive retains full owned inputs and failed attempts. The repository
archive contains owned source streams, observations and tool provenance, not
vendor DLLs, Ghidra program databases or portable dependency distributions.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
TRIAL = P / 'skill-trial-20261002-v1'
STEM = 'gxw-native-diagnostic-sources-20261002'
PUBLIC = ROOT / 'research/evidence' / (STEM + '.zip')
LOCAL = P / (STEM + '-local.zip')
RESULT = ROOT / 'research/results' / (STEM + '.json')
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'research')]
from gxw.container_writer import validate_cfb_streams
from gxw.object_model import read_project_context
from gxw.project_metadata import logical_mapping
from gxw.text_pou import parse_st_pou
from program_check_evidence import analyze_directory, correlate_st_diagnostic

DIRECTORIES = (
    'diagnostic-source-locations-20261002-v1',
    'diagnostic-source-abi-20261002-v1',
    'diagnostic-inverse-sources-20261002-v1',
    'diagnostic-callsite-module-20261002-v1',
    'diag-query-20261002-v1',
    'diag-cpu-20261002-v1',
    'ghidra-debug-20261002-v1',
)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def source_case(result):
    directory = Path(result['directory'])
    raw = (directory / 'input.gxw').read_bytes()
    context = read_project_context(raw, 'FBD_MATRIX.Program.pou')
    outer = validate_cfb_streams(raw)
    nested = validate_cfb_streams(outer['_hdb'])
    mapping = logical_mapping(outer['projectdatalist.xml'])
    body = nested[mapping['MODULE_FB.Program.pou']]
    source_texts = {'MODULE_FB': parse_st_pou(body).text}
    references = result['source_references']['queries'][0]['rows']
    graph = context.object_model()['nodes']
    phases = analyze_directory(directory)
    projections = [correlate_st_diagnostic(query, result['link_witness']['rows'], references,
        source_texts, graph) for query in result['diagnostic_locations']]
    expected_status = 'unresolved' if context.sources.cpu == 'FX0N' else 'uniquely_correlated'
    if not all(row['status'] == expected_status for row in projections):
        raise ValueError({'directory': str(directory), 'projections': projections})
    if len(projections) == 2 and expected_status == 'uniquely_correlated':
        assert {row['instance'] for row in projections} == {
            'FBD_MATRIX.APP_MODULE_STAGE_A', 'FBD_MATRIX.APP_MODULE_STAGE_B'}
    assert all(row['source']['text'] == 'Y1 := SIGNAL;' for row in projections if row['status'] == 'uniquely_correlated')
    assert result['compile_completed'] and result['checker_read_correspondence'] == 'current'
    assert phases['public_check']['status'] == 'incomplete' and result['diagnostic_projection'] == 'failed'
    return {'cpu': context.sources.cpu, 'scope': result.get('prior_scope'),
        'directory': directory.relative_to(ROOT).as_posix(),
        'phase_result': result, 'phase_classification': phases, 'source_texts': source_texts,
        'owned_sources_base64': {
            name: base64.b64encode(nested[mapping[name]]).decode('ascii') for name in
            ('FBD_MATRIX.Program.pou', 'FBD_MATRIX.Labels.lh', 'MODULE_FB.Program.pou', 'MODULE_FB.Labels.lh')},
        'caller_nodes': graph, 'projections': projections, 'expected_status': expected_status}


def collect():
    attempts = read_json(P / 'diag-cpu-20261002-v1/results.json')
    selected = [row for row in attempts if row['returncode'] == 2]
    assert len(selected) == 55 and len({row['cpu'] for row in selected}) == 55
    witnesses = [source_case(row) for row in selected]
    pilot = read_json(P / 'diag-query-20261002-v1/results.json')
    pilot_witnesses = [source_case(row) for row in pilot if row['returncode'] == 2]
    debug = read_json(P / 'ghidra-debug-20261002-v1/results.json')
    debug_calls = [call for row in debug for call in row['debug_observations']]
    assert len(debug) == 3 and len(debug_calls) == 36
    assert all(row['returncode'] == 0 and not row['observation_errors'] for row in debug)
    assert all(call['initialized'] and call['hresult'] == -2147467259
        and call['code'] == 0x50010006 and call['count'] == 0
        and call['ranges_present'] is False for call in debug_calls)
    counts = read_json(TRIAL / 'after-fix-counts.json')
    final_counts = read_json(TRIAL / 'after-reference-guards-counts.json')
    assert counts['status'] == final_counts['status'] == 0
    assert counts['evaluations'] == {'rename_sequence': 101, 'formal_rename_negation': 100, 'opaque_bytes': 102}
    source = TRIAL.parent / 'application-fbd-v2-20261002-v1/full-FX3U-FX3UC/native-compile/input.gxw'
    (TRIAL / 'pbt-source-input.gxw').write_bytes(source.read_bytes())
    result = {'schema_version': 1, 'date': '2026-10-02', 'checkout': 'ef0a255 plus recorded working-tree changes',
        'environment': {'native_dll_version': '1.635.0.1', 'python': '3.13',
            'ghidra_cli': '0.2.2', 'ghidra': '12.1.4', 'jdk': '21.0.12.1+1', 'hypothesis': counts['hypothesis']},
        'native_cpu_attempts': len(attempts), 'retained_startup_collisions': sum(r['returncode'] == 5 for r in attempts),
        'exact_cpu_options': len(selected),
        'correlated_diagnostics': sum(p['status'] == 'uniquely_correlated' for w in witnesses for p in w['projections']),
        'unresolved_diagnostics': sum(p['status'] == 'unresolved' for w in witnesses for p in w['projections']),
        'unresolved_options': [w['cpu'] for w in witnesses if w['expected_status'] == 'unresolved'],
        'backend_completion_at_public_failure': dict(Counter(bool(r['backend_completed_targets']) for r in selected)),
        'backend_not_complete_options': [r['cpu'] for r in selected if not r['backend_completed_targets']],
        'cpu_options': [r['cpu'] for r in selected], 'scopes': [r['prior_scope'] for r in selected],
        'public_check': 'incomplete in all 55 diagnostic controls',
        'ghidra_debug_boundary': {'method_rva': '0x652a', 'native_module': 'DZDataABS_Compiler_IEC.dll',
            'cpu_options': [r['scope'].removeprefix('full-') for r in debug],
            'calls': len(debug_calls), 'observed_code': '0x50010006',
            'static_result': 'always E_FAIL; initialized object reports 0x50010006; null backend reports 0x50010004'},
        'property_trial': {'generated_cases': 300, 'explicit_cases': 3, 'counts': counts,
            'final_reference_guard_recheck': final_counts,
            'minimal_counterexample': {'formal': 'STATE', 'new_name': 'P_0', 'side': 'in', 'negated': False},
            'bug': 'formal rename left earlier port edits and named wire references on old endpoint names',
            'project_dependency_changed': False},
        'validation': {'object_model': '88 passed', 'web_mcp_and_generation': '74 passed',
            'plc_or_simulator_execution': 'not_run'},
        'limits': ['55 exact native menu options, with prior scope reductions; not each physical CPU in a combined option',
            '108 correlated and 2 unresolved diagnostics are controlled two-instance module observations, not independent language structures',
            'FX0N control has no separate FB instance intervals in its primary link map; both source instance candidates remain unresolved',
            'coarse FBD network locations and missing/ambiguous intervals remain unresolved',
            'source correlation does not complete the public check or certify source/configuration selection generally',
            'property checks validate software editing and byte preservation, not execution of renamed ST formals'],
        'skills': {
            'ghidra-cli': 'https://github.com/akiselev/ghidra-cli/blob/v0.2.2/.claude/skills/ghidra-cli/SKILL.md',
            'property-based-testing': 'https://github.com/trailofbits/skills/blob/master/plugins/property-based-testing/skills/property-based-testing/SKILL.md'}}
    files = {'cpu-source-witnesses.json': json_bytes(witnesses),
        'four-diagnostic-pilot.json': json_bytes(pilot_witnesses),
        'native-debug-boundary.json': json_bytes(debug), 'cpu-attempts.json': json_bytes(attempts)}
    for dirname in DIRECTORIES[:4]:
        for name in ('results.json', 'results-with-partial-errors.json'):
            path = P / dirname / name
            if path.is_file():
                files[dirname + '/' + name] = path.read_bytes()
    for relative in ('src/gxw/editor.py', 'tests/test_gxw_object_model.py',
                     'tests/test_gxw_compiler.py',
                     'research/program_check_evidence.py', 'research/freeze_gxw_diagnostic_sources.py'):
        files['source/' + relative] = (ROOT / relative).read_bytes()
    for path in TRIAL.rglob('*'):
        relative = path.relative_to(TRIAL)
        if path.is_file() and not any(part in {'python-deps', 'bin', '__pycache__', '.hypothesis'} for part in relative.parts):
            if path.suffix in {'.py', '.java', '.md', '.txt', '.json', '.jsonl', '.bin'} or path.name == 'LICENSE':
                files['skill-trial/' + relative.as_posix()] = path.read_bytes()
    files['checkpoint.json'] = json_bytes(result)
    return result, files


def write_archive(path, files):
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == set(files)
        assert all(archive.read(name) == data for name, data in files.items())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    arguments = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    if not arguments.check_only:
        for path in (PUBLIC, LOCAL, RESULT):
            if path.exists():
                raise FileExistsError('Frozen checkpoint already exists: ' + str(path))
    result, files = collect()
    if arguments.check_only:
        print(json.dumps({'checked': len(files), 'cpu_options': result['exact_cpu_options'],
            'diagnostics': result['correlated_diagnostics']}))
        return
    local = dict(files)
    for dirname in DIRECTORIES:
        directory = P / dirname
        for path in directory.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                local['raw/' + path.relative_to(P).as_posix()] = path.read_bytes()
    local['raw/skill-trial/pbt-source-input.gxw'] = (TRIAL / 'pbt-source-input.gxw').read_bytes()
    write_archive(PUBLIC, files)
    write_archive(LOCAL, local)
    RESULT.write_bytes(json_bytes(result))
    print(json.dumps({'public_entries': len(files), 'local_entries': len(local),
        'cpu_options': result['exact_cpu_options'], 'diagnostics': result['correlated_diagnostics']}))


if __name__ == '__main__':
    main()
