"""Preserve the member-array extension without replacing earlier checkpoints.

Read completed observations only; do not rerun native experiments or calculate
hashes. Third-party source and native traces stay in a local evidence archive.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
GROUPS = ['fx3g-member-arrays-forward-20260930',
          'fx3g-nested-structure-discovery-20260930']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    output = ROOT / 'research/results/gxw-fx-member-arrays-20260930'
    public = ROOT / 'research/evidence/gxw-fx-member-arrays-20260930.zip'
    local = P / 'gxw-fx-member-arrays-local-20260930.zip'
    for path in (output, public, local):
        if path.exists():
            raise FileExistsError(path)
    rows = []
    for group in GROUPS:
        for row in read(P / group / 'comparison.json'):
            check = row['check']
            reopen = row.get('reopen')
            if check:
                assert not check['rejected'] and reopen
                assert not reopen['check']['rejected'] and reopen['bytes_identical']
                status = 'native-check-and-reopen-accepted'
            else:
                status = 'native-declaration-refusal'
            rows.append(dict(group=group, case=row['case'], status=status,
                             generated_bytes=row['generated_bytes'],
                             original_prospective_equal=row['prospective_equal'],
                             original_source_model_refusal=row.get('expected_error'),
                             diagnostics=row['diagnostics'], process=row['process'],
                             reopen_identical=reopen['bytes_identical'] if reopen else None,
                             evidence_directory=(P / group / row['case']).relative_to(ROOT).as_posix()))
    layout = read(P / 'fx3g-member-arrays-forward-20260930-layout-summary.json')
    assert layout['observations'] == layout['prospective_full_byte_matches'] == 3
    assert all(r['current_source_model_equal'] and r['global_block_projection_equal']
               and r['independent_address_projection_match'] for r in layout['cases'])
    counts = dict(Counter(r['status'] for r in rows))
    counts.update(native_observations=len(rows),
                  prospective_full_byte_matches=sum(r['original_prospective_equal'] is True for r in rows),
                  independent_cache_member_references=sum(r['independent_cache_member_reference_matches'] for r in layout['cases']))
    output.mkdir()
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(),
                    base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    previous_checkpoint='research/results/gxw-fx-function-layout-20260930/manifest.json',
                    scope='One real FX3G library lineage, compiler 15.50; copied offline compile/check/save/reopen only.',
                    counts=counts, observations=rows, array_layout=layout,
                    findings=[
                        'Primitive member arrays extend each family stride by element count times primitive width; DWORD uses two words without alignment padding.',
                        'Outer and member array literal indices use their own declared bounds and row-major offsets, including negative origins and rank two.',
                        'Three full-byte predictions were saved before native runs and match compilation, fresh checks, save/reopen and independent cache address projections.',
                        'Three earlier positive discovery cases also match the extended model after observation; their original model refusals remain preserved in the previous checkpoint.',
                        'Nested member references from Modbus to AlarmManger types were refused at the changed declaration rows with diagnostic 0x500c5052; this does not establish general nested-structure support or prohibition.',
                    ],
                    limitations=[
                        'Research model only, no product compiler integration or general ST compilation.',
                        'Only BOOL/INT/WORD/DWORD primitive members, static literal indices, rank at most two, bounded global-document ordering.',
                        'No nested types, dynamic indices, explicit member locations or PLC execution validation.',
                        'Cross-library visibility and nested type constraints remain separate unresolved hypotheses.',
                        'Local archive contains third-party source with redistribution rights not established; existing raw evidence metadata is retained without new hashes.',
                    ],
                    archives=dict(public=public.relative_to(ROOT).as_posix(), local=str(local)))
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    authored = list(P.glob('*.py')) + list(P.glob('*.js'))
    authored += list((ROOT / 'research').glob('*.py')) + list((ROOT / 'src/gxw').rglob('*.py'))
    authored += [ROOT / 'LICENSE', ROOT / 'AGENTS.md', output / 'manifest.json']
    evidence = []
    for group in GROUPS:
        directory = P / group
        for path in directory.rglob('*'):
            if not path.is_file() or any(part in ('home', 'compiler', 'compiler_DZComp', '__pycache__') for part in path.relative_to(directory).parts):
                continue
            if path.suffix.lower() not in ('.exe', '.dll', '.pdb', '.pyc', '.zip'):
                evidence.append(path)
    evidence += [P / 'fx3g-library-source-roundtrip-v6/original-library/candidate.gxw',
                 P / 'fx3g-member-arrays-forward-plan.json',
                 P / 'fx3g-nested-structure-discovery-plan.json',
                 P / 'fx3g-member-arrays-forward-20260930-layout-summary.json']
    discovery = P / 'fx3g-structure-member-array-pointer-controls-20260930'
    evidence += list(discovery.glob('*/array-members-post-observation-fit.*'))
    for target, files in ((public, authored), (local, authored + evidence)):
        entries = []
        with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(set(files)):
                name = path.relative_to(ROOT).as_posix()
                archive.write(path, name)
                entries.append(dict(path=name, bytes=path.stat().st_size))
            archive.writestr('checkpoint-files.json', json.dumps(entries, indent=2))
        with zipfile.ZipFile(target) as archive:
            assert len(archive.infolist()) == len(entries) + 1
            assert sum(i.file_size for i in archive.infolist() if i.filename != 'checkpoint-files.json') == sum(e['bytes'] for e in entries)
        print(json.dumps(dict(path=str(target), files=len(entries), bytes=target.stat().st_size)), flush=True)
    print(json.dumps(counts), flush=True)


if __name__ == '__main__':
    main()
