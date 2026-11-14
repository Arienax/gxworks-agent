"""CHK source-record preservation; offline results are not Native execution."""
import csv
import pytest

# X524/526/527: negative contacts remain numbered source records even though
# they do not participate in CHK diagnosis. X530 permits X/Y pattern contacts
# and dummy F coils whose device numbers do not define the coil ordinal.
CHK_RECORDS = [
    ('LD', ['T0']), ('CHKST', []),
    ('LD', ['X2']), ('AND', ['X8']), ('LDI', ['XA']), ('ANI', ['XC']), ('CHK', []),
    ('LD', ['SM400']), ('CHKCIR', []),
    ('LD', ['X10Z0']), ('AND', ['X12Z0']), ('OUT', ['F7']),
    ('LDI', ['Y40Z0']), ('OUT', ['F3']),
    ('LD', ['SM400']), ('CHKEND', []), ('END', []),
]


def _source_csv(tmp_path, cpu):
    from gxworks2.csv_manager import GXWORKS2_HEADER,GXWORKS2_COMMENT_HEADER
    program=tmp_path/'CHK_SOURCE.csv';comments=tmp_path/'CHK_COMMENT.csv'
    with program.open('w',encoding='utf-16',newline='') as out:
        writer=csv.writer(out,delimiter='\t',lineterminator='\r\n')
        writer.writerows([['MAIN'],['PLC信息:',cpu],GXWORKS2_HEADER])
        # No fabricated native step counts: preserve unnumbered source rows.
        writer.writerows([['','',op,args[0] if args else '','','',''] for op,args in CHK_RECORDS])
    with comments.open('w',encoding='utf-16',newline='') as out:
        csv.writer(out,delimiter='\t',lineterminator='\r\n').writerows([['COMMENT'],GXWORKS2_COMMENT_HEADER])
    return program,comments


def _ir_sequence(ir):
    from plc.ir import ir_to_ladder
    return [(r['op'],r['args']) for rung in ir_to_ladder(ir)['rungs'] for r in rung['ordered_instructions']]


@pytest.mark.parametrize('cpu',['Q02','Q02H'])
def test_chk_ordered_csv_ir_roundtrip_preserves_diagnostic_records(tmp_path,cpu):
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    program,comments=_source_csv(tmp_path,cpu)
    source=inspect_gxworks2_csv(program,comments,plc_model=cpu,program_name='MAIN')
    assert source['program_ir'] is not None
    assert _ir_sequence(source['program_ir'])==CHK_RECORDS
    target=tmp_path/'saved.csv';target_comments=tmp_path/'saved_comments.csv'
    write_gxworks2_csv_from_source(source,target,target_comments,plc_model=cpu)
    reread=inspect_gxworks2_csv(target,target_comments,plc_model=cpu,program_name='MAIN')
    assert _ir_sequence(reread['program_ir'])==CHK_RECORDS
    assert [(r['op'],r['args']) for n in reread['raw_networks'] for r in n['raw_instructions']]==CHK_RECORDS
    assert reread['coverage']['runtime_verified'] is False
    assert [args[0] for op,args in CHK_RECORDS if op=='OUT']==['F7','F3']


@pytest.mark.parametrize('cpu',['Q02','Q02H'])
def test_chk_gxw_new_project_requires_own_cpu_template(cpu):
    from gxw.project_templates import blank_ladder_baseline,ladder_generation_support
    from gxw.models import GXWFormatError
    assert ladder_generation_support(cpu)['status']=='unsupported'
    with pytest.raises(GXWFormatError,match='No captured blank Ladder project'):
        blank_ladder_baseline(cpu)


@pytest.mark.parametrize('cpu',['Q02','Q02H'])
def test_existing_q02_fourzero_witness_decodes_as_complete_ordered_fragment(cpu):
    import json
    from pathlib import Path
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    evidence=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-chk-native-contract-repair/existing-codec-evidence.json')
    observed=json.loads(evidence.read_text())['observed_results'][cpu]
    forms=['CHKST','CHK','CHKCIR','CHKEND']
    raw=b''.join(bytes.fromhex(observed[op]) for op in forms)
    listing=decode_token_program(parse_token_fragment(raw,0,len(raw)),profile='q02',text_encoding='cp932')
    assert not listing.gaps
    assert [(r.mnemonic,r.args) for r in listing.instructions]==[(op,()) for op in forms]
    assert listing.reconstruct()==raw
