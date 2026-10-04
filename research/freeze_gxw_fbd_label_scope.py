"""Freeze the completed source-scope edit module and exact CPU boundaries.

This audits stored offline experiments. No native DLL is called, no old
checkpoint is replaced, and archive verification compares the original bytes.
Full GXW projects remain in the local archive, as in earlier FBD checkpoints.
"""
from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
M = P / 'cpu-fbd-matrix-20261002-v1'
STEM = 'gxw-fbd-label-scope-20261004'
PUBLIC = ROOT / 'research/evidence' / (STEM + '.zip')
LOCAL = P / (STEM + '-local.zip')
RESULT = ROOT / 'research/results' / (STEM + '.json')
FX_RANGE = P / 'native-projection-drain-20261004-v30/fx0n-scope-range-a2'
FX_SCOPE = P / 'native-projection-drain-20261004-v25/cold-fb-shadow-a2'
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'research'), str(P)]
from gxw.callable_sources import ProjectCallableSources
from gxw.container_writer import validate_cfb_streams
from gxw.declarations import parse_declarations
from gxw.object_model import read_project_context
from gxw.project_metadata import logical_mapping, read_project_compile_options, read_project_text_context
from gxw.structured_pou import parse_structured_pou
from probe_cpu_fbd_matrix import evaluate_label_scope_observations
from probe_inline_fb_identity import current_references, current_caller_contexts, project_st_in_contexts
from program_check_evidence import analyze_directory


