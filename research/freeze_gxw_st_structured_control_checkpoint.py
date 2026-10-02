"""Freeze completed structured-control evidence, without native reruns or hashes.

Public archive: authored tools, bounded findings and decoder sources.
Local archive: original experiment inputs, outputs and failures. Original
before-native predictions and earlier stage indexes remain unchanged.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
EXTRA_GROUPS = [
    'q-st-if-else-discovery-20260930',
    'fx-st-if-else-discovery-20260930',
    'q-st-loop-forward-20260930',
    'fx-st-loop-forward-20260930',
    'q-st-for-boundary-discovery-20260930',
    'q-st-for-control-forward-20260930',
    'fx-st-nested-for-forward-20260930',
]
CASE_STAGES = ('forward', 'ranges-forward', 'lifetime-forward', 'context-disc', 'context-forward')


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def retained_file(path, directory):
    relative = path.relative_to(directory)
    if any(part in ('home', 'compiler', 'compiler_DZComp', '__pycache__') for part in relative.parts):
        return False
    return path.suffix.lower() not in ('.exe', '.dll', '.pdb', '.pyc', '.zip')


def main():
    output = ROOT / 'research/results/gxw-st-structured-control-20260930'
    public = ROOT / 'research/evidence/gxw-st-structured-control-20260930.zip'
    local = P / 'gxw-st-structured-control-local-20260930.zip'
    for path in (output, public, local):
        if path.exists():
            raise FileExistsError(path)
    previous = read(P / 'structured-control-continuation-index.json')
    prior_handling = {(r['group'], r['case']): r['handling'] for r in previous['cases']}
    groups = sorted(set([r['group'] for r in previous['cases']] + EXTRA_GROUPS + [
        f'{cpu}-st-case-{stage}-20260930' for cpu in ('q', 'fx') for stage in CASE_STAGES
    ]))
    rows, local_files = [], []
    for group in groups:
        directory = P / group
        comparisons = read(directory / 'comparison.json')
        findings_path = directory / 'case-findings.json'
        findings = {r['case']: r for r in read(findings_path)['cases']} if findings_path.exists() else {}
        for observation in comparisons:
            name = observation['case']
            native = Path(observation['native_directory'])
            generated = list(native.glob('pcode-*.bin'))
            sizes = [f.stat().st_size for f in generated]
            check, reopen = observation.get('check'), observation.get('reopen')
            if check is not None and check['rejected']:
                handling = 'native-generated-check-rejected'
            elif check is not None and reopen and not reopen['check']['rejected']:
                handling = 'native-check-and-reopen-accepted'
            elif check is not None:
                handling = 'native-check-accepted-reopen-unverified'
            elif prior_handling.get((group, name)) == 'experiment-preparation-failure':
                handling = 'experiment-preparation-failure'
            else:
                handling = 'frontend-rejected'
            match = observation.get('predicted_equal', observation.get('before_native_prediction_equal'))
            finding = findings.get(name, {})
            if finding.get('before_native_byte_match') is True:
                match = True
            diagnostics = sorted({hex(d['code']) for d in observation.get('diagnostics', [])} |
                                 set(observation.get('diagnostic_codes', [])))
            rows.append(dict(group=group, case=name, handling=handling,
                             native_directory=str(native.relative_to(ROOT)), generated_bytes=sizes,
                             prospective_byte_match=match, fresh_check=check,
                             reopen_identical=(reopen.get('bytes_identical', reopen.get('identical')) if reopen else None),
                             diagnostics=diagnostics, case_evidence=finding))
        local_files.extend(f for f in directory.rglob('*') if f.is_file() and retained_file(f, directory))
    counts = dict(Counter(r['handling'] for r in rows))
    counts['prospective_byte_matches'] = sum(r['prospective_byte_match'] is True for r in rows)
    output.mkdir()
    manifest = dict(
        created_utc=datetime.now(timezone.utc).isoformat(),
        base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        scope='Bounded source-only ST output research; offline copied Q03UDV and FX3UC projects, native compiler 15.50. Source transplants are not independent real-project lineages.',
        groups=groups, counts=counts, observations=rows,
        findings=[
            'FOR 17d links init155 and bounds15e; bounds link TO and step/body172. Scalar BY stays live, arithmetic BY is captured at the measured loop head.',
            'IF/ELSE captures are prepared in source order; ELSE is emitted first. Tail IF continuations can share end labels. Untouched source and raw native cells are retained.',
            'REPEAT checks after its body. EXIT targets the nearest loop; its end label is allocated when needed. Q JMP and measured FX true-condition CJ remain separate dialects.',
            'Measured Q RETURN followed by immediate caller data, and FOR ending directly in EXIT, generate code rejected by fresh ProgramCheck with 0x050c9322. Generation is not acceptance.',
            'Standalone CASE streams frontend arms. CASE under measured IF/FOR/CASE builds 173 (selector L12, arm list L16) and uses generator d8f10. This resolves the earlier stage index that left 173 opaque.',
            'Context-bound selection projection: 3a choices L12/body L16; 175 default body L16; positive INT112, range189 and ordered selection-list2c. Other fields and uses of 2c remain opaque.',
            'Standalone CASE retains captures progressively per arm. Released arithmetic-index scratch can become the next arm capture. Enclosing IF/FOR instead prepares subtree captures first: measured three-word standalone cases accept while three-word IF rejects and four-word IF accepts.',
            'CASE end label is allocated at its first emitted arm exit, after the first arm body. Nested CASE labels therefore precede the enclosing CASE end. No-ELSE retains separate failed-arm and common-end labels.',
            'Current CASE stage has 38 native observations: 34 check/reopen accepted, 4 frontend capacity rejections; 24 source predictions saved before native all match bytes. Independent lexical decode and finite initialized witnesses are separate from PLC execution.',
        ],
        preservation='No native rerun, hash computation or rewriting of earlier predictions/indexes. Preparation failures, compiler rejections and post-observation fits remain distinct.',
        archives=dict(public=str(public.relative_to(ROOT)), local=str(local)),
        limitations=[
            'No PLC/device/simulator execution and no production-safe promotion.',
            'Opt-in source model covers bounded INT choices, initialized ASCII string assignments and measured control forms; unsupported leaves fail closed.',
            'Earlier IF snapshots may truncate unused-cell inventory; original truncation indicators are preserved.',
            'Local archive contains third-party engineering projects and source; redistribution rights are not established.',
            'Existing event files may contain historical fingerprint metadata; this snapshot computes no new hashes.',
        ],
    )
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (output / 'working-diff.patch').write_bytes(subprocess.check_output([
        'git', 'diff', '--', 'research/probe_gxw_parameters.py', 'research/replay_gxw_workspace.py'
    ], cwd=ROOT))
    authored = list(P.glob('*.py')) + list(P.glob('*.js'))
    authored += [ROOT / 'research/probe_gxw_parameters.py', ROOT / 'research/replay_gxw_workspace.py',
                 ROOT / 'research/probe_gxw_program_check_resources.py',
                 Path(__file__), ROOT / 'LICENSE', ROOT / 'AGENTS.md']
    authored += list((ROOT / 'src/gxw').rglob('*.py')) + list(output.iterdir())
    # Preserve mutable planning/index/static notes as local research context.
    local_files += [f for f in P.iterdir() if f.is_file() and f.suffix.lower() in ('.json', '.txt')]
    for target, selected in ((public, authored), (local, local_files + authored)):
        entries = []
        with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(set(selected)):
                name = path.relative_to(ROOT).as_posix()
                archive.write(path, name)
                entries.append(dict(path=name, bytes=path.stat().st_size))
            archive.writestr('checkpoint-files.json', json.dumps(entries, ensure_ascii=False, indent=2))
        # Inspect the central-directory inventory only; no hashes or native reruns.
        with zipfile.ZipFile(target) as archive:
            assert len(archive.infolist()) == len(entries)+1
            assert sum(i.file_size for i in archive.infolist() if i.filename != 'checkpoint-files.json') == sum(e['bytes'] for e in entries)
        print(json.dumps(dict(path=str(target), files=len(entries), bytes=target.stat().st_size)))
    print(json.dumps(dict(completed_observations=len(rows), selected_groups=len(groups), counts=counts)))


if __name__ == '__main__':
    main()
