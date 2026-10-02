"""Freeze a substantial source-derived function/global-layout research stage.

Original predictions, native refusals and discovered preparation mistakes stay
distinct. Public archive contains authored tools and the bounded manifest only;
third-party project/source observations remain in a local evidence archive.
No native reruns, new hash calculation or formal prose report.
"""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import json,subprocess,zipfile

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
GROUPS=[
 'fx3g-st-library-call-abi-disc-20260930',
 'fx3g-user-functions-forward-v2-20260930',
 'fx3g-user-functions-boolean-forward-20260930',
 'fx3g-user-functions-declaration-order-20260930',
 'fx3g-structure-globals-forward-20260930',
 'fx3g-structure-bounds-forward-20260930',
 'fx3g-structure-instances-forward-20260930',
 'fx3g-global-documents-forward-20260930',
 'fx3g-global-mixed-order-forward-20260930',
 'fx3g-structure-member-arrays-discovery-20260930',
 'fx3g-structure-member-array-declaration-20260930',
 'fx3g-structure-member-array-default-controls-20260930',
 'fx3g-structure-member-array-pointer-controls-20260930',
 'fx3g-function-pointer-boundary-20260930',
]


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    output=ROOT/'research/results/gxw-fx-function-layout-20260930'
    public=ROOT/'research/evidence/gxw-fx-function-layout-20260930.zip'
    local=P/'gxw-fx-function-layout-local-20260930.zip'
    for path in (output,public,local):
        if path.exists():raise FileExistsError(path)
    rows=[]
    for group in GROUPS:
        for row in read(P/group/'comparison.json'):
            check=row['check'];reopen=row.get('reopen');match=row.get('prospective_equal')
            if check:
                assert not check['rejected'] and reopen and not reopen['check']['rejected'] and reopen['bytes_identical']
                status='native-check-and-reopen-accepted'
            else:status='native-main-or-frontend-refusal'
            rows.append(dict(group=group,case=row['case'],status=status,generated_bytes=row['generated_bytes'],
                 original_prospective_equal=match,original_source_model_refusal=row.get('expected_error'),
                 diagnostics=[dict(code=hex(d['code']),arguments=d['arguments']) for d in row['diagnostics']],
                 independent_address_projection_match=row.get('independent_address_projection_match'),
                 reopen_identical=reopen.get('bytes_identical') if reopen else None,
                 evidence_directory=str((P/group/row['case']).relative_to(ROOT))))
    counts=dict(Counter(r['status'] for r in rows))
    counts.update(native_observations=len(rows),prospective_full_byte_matches=sum(r['original_prospective_equal'] is True for r in rows),
                  prospective_full_byte_failures=sum(r['original_prospective_equal'] is False for r in rows))
    layout=read(P/'fx3g-source-structure-layout-summary-20260930.json')
    manifest=dict(created_utc=datetime.now(timezone.utc).isoformat(),
        base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        scope='One real FX3G UtilsV5/AlarmManger/TimeControl/Modbus project lineage; native compiler 15.50, offline copied projects only.',
        counts=counts,observations=rows,
        findings=[
          'Outlined functions share one frame/routine per definition. Inputs precede implicit return and locals; declaration record IDs determine each class order.',
          'Nested argument calls register before their enclosing calls. Outlined routine discovery uses first-registration queue order; repeated calls reuse a frame.',
          'Measured two-call AND captures the left result. OR captures both results and combines right capture then left; three-bit/four-bit capacity controls keep refusals separate.',
          'Source-only global layout splits structures into word/bit blocks, preserves element strides, supports static literal negative origins, rank two and separate same-type instances.',
          'Globals allocate in source library catalog order followed by project global rows, with record IDs inside each document. Primitive and structure rows can interleave. Earlier type-stage hypotheses failed and remain preserved.',
          'Native type POU names are not unique across structure instances; array descriptors resolve targets by table offsets. Forty independent cache member-address projections agree with the source model.',
          'Adjacent outlined CALL controls accept P2047 and refuse P2048; flat P2100 refuses independently of member arrays. This does not define general branch-label ranges.',
          'WORD/BOOL member arrays and their literal references pass native check/reopen at P1800 after only the project P range changes. Current source model still refuses array members, so these are discovery evidence, not complete source predictions.',
        ],
        structure_layout_observations=layout['observations'],structure_layout_prospective_matches=layout['prospective_full_byte_matches'],
        independent_cache_member_references=sum(r['independent_cache_member_reference_matches'] for r in layout['cases']),
        preserved_limitations=[
          'Current source model covers bounded primitive functions and flat primitive structure members; no general user-function compiler or product integration.',
          'No nested structures, member arrays in the source model, dynamic member indices or explicit member address layout.',
          'Multiple global documents are bounded to one storage document per library and one project global document; arbitrary library catalog reorder remains unverified.',
          'No PLC/device/simulator execution or runtime-semantics certification.',
          'Original OR mismatch renderer failed after native acceptance; its process return dictionary was lost. Recovery and original failed bytes stay preserved.',
          'One preparation attempt used a source patcher outside its .prj scope. Seven array controls used invalid P2100; original diagnostics and separate correcting controls remain intact.',
          'Local archive contains third-party engineering source; redistribution rights are not established. Existing event fingerprint metadata is preserved without recomputation.',
        ],archives=dict(public=str(public.relative_to(ROOT)),local=str(local)))
    output.mkdir();(output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    authored=list(P.glob('*.py'))+list(P.glob('*.js'))+list((ROOT/'research').glob('*.py'))
    authored+=list((ROOT/'src/gxw').rglob('*.py'))+[ROOT/'LICENSE',ROOT/'AGENTS.md',output/'manifest.json']
    local_files=[]
    for group in GROUPS+['fx3g-user-functions-forward-20260930']:
        directory=P/group
        for path in directory.rglob('*'):
            if not path.is_file() or any(part in ('home','compiler','compiler_DZComp','__pycache__') for part in path.relative_to(directory).parts):continue
            if path.suffix.lower() not in ('.exe','.dll','.pdb','.pyc','.zip'):local_files.append(path)
    local_files+=[P/'fx3g-library-source-roundtrip-v6/original-library/candidate.gxw',P/'cross-family-source-holdouts/case-00/input.gxw']
    local_files+=[f for f in P.iterdir() if f.is_file() and f.name.startswith('fx3g-') and f.suffix in ('.json','.txt')]
    for target,files in ((public,authored),(local,authored+local_files)):
        entries=[]
        with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for path in sorted(set(files)):
                name=path.relative_to(ROOT).as_posix();archive.write(path,name);entries.append(dict(path=name,bytes=path.stat().st_size))
            archive.writestr('checkpoint-files.json',json.dumps(entries,indent=2))
        with zipfile.ZipFile(target) as archive:
            assert len(archive.infolist())==len(entries)+1
            assert sum(i.file_size for i in archive.infolist() if i.filename!='checkpoint-files.json')==sum(e['bytes'] for e in entries)
        print(json.dumps(dict(path=str(target),files=len(entries),bytes=target.stat().st_size)),flush=True)
    print(json.dumps(counts),flush=True)


if __name__=='__main__':main()