PORTS = [('SIGNAL', 'in', 3, 'BOOL'), ('in.STATE', 'in', 5, 'WORD'),
         ('RESULT', 'out', 4, 'BOOL'), ('out.STATE', 'out', 5, 'WORD')]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def data(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def source_witness(raw, edited):
    context = read_project_context(raw, 'FBD_MATRIX.Program.pou')
    name = '混合范围实例_STAGE_A' if edited else 'TIMER_A'
    model = context.object_model()
    call = next(n for n in model['nodes'] if n['symbol'] == name)
    assert [(p['name'], p['side'], p['class_code'], p['data_type']) for p in call['ports']] == PORTS
    local = context.sources.label(name)
    assert local[0] == 'FBD_MATRIX.Labels.lh' and local[1].data_type == 'MODULE_FB'
    global_ = next(r for r in context.declarations['Global1.gh'].rows if r.name == 'TIMER_A')
    assert (global_.data_type, global_.device) == ('BOOL', 'Y2')
    assert global_.comment == ('保留隐藏全局声明' if edited else '')
    assert any(n['template'] == 'input' and n['symbol'] == 'TIMER_A变' for n in model['nodes'])
    outer = validate_cfb_streams(raw)
    inner = validate_cfb_streams(outer['_hdb'])
    mapping = logical_mapping(outer['projectdatalist.xml'])
    project = next(n for n in mapping if n.endswith('.prj'))
    metadata = inner[mapping[project]]
    assert read_project_compile_options(metadata)['global_variable_hiding'] is True
    return dict(cpu=context.sources.cpu, edited=edited, instance=name,
        project_metadata_base64=base64.b64encode(metadata).decode('ascii'),
        program_base64=base64.b64encode(context.program.raw).decode('ascii'),
        declarations={n: base64.b64encode(d.raw).decode('ascii') for n, d in context.declarations.items()})


def public_source_replay(witness):
    metadata = base64.b64decode(witness['project_metadata_base64'])
    assert read_project_text_context(metadata)['cpu'] == witness['cpu']
    options = read_project_compile_options(metadata)
    assert options['global_variable_hiding'] is True
    declarations = {n: parse_declarations(base64.b64decode(v), logical_name=n)
                    for n, v in witness['declarations'].items()}
    sources = ProjectCallableSources(witness['cpu'], 'FBD_MATRIX.Program.pou', declarations, {},
                                    global_variable_hiding=options['global_variable_hiding'])
    program = parse_structured_pou(base64.b64decode(witness['program_base64']),
                                   logical_name='FBD_MATRIX.Program.pou')
    call = next(n for n in program.nodes if n.symbol == witness['instance'])
    actual = sources.callable(call)['ports']
    assert [(p['name'], p['side'], p['class_code'], p['data_type']) for p in actual] == PORTS
    assert sources.label(witness['instance'])[0] == 'FBD_MATRIX.Labels.lh'


def audit_phase(directory, cpu):
    audited = analyze_directory(directory)
    assert audited['compilation']['completed'] and audited['compilation']['acceptance'] == 'accepted'
    assert audited['publication']['completed']
    assert audited['checker_resources']['correspondence'] == 'current'
    assert audited['checker_resources']['reads'] and not audited['checker_resources']['observation_errors']
    native = [json.loads(line) for line in (directory / 'native-events.jsonl').read_text(
        encoding='utf-8-sig').splitlines()]
    targets = [e for e in native if e.get('operation') == 'ProgramCheckTargetOutcome']
    assert targets and all(e['backend_check'] == 'completed-rejected' and e['terminal_progress_observed']
        and e['native_error_observations'] == 2 and e['native_source_locations'] == 'completed'
        and e['public_adapter_projection'] == 'not-called' for e in targets)
    assert [e['cpu'] for e in native if e.get('operation') == 'NativeProjectAttributes'] == [cpu]
    return dict(compilation='completed-accepted', publication='completed', checker_reads='current',
        backend_check='completed-rejected', expected_diagnostic='two intentional duplicate Y1 outputs',
        native_source_queries='completed', public_adapter_projection='not-called', runtime_execution='not-tested')


def collect():
    rows = read(M / 'module-label-scope-v5-results.json')
    targets = read(M / 'module-matrix-targets.json')
    assert len(rows) == len(targets) == 69
    assert {r['cpu'] for r in rows} == {r['cpu'] for r in targets}
    witnesses, cpu_rows = [], []
    for stored in rows:
        row = evaluate_label_scope_observations(deepcopy(stored))
        summary = dict(cpu=row['cpu'], target=row['target'], status=row['status'],
                       source_binding=row.get('source_binding_status', 'not-verified'))
        if summary['source_binding'] != 'verified':
            summary['factory'] = row.get('factory')
            native = row.get('original_native', {})
            summary['compilation'] = native.get('compile_status', 'not-reached')
            summary['diagnostics'] = [{k: d[k] for k in ('kind', 'code', 'name', 'arguments')}
                                      for d in native.get('compile_reports', []) if d['kind'] != 1]
            cpu_rows.append(summary)
            continue
        assert row['source_cpu'] == row['cpu'] and row['parameter_cpu_word'] == row['target']['es_cpu']
        directory = Path(row['directory'])
        original = (directory / 'scope-source.gxw').read_bytes()
        edited = (directory / 'scope-edited.gxw').read_bytes()
        saved = (Path(row['edited_native']['directory']) / 'native-saved.gxw').read_bytes()
        cold = (Path(row['cold_native']['directory']) / 'native-saved.gxw').read_bytes()
        phases = {}
        for stage in ('original_native', 'edited_native', 'cold_native'):
            phases[stage] = audit_phase(Path(row[stage]['directory']), row['cpu'])
        assert (Path(row['cold_native']['directory']) / 'input.gxw').read_bytes() == saved
        for value in (saved, cold):
            before = read_project_context(edited, 'FBD_MATRIX.Program.pou')
            after = read_project_context(value, 'FBD_MATRIX.Program.pou')
            assert [r.raw for r in before.program.iter_records()] == [r.raw for r in after.program.iter_records()]
        assert {p.name: p.read_bytes() for p in Path(row['edited_native']['directory']).glob('pcode-*.bin')} == {
            p.name: p.read_bytes() for p in Path(row['cold_native']['directory']).glob('pcode-*.bin')}
        current = {key: source_witness(value, key != 'original') for key, value in
                   (('original', original), ('edited', edited), ('native_saved', saved), ('cold_saved', cold))}
        for witness in current.values():
            public_source_replay(witness)
        witnesses.append(dict(cpu=row['cpu'], sources=current))
        summary.update(phases=phases, hiding_disabled_control='completed-rejected/0x500f1055',
                       cold_pcode_bytes_exact=True, native_diagnostic_sources=row['native_diagnostic_sources'])
        cpu_rows.append(summary)
    counts = Counter(r['source_binding'] for r in cpu_rows)
    assert counts == {'verified': 55, 'not-verified': 14}
    scope_properties = read(FX_SCOPE / 'label-scope-edit-properties.json')
    assert scope_properties['checks']['coherent_edit_sequences'] == 100
    assert scope_properties['checks']['invalid_binding_edits'] == 9
    range_properties = read(FX_RANGE / 'native-range-properties.json')
    assert range_properties['checks'] == dict(native_seed_locations=2, coherent_generated=200, contradictions=36, ambiguities=2)
    range_raw = (FX_RANGE / 'native-saved.gxw').read_bytes()
    refs = current_references(FX_RANGE)
    contexts, texts = current_caller_contexts(range_raw, refs)
    graphs = {name: c.object_model()['nodes'] for name, c in contexts.items()}
    correlations = []
    for line in (FX_RANGE / 'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines():
        event = json.loads(line)
        if event.get('operation') != 'ProgramCheckNativeSourceLocations':
            continue
        for entry in event['locations']:
            query, original = entry['source_location'], entry['original']
            adapted = dict(query, query=dict(resource=query['resource'], start_step=query['code_step'],
                           original_kind=original['kind'], original_code=original['code']))
            actual = project_st_in_contexts(adapted, refs, texts, graphs)
            name, bbox = {1: ('混合范围实例_STAGE_A', [8, 2, 13, 6]), 3: ('TIMER_B', [21, 2, 26, 6])}[original['step']]
            assert actual['status'] == 'uniquely_correlated' and actual['instance'] == 'FBD_MATRIX.' + name
            assert actual['caller']['bbox'] == bbox and actual['source']['pou'] == 'MODULE_FB'
            assert actual['source']['zero_based_line'] == 0 and 'debug_evidence' not in actual
            correlations.append(actual)
    assert len(correlations) == 2
    fx = next(r for r in rows if r['cpu'] == 'FX0N')
    for channel in range(3):
        assert (FX_RANGE / f'pcode-0-{channel}.bin').read_bytes() == (
            Path(fx['cold_native']['directory']) / f'pcode-0-{channel}.bin').read_bytes()
    result = dict(schema=1, date='2026-10-04', status='core-integrated/native-validated-bounded-module',
        baseline_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
        environment=dict(native_component_version='1.635.0.1', project_codepage=936,
                         ghidra_cli='0.2.2', ghidra='12.1.4', hypothesis='6.168.3'),
        mechanism=dict(option_tag='0x10010000', enabled_low_bit=1, saved_storage_bound_including_sentinel=32,
            saved_array_location='codepage DWORD + 88-byte SystemVariablesInfo + counted DWORD array',
            native_flag_owner='DZDataABS_DataManager_IEC.OnMemoryDataManager', native_flag_offset='0x172',
            native_disabled_error='0x500f1055', native_enabled_warning='0x500f1056'),
        cpu_menu_options=69, source_binding_verified=55, native_direct_fbd_source_options=54,
        native_inline_st_source_options=['FX0N'], cpu_options=cpu_rows,
        core_semantics='Current project option selects the unique local FB over the same-named global BOOL; scope edits preserve the other declaration and Unicode identifiers.',
        property_checks=dict(scope_edits=scope_properties, fx0n_native_ranges=range_properties,
            mixed_q03udv_fx0n_native_ranges=read(P / 'native-projection-drain-20261004-v3/inverse-a2/native-range-properties.json')),
        fx0n_instance_mapping=dict(cpu='FX0N', native_cpu=514, converter='ECCodeGeneratorFX2.dll/15.31',
            method='original GetPCodeRange with current instance paths; independently agrees with same-compile debug intervals',
            correlations=correlations),
        core_regression=dict(command='python -X utf8 -m pytest tests/test_gxw_object_model.py tests/test_gxw_project_writer.py tests/test_gxw_declarations.py tests/test_gxw_lossless.py -q', passed=524),
        evidence=dict(repository_archive=PUBLIC.relative_to(ROOT).as_posix(), local_archive=str(LOCAL)),
        limits=['Two controlled source families: frozen source-library FLOW_PORTS and native-created MODULE_FB; this is not independent real-world project coverage.',
            'Merged CPU menu options retain their original names. No individual physical variant or PLC/simulator execution is certified.',
            'The 14 creation/compile failures remain unverified. A helper failure or rejected sample does not establish native lack of FBD support.',
            'Native acceptance, current resource reads, backend rejection, source mapping, save and cold reopen are separate results.',
            'Public adapter checks were not called in this module; Q 0x2d010025 conversion failure remains unresolved.',
            'One-program editing still rejects global renames when other program POUs exist; only a hidden global field edit is claimed in the CPU matrix.',
            'Duplicate declarations within a scope and unreadable optional project options remain conservative binding gaps.',
            'IN_OUT endpoint binding and physical D30/D40 references do not prove execution or copyback semantics.'])
    return result, witnesses


def archive_sources(*, native_lifecycle=False):
    public, local = {}, {}
    for path in (ROOT / 'src/gxw').glob('*.py'):
        public['source/' + path.relative_to(ROOT).as_posix()] = path
    for name in ('test_gxw_object_model.py', 'test_gxw_declarations.py', 'test_gxw_lossless.py', 'test_gxw_project_writer.py'):
        public['source/tests/' + name] = ROOT / 'tests' / name
    for path in (ROOT / 'tests/fixtures').glob('gxw*.json'):
        public['source/' + path.relative_to(ROOT).as_posix()] = path
    for name in ('freeze_gxw_fbd_label_scope.py', 'program_check_evidence.py', 'native_gxw_tokens.py'):
        public['source/research/' + name] = ROOT / 'research' / name
    pending = [P / name for name in ('run_fbd_module_cpu_matrix.py', 'probe_diagnostic_properties.py',
                                   'probe_inline_fb_identity.py', 'probe_native_projection_drain.py')]
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        public['tools/' + path.name] = path
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                       [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            pending.extend(P / (module.split('.')[0] + '.py') for module in modules)
    for pattern in ('*.cs', '*.js'):
        for path in P.glob(pattern):
            public['tools/' + path.name] = path
    if native_lifecycle:
        roots=list(M.glob('factory-*-module-native-decl-v2'))
        roots+=list(M.glob('factory-*-module-native-declaration-lifecycle-v1'))
        roots+=list(M.glob('factory-*-native-decl-v3-boundary'))
    else:
        roots = list(M.glob('factory-*-module-label-scope-v*')) + [M / 'scope-global-donor-v1']
        roots += [P / ('native-projection-drain-20261004-v' + str(n)) for n in (24, 25, 26, 28, 29, 30)]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob('*'):
            if not path.is_file() or 'home' in path.relative_to(root).parts:
                continue
            if path.suffix not in ('.py', '.json', '.jsonl', '.cs', '.js', '.bin', '.gxw', '.dat', '.ldb', '.txt', '.tsv'):
                continue
            key = 'observations/' + path.relative_to(P).as_posix()
            (local if path.suffix == '.gxw' else public)[key] = path
    names=('module-native-decl-v2-results.json','module-native-decl-v2-progress.json','module-native-decl-v2-targets.json',
        'native-decl-v3-boundary-results.json','native-decl-v3-boundary-progress.json',
        'module-native-declaration-lifecycle-v1-results.json','module-label-scope-v5-results.json') if native_lifecycle else (
        'module-label-scope-v5-results.json', 'module-label-scope-v5-progress.json', 'module-matrix-targets.json')
    for name in names:
        public['observations/cpu-fbd-matrix-20261002-v1/' + name] = M / name
    static = P / 'skill-trial-20261002-v1/ghidra-cli'
    patterns=('local-variable-native-*-20261004.*',) if native_lifecycle else (
        'label-*-20261004.*', 'scope-new-project-options-20261004.*', 'native-range-*-20261004.*')
    for pattern in patterns:
        for path in static.glob(pattern):
            public['static/' + path.name] = path
    if native_lifecycle:
        # Preserve original failed factory evidence without copying the prior
        # completed scope module or altering its frozen checkpoint.
        prior=read(M/'module-label-scope-v5-results.json')
        for row in prior:
            if row.get('factory',{}).get('exported'):continue
            for path in Path(row['directory']).glob('*'):
                if path.is_file() and path.suffix in ('.json','.jsonl','.cs','.bin'):
                    public['observations/'+path.relative_to(P).as_posix()]=path
        return public,local
    for path in (FX_SCOPE / 'label-scope-edit-counterexample.json',
                 P / 'native-nested-unused-candidate/fx-global-fb-shadow.json'):
        public['observations/' + path.relative_to(P).as_posix()] = path
    return public, local


def lifecycle_witness(raw):
    context=read_project_context(raw,'FBD_MATRIX.Program.pou')
    outer=validate_cfb_streams(raw);inner=validate_cfb_streams(outer['_hdb'])
    mapping=logical_mapping(outer['projectdatalist.xml'])
    metadata=inner[mapping[next(n for n in mapping if n.endswith('.prj'))]]
    return dict(cpu=context.sources.cpu,project_metadata_base64=base64.b64encode(metadata).decode('ascii'),
        program_base64=base64.b64encode(context.program.raw).decode('ascii'),
        declarations={n:base64.b64encode(d.raw).decode('ascii') for n,d in context.declarations.items()})


def replay_lifecycle(witness):
    from gxw.object_model import export_object_model
    metadata=base64.b64decode(witness['project_metadata_base64'])
    assert read_project_text_context(metadata)['cpu']==witness['cpu']
    options=read_project_compile_options(metadata)
    documents={n:parse_declarations(base64.b64decode(v),logical_name=n) for n,v in witness['declarations'].items()}
    sources=ProjectCallableSources(witness['cpu'],'FBD_MATRIX.Program.pou',documents,{},
        global_variable_hiding=options['global_variable_hiding'])
    program=parse_structured_pou(base64.b64decode(witness['program_base64']),logical_name='FBD_MATRIX.Program.pou')
    graph=export_object_model(program,documents,sources=sources)
    assert graph['schema_version']==2 and graph['cpu']==witness['cpu'] and not graph['issues']
    calls=[n for n in graph['nodes'] if n['template']=='function_block:MODULE_FB']
    assert {n['symbol'] for n in calls}=={'矩阵实例_NATIVE_STAGE_A','NATIVE_CREATED_FB_STAGE_B'}
    assert all([(p['name'],p['side'],p['class_code'],p['data_type']) for p in n['ports']]==PORTS for n in calls)
    labels={r.name:r for r in documents['FBD_MATRIX.Labels.lh'].rows}
    assert set(labels)=={'矩阵实例_NATIVE_STAGE_A','NATIVE_CREATED_FB_STAGE_B','TIMER_A变'}
    created=labels['NATIVE_CREATED_FB_STAGE_B']
    assert (created.data_type,created.type_code,created.type_reference,created.class_code)==('MODULE_FB',0,'',1)
    assert sources.declaration_kind(created)=='function_block'
    assert any(n['template']=='input' and n['symbol']=='TIMER_A变' for n in graph['nodes'])
    assert [(n['symbol'],n['x'],n['y']) for n in graph['nodes'] if n['symbol'] in ('D50','D51')]==[
        ('D50',19,11),('D51',26,11)]
    return program,documents


def audit_lifecycle_phase(directory,cpu):
    audited=analyze_directory(directory)
    assert audited['compilation']['completed'] and audited['compilation']['acceptance']=='accepted'
    assert audited['publication']['completed'] and audited['checker_resources']['correspondence']=='current'
    assert audited['checker_resources']['reads'] and not audited['checker_resources']['observation_errors']
    native=[json.loads(line) for line in (directory/'native-events.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    targets=[e for e in native if e.get('operation')=='ProgramCheckTargetOutcome']
    assert targets and all(t['backend_check']=='completed-accepted' and t['native_error_observations']==0
        and t['terminal_progress_observed'] and t['native_resource_end_marker_observed']
        and t['completed_resource']=='MAIN' and t['public_adapter_projection']=='not-called' for t in targets)
    assert [e['cpu'] for e in native if e.get('operation')=='NativeProjectAttributes']==[cpu]
    export=[e for e in native if e.get('operation')=='NativeExport']
    assert len(export)==1 and export[0]['build_rejected'] is False and export[0]['check_rejected'] is False
    return dict(compile='completed-accepted',publication='completed',checker_reads='current',
        backend_check='completed-accepted',resource='MAIN',resource_end_marker='observed',
        diagnostic_errors=0,public_adapter_projection='not-called',native_save='completed',runtime_execution='not-tested')


def collect_lifecycle():
    rows=read(M/'module-native-decl-v2-results.json')
    targets=read(M/'module-native-decl-v2-targets.json')
    controls=read(M/'native-decl-v3-boundary-results.json')
    assert len(rows)==len(targets)==69 and {r['cpu'] for r in rows}=={r['cpu'] for r in targets}
    assert {r['cpu'] for r in controls}=={'FX0S/FX0','FX1','LJ72GF15-T2','LJ72MS15'}
    overlay={r['cpu']:r for r in controls}
    witnesses,cpu_rows=[],[]
    for original in rows:
        row=overlay.get(original['cpu'],original)
        summary=dict(cpu=row['cpu'],target=row['target'],status=row['status'],directory=row['directory'],
            prior_status=row['prior_status'])
        if row['status']!='verified-native-source-lifecycle':
            native=row.get('edited_native',{})
            summary.update(compile=native.get('compile_status','not-run'),native_changes=native.get('native_source_changes',[]),
                diagnostics=[d for d in native.get('compile_reports',[]) if d['kind'] in (2,3)],
                prior_factory=row.get('prior_factory'),lifecycle_verified=False,
                unsupported_cpu_claim=False)
            cpu_rows.append(summary);continue
        assert row['parameter_cpu_word']==row['target']['es_cpu'] and row['native_cpu_identity_exact']
        edited,cold=(Path(row[key]['directory']) for key in ('edited_native','cold_native'))
        phases={stage:audit_lifecycle_phase(directory,row['cpu']) for stage,directory in (('edited',edited),('cold',cold))}
        saved=(edited/'native-saved.gxw').read_bytes();cold_saved=(cold/'native-saved.gxw').read_bytes()
        assert (cold/'input.gxw').read_bytes()==saved
        original_context=read_project_context((edited/'input.gxw').read_bytes(),'FBD_MATRIX.Program.pou')
        snapshots={stage:lifecycle_witness(value) for stage,value in (('native_saved',saved),('cold_saved',cold_saved))}
        body=(Path(row['directory'])/'body-after.bin').read_bytes()
        assert (edited/'native-body-after-0.bin').read_bytes()==body
        parsed=[]
        for witness in snapshots.values():
            program,documents=replay_lifecycle(witness);parsed.append((program,documents))
            from gxw.source_header import source_payload_offset
            import struct
            offset=source_payload_offset(program.raw)+5
            size=struct.unpack_from('<I',program.raw,offset)[0]
            assert offset+size+24==len(program.raw) and program.raw[offset:offset+size]==body
            before={r.name:r for r in original_context.declarations['FBD_MATRIX.Labels.lh'].rows}
            after={r.name:r for r in documents['FBD_MATRIX.Labels.lh'].rows}
            assert before['TIMER_A变'].raw==after['TIMER_A变'].raw
            assert [r.raw for r in program.unknown_records]==[r.raw for r in original_context.program.unknown_records]
            for name,document in original_context.declarations.items():
                if name!='FBD_MATRIX.Labels.lh':assert [r.raw for r in document.rows]==[r.raw for r in documents[name].rows]
        assert parsed[0][0].raw==parsed[1][0].raw
        assert {n:d.raw for n,d in parsed[0][1].items()}=={n:d.raw for n,d in parsed[1][1].items()}
        code_files={p.name:p.read_bytes() for p in edited.glob('pcode-*.bin')}
        assert len(code_files)==3 and code_files=={p.name:p.read_bytes() for p in cold.glob('pcode-*.bin')}
        changes=row['edited_native']['native_source_changes']
        assert [e['operation'] for e in changes]==['NativeRemovedVariableLookup','NativeLocalVariableRemove',
            'NativeLocalVariableCreate','NativeLocalVariableTypeChange','NativeLocalVariableRename','NativeBodyWrite']
        removed_lookup=changes[0]
        assert removed_lookup['hresult']==0 and removed_lookup['code']==0 and removed_lookup['id']==[0]*12
        assert removed_lookup['resolves_declared_name'] is False
        assert changes[3]['before']=='BOOL' and changes[3]['after']=='MODULE_FB'
        created_native_id=changes[2]['created']['id']
        created_saved=next(r for r in row['saved_instance_fields'] if r['name']=='NATIVE_CREATED_FB_STAGE_B')
        assert created_native_id[6]==4 and created_saved['record_id']==(0 if row['cpu']=='FX0N' else 2)
        witnesses.append(dict(cpu=row['cpu'],sources=snapshots))
        summary.update(lifecycle_verified=True,phases=phases,saved_fields=row['saved_instance_fields'],
            cold_pcode_bytes_exact=True,source_body_exact=True,untouched_declarations_exact=True,
            created_native_object_id=created_native_id,created_saved_record_id=created_saved['record_id'],
            original_native_operations=changes)
        cpu_rows.append(summary)
    counts=Counter(r['status'] for r in cpu_rows)
    assert counts=={'verified-native-source-lifecycle':55,'native-lifecycle-compile-or-save-failed':4,
        'not-verified-prior-project-boundary':10}
    findings=read(P/'ul42-valid/user-library-binding-observations.json')
    result=dict(schema=1,date='2026-10-04',status='bounded-native-source-module-verified/core-reader-integrated',
        baseline_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),
        environment=dict(native_component_version='1.635.0.1',project_codepage=936,ghidra_cli='0.2.2',ghidra='12.1.4'),
        cpu_menu_options=69,native_lifecycle_verified=55,edited_source_compile_rejected=4,prior_factory_unverified=10,
        cpu_options=cpu_rows,source_provenance='native-created controlled MODULE_FB and FBD callers; same project family across CPUs',
        methods=dict(get_body=1692,set_body=1688,get_local_table=1748,get_local_variable=1756,create_element=1976,
            delete_object=20,set_name=60,set_variable_type=1552,set_variable_class=1644),
        findings=['Created and BOOL-to-FB-retyped native variables retain type_code 0 and an empty type_reference after accepted compilation and cold reopen.',
            'Current declared type and CPU-bound callable source define the interface; auxiliary serialized fields alone do not identify an FB.',
            'A deleted native local lookup can return HRESULT 0/code 0 with an all-zero object ID; success does not prove object existence.',
            'The created live variable object ID has seventh DWORD 4; the saved declaration record_id is 2 in 54 controls and 0 for FX0N. Those values are not interchangeable; their storage role is not inferred.',
            'Deletion, creation, type mutation, variable-length rename and FBD body mutation are observed through original workspace APIs and exact source readback.'],
        related_independent_fx3g_library_scope=dict(cpu=findings['cpu'],native_cpu=findings['native_cpu'],
            converter_version=findings['converter_version'],codepage=findings['codepage'],
            evidence=str(P/'ul42-valid/user-library-binding-observations.json'),
            limits='Independent ST library definitions and controlled FBD callers; not additional cross-CPU real-project coverage.'),
        core_regression=findings['core_validation'],property_checks=findings['property_check'],
        commands=dict(native_matrix='python research/experiments/sfc-graph-20260926/public-corpus-discovery/run_fbd_module_cpu_matrix.py --native-declaration-lifecycle --pass-name module-native-decl-v2',
            rejected_controls='python research/experiments/sfc-graph-20260926/public-corpus-discovery/run_fbd_module_cpu_matrix.py --native-declaration-lifecycle --pass-name native-decl-v3-boundary --cpu FX0S/FX0 --cpu FX1 --cpu LJ72GF15-T2 --cpu LJ72MS15',
            freeze='python research/freeze_gxw_fbd_label_scope.py --native-declaration-lifecycle',
            replay='python research/freeze_gxw_fbd_label_scope.py --native-declaration-lifecycle --replay'),
        evidence=dict(repository_archive=PUBLIC.relative_to(ROOT).as_posix(),local_archive=str(LOCAL)),
        limits=['The 55 verified entries are exact native menu choices; combined names retain their actual ranges. No extrapolation to other CPU options.',
            'Four edited-source compile rejections and ten prior factory failures remain unverified, not native unsupported declarations.',
            'FB-to-different-FB interface mutation and independent ST library import evidence remain scoped to their recorded FX3G controls.',
            'This module uses the original backend check. Q public diagnostic conversion 0x2d010025 remains unresolved.',
            'Declaration/body writes remain a bounded research helper and are not yet a production native write service or Web/MCP editing path.',
            'No PLC/simulator execution. Correct binding, native acceptance and cold reopen do not establish runtime behavior.',
            'The initial longer experiment directories failed at native import 0x2d020013; shorter-directory controls succeeded. No exact path limit is inferred.'])
    return result,witnesses


def write_archive(path, files, values):
    with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name, source in sorted(files.items()):
            assert not Path(name).is_absolute() and '..' not in Path(name).parts
            output.write(source, name)
        for name, value in sorted(values.items()):
            assert name not in files
            output.writestr(name, value)
    with zipfile.ZipFile(path) as archived:
        assert len(archived.namelist()) == len(files) + len(values)
        assert all(archived.read(name) == source.read_bytes() for name, source in files.items())
        assert all(archived.read(name) == value for name, value in values.items())
    return dict(entries=len(files) + len(values), bytes=path.stat().st_size, entry_bytes_exact=True)


def main():
    global STEM,PUBLIC,LOCAL,RESULT
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit-only', action='store_true')
    parser.add_argument('--replay', action='store_true')
    parser.add_argument('--native-declaration-lifecycle',action='store_true')
    options = parser.parse_args()
    if options.native_declaration_lifecycle:
        STEM='gxw-fbd-native-declarations-20261004'
        PUBLIC=ROOT/'research/evidence'/(STEM+'.zip')
        LOCAL=P/(STEM+'-local.zip')
        RESULT=ROOT/'research/results'/(STEM+'.json')
    if options.replay:
        with zipfile.ZipFile(PUBLIC) as archive:
            rows = json.loads(archive.read('cpu-source-witnesses.json'))
            for row in rows:
                for witness in row['sources'].values():
                    (replay_lifecycle if options.native_declaration_lifecycle else public_source_replay)(witness)
        print(json.dumps(dict(cpu_options=len(rows), source_snapshots=sum(len(r['sources']) for r in rows),
                              scope='offline original source witness replay; not new native execution')))
        return
    if not options.audit_only and any(path.exists() for path in (PUBLIC, LOCAL, RESULT)):
        raise FileExistsError('Frozen checkpoint must not be replaced')
    result, witnesses = collect_lifecycle() if options.native_declaration_lifecycle else collect()
    files, local_files = archive_sources(native_lifecycle=options.native_declaration_lifecycle)
    print(json.dumps(dict(cpu_options=69, source_binding_verified=55, unverified=14,
        public_entries=len(files), local_project_entries=len(local_files))), flush=True)
    if options.audit_only:
        return
    result['archives'] = dict(repository=write_archive(PUBLIC, files,
        {'checkpoint.json': data(result), 'cpu-source-witnesses.json': data(witnesses)}),
        local=write_archive(LOCAL, local_files, {}))
    RESULT.write_bytes(data(result))
    print(json.dumps(dict(result=RESULT.relative_to(ROOT).as_posix(), archives=result['archives'])), flush=True)


if __name__ == '__main__':
    main()
