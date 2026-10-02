"""Freeze the native FBD normalization, current checks and cold-reopen module.

This only audits existing experiments. It neither runs native code nor changes
the project. Full GXW inputs stay in a local archive; owned source streams and
observations are retained in the repository archive. Compare bytes directly.
"""
from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
M = P / 'fbd-checked-cpu-roundtrip-20261003-v1'
STEM = 'gxw-fbd-checked-roundtrip-20261003'
PUBLIC = ROOT / 'research/evidence' / (STEM + '.zip')
LOCAL = P / (STEM + '-local.zip')
RESULT = ROOT / 'research/results' / (STEM + '.json')
sys.path[:0] = [str(ROOT / 'src'), str(P)]
from gxw.container_writer import validate_cfb_streams
from gxw.object_model import read_project_context
from gxw.project_metadata import logical_mapping
from gxw.text_pou import parse_st_pou


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def events(directory):
    return [json.loads(line) for line in
            (directory / 'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]


def source_witness(raw):
    outer = validate_cfb_streams(raw)
    inner = validate_cfb_streams(outer['_hdb'])
    mapping = logical_mapping(outer['projectdatalist.xml'])
    names = ('FBD_MATRIX.Program.pou', 'FBD_MATRIX.Labels.lh',
             'MODULE_FB.Program.pou', 'MODULE_FB.Labels.lh')
    return {name: base64.b64encode(inner[mapping[name]]).decode('ascii') for name in names}


def preserved(before, after):
    a = read_project_context(before, 'FBD_MATRIX.Program.pou')
    b = read_project_context(after, 'FBD_MATRIX.Program.pou')
    assert a.sources.cpu == b.sources.cpu
    assert [r.raw for r in a.program.iter_records()] == [r.raw for r in b.program.iter_records()]
    assert [r.raw_header for r in a.program.blocks] == [r.raw_header for r in b.program.blocks]
    assert {n: [r.raw for r in d.rows] for n, d in a.declarations.items()} == {
        n: [r.raw for r in d.rows] for n, d in b.declarations.items()}
    st_before = base64.b64decode(source_witness(before)['MODULE_FB.Program.pou'])
    st_after = base64.b64decode(source_witness(after)['MODULE_FB.Program.pou'])
    assert parse_st_pou(st_before).text == parse_st_pou(st_after).text


def audit_phase(directory, cpu, save):
    result = read_json(directory / 'result.json')
    native = events(directory)
    assert result['returncode'] == 0 and not result['observation_errors']
    assert result['compile_completed'] and result['publication_completed']
    assert result['checker_read_correspondence'] == 'current'
    assert result['diagnostic_projection'] == 'completed' and not result['projection_failures']
    assert result['exported'] is save
    targets = {tuple(row) for row in result['selected_targets']}
    assert targets and targets == {tuple(row) for row in result['backend_completed_targets']}
    generated = {row['name']: row['index'] for row in result['generated_resources']}
    for read in result['checker_reads']:
        names = read.get('matches_current_resource_names', [])
        assert names and tuple(read['check_target']) in targets
        assert any(all((directory / f"check-read-{read['serial']}-{channel}.bin").read_bytes() ==
                       (directory / f"pcode-{generated[name]}-{channel}.bin").read_bytes()
                       for channel in range(3)) for name in names)
    assert result['checker_reads']
    selected = lambda operation: [row for row in native if row.get('operation') == operation]
    source = selected('CurrentSourceCheck.AcceptanceObserved')
    assert len(source) == 1 and source[0]['execution_completion'] == 'completed'
    assert source[0]['acceptance'] == 'accepted' and not source[0]['public_program_check_promoted']
    public = selected('ProgramCheckCompleted')
    assert len(public) == 1 and public[0]['targets'] == len(targets) and not public[0]['rejected']
    enabled, disabled = selected('CurrentSourceCheck.NativeHeaderModeEnabled'), selected('CurrentSourceCheck.NativeHeaderModeDisabled')
    assert len(enabled) == len(disabled) == 1
    assert enabled[0]['original_before'] == disabled[0]['original_after'] == 0
    assert enabled[0]['original_after'] == 1 and not enabled[0]['gxw_input_modified']
    assert [row['cpu'] for row in selected('NativeProjectAttributes')] == [cpu]
    configured = selected('OwnedProjectEncoding.Configured')
    if save:
        assert len(configured) == 1 and configured[0]['actual'] == configured[0]['requested'] == 936
    else:
        assert not configured and [row['codepage'] for row in selected('NativeProjectCodePage')] == [936]
    return result


def experiment_directories():
    patterns = ('fbd-native-normalization-20261002-*', 'fbd-project-codepage-20261002-*',
                'fbd-cold-*-20261003-*', 'fbd-native-header-mode-20261003-*',
                'fbd-checked-cpu-roundtrip-20261003-v1')
    return sorted({path for pattern in patterns for path in P.glob(pattern) if path.is_dir()})


def collect():
    rows, scope = read_json(M / 'results.json'), read_json(M / 'scope.json')
    seeds = read_json(P / 'application-fbd-v2-20261002-v1/native-results.json')
    assert len(rows) == len(seeds) == scope['native_seed_options'] == 55
    assert {r['cpu'] for r in rows} == {r['cpu'] for r in seeds}
    assert all(row['status'] == 'passed' for row in rows)
    assert scope['exact_cpu_options'] == 69 and len(scope['unverified_options']) == 14
    public = {}
    witnesses, attempts = [], []
    for row in rows:
        save, cold = Path(row['save']['directory']), Path(row['cold_reopen']['directory'])
        save_result, cold_result = audit_phase(save, row['cpu'], True), audit_phase(cold, row['cpu'], False)
        candidate = (Path(row['directory']) / 'chinese-input.gxw').read_bytes()
        saved = (save / 'native-saved.gxw').read_bytes()
        assert candidate == (save / 'input.gxw').read_bytes()
        assert saved == (cold / 'input.gxw').read_bytes()
        preserved(candidate, saved)
        assert row['save']['native_cpu_engine_key'] == row['cold_reopen']['native_cpu_engine_key']
        witnesses.append(dict(cpu=row['cpu'], prior_scope=Path(row['seed']).name,
            candidate=source_witness(candidate), native_saved=source_witness(saved),
            save=save_result, cold_reopen=cold_result))
        for directory in (save.parent, cold.parent):
            attempts.extend(read_json(directory / 'results.json'))
    domain = read_json(P / 'fbd-cold-source-status-20261003-v5/status-domain-properties.json')
    assert domain['complete_four_flag_domain'] and domain['all_native_payloads_match']
    assert sorted(domain['hypothesis_evaluations']) == list(range(16))
    controls = read_json(P / 'fbd-native-header-mode-20261003-v1/status-controls.json')
    assert [r['source_check'][0]['acceptance'] for r in controls] == ['rejected', 'accepted']
    assert Path(controls[0]['directory'], 'input.gxw').read_bytes() == Path(controls[1]['directory'], 'input.gxw').read_bytes()
    assert all(not row['public_check'] for row in controls)
    missing = read_json(P / 'fbd-native-header-mode-20261003-v2/status-controls.json')[0]
    assert not missing['source_check'] and not missing['public_check']
    assert any(row['code'] == 0x5002000f for row in missing['native_errors'])
    result = dict(schema_version=1, date='2026-10-03',
        environment=dict(native_dll_version='1.635.0.1', python='3.13',
                         ghidra_cli='0.2.2', ghidra='12.1.4', hypothesis='6.168.3'),
        scenario=scope['scenario'], exact_native_menu_options=69, verified_options=55,
        complete_save_phases=55, complete_cold_reopen_phases=55,
        cpu_options=[dict(cpu=row['cpu'], engine_key=row['save']['native_cpu_engine_key'],
                         prior_scope=Path(row['seed']).name) for row in rows],
        unverified_options=scope['unverified_options'],
        retained_attempts=len(attempts), attempt_returncodes=dict(Counter(str(r['returncode']) for r in attempts)),
        prior_checkpoints=['gxw-fbd-application-v2-20261002', 'gxw-native-diagnostic-sources-20261002',
                           'gxw-current-source-check-20261002', 'gxw-source-check-lifetime-20261002'],
        header_mode=dict(module='DZDataABS_DataManager_IEC.dll',
            get_manager_rva='0x1f2de', get_change_flag_rva='0x319da',
            enable_rva='0x32a8a', disable_rva='0x32a95',
            mechanism='Original mode makes declaration change-flag queries return 1 during owned source Check; complete before restoring mode.',
            source_branch=dict(module='SICConverter_IEC.dll', unit_handler_rva='0x251cf', flag_query_return_rva='0x253d8'),
            gxw_bytes_changed_by_mode=False, native_returns_or_diagnostics_patched=False),
        project_encoding=dict(native_project_codepage=936, observed_conversion_codepage=54936,
                              setter_used_on_save=True, setter_used_on_cold_reopen=False),
        finite_property_domain=domain,
        controls=dict(same_input_default_and_mode=controls, missing_source=missing,
            real_backend_negative='Y1 := SIGNAL; remains a 0x050c9300 diagnostic; public projection fails 0x2d010025 and is incomplete.',
            positive_source='Restored original MODULE_FB body without the added diagnostic statement; Chinese names retained.'),
        evidence=dict(repository_archive=PUBLIC.relative_to(ROOT).as_posix(), local_archive=str(LOCAL)),
        limits=['One controlled project family with exact CPU-specific native fixtures and retained prior scope reductions.',
                'Combined CPU menu choices retain their exact menu scope; individual physical variants were not separated.',
                'The other 14 menu options have no retained successful FBD fixture; prior failures do not prove unsupported hardware.',
                'No PLC or simulator execution. Compilation and checks do not establish runtime semantics.',
                'Negative public diagnostic projection remains incomplete; no empty list or backend progress is promoted to success.',
                'Private native ABI is pinned to the recorded DLL version; no application integration is implied.'])
    public['checkpoint.json'] = json_bytes(result)
    public['cpu-source-witnesses.json'] = json_bytes(witnesses)
    public['cpu-attempts.json'] = json_bytes(attempts)
    public['cpu-scope.json'] = json_bytes(scope)
    for name in ('module-matrix-targets.json', 'module-matrix-results.json',
                 'module-combined-v2-results.json', 'module-fx-type-boundaries-results.json'):
        public['prior-cpu-scope/' + name] = (P / 'cpu-fbd-matrix-20261002-v1' / name).read_bytes()
    static = P / 'skill-trial-20261002-v1/ghidra-cli'
    for pattern in ('datamanager-change-*', 'sic-source-unit-*', 'sic-change-mode-*',
                    'sic-build-body.*', 'adapter-build-inner-body.*',
                    'sic-header-parent-body.*', 'sic-header-transform-body.*',
                    'sic-header-complete-body.*', 'PrepareAdapterBuild.java',
                    'PrepareCompilerBuild.java', 'PrepareColdTableOperation.java', 'InspectIecSourceCheck.java'):
        for path in static.glob(pattern):
            if path.is_file():
                public['static-analysis/' + path.name] = path.read_bytes()
    # Source closure for the owned experiment scripts; external packages stay external.
    pending = list(P.glob('probe_fbd_*.py')) + [P / 'native_fbd_body_codec.py', P / 'analyze_fbd_cold_source_status.py']
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        public['tools/' + path.name] = path.read_bytes()
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            pending.extend(P / (module.split('.')[0] + '.py') for module in modules)
    for pattern in ('*.cs', '*.js'):
        for path in P.glob(pattern):
            public['tools/' + path.name] = path.read_bytes()
    for path in (ROOT / 'src/gxw').glob('*.py'):
        public['source/' + path.relative_to(ROOT).as_posix()] = path.read_bytes()
    public['source/research/' + Path(__file__).name] = Path(__file__).read_bytes()
    allowed = {'results.json', 'scope.json', 'status-controls.json', 'status-domain-properties.json',
               'property-evaluations.json', 'normalization-result.json', 'checked-roundtrip.json',
               'positive-control-mutation.json', 'native-events.jsonl', 'observation-events.jsonl',
               'result.json', 'plan.json', 'edit.json', 'run.stdout.bin', 'run.stderr.bin'}
    for directory in experiment_directories():
        for path in directory.rglob('*'):
            if path.is_file() and (path.name in allowed or path.name.startswith(('pcode-', 'check-read-', 'normalized-body-'))):
                public['observations/' + path.relative_to(P).as_posix()] = path.read_bytes()
    return result, public


def write_archive(path, files):
    with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(path) as archive:
        assert set(archive.namelist()) == set(files)
        assert all(archive.read(name) == data for name, data in files.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    if not args.check_only:
        for path in (PUBLIC, LOCAL, RESULT):
            if path.exists():
                raise FileExistsError('Frozen checkpoint already exists: ' + str(path))
    result, files = collect()
    if not args.check_only:
        local = dict(files)
        for directory in experiment_directories():
            for path in directory.rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts:
                    local['raw/' + path.relative_to(P).as_posix()] = path.read_bytes()
        write_archive(LOCAL, local)
        write_archive(PUBLIC, files)
        RESULT.write_bytes(json_bytes(result))
    print(json.dumps(dict(verified_options=result['verified_options'],
        complete_phases=result['complete_save_phases'] + result['complete_cold_reopen_phases'],
        attempts=result['attempt_returncodes'], files=len(files), written=not args.check_only)))


if __name__ == '__main__':
    main()
