"""CMP's Universal header keeps named serial restrictions on their own CPUs."""
import json,sqlite3
from pathlib import Path
import pytest
HERE=Path('D:/gxworks-agent-private/tmp/rag-gap-implementation-20261029/ql-cmp-native-scope-repair')

@pytest.mark.parametrize('op',['CMP','CMPP','DCMP','DCMPP'])
def test_cmp_remaining_twenty_U_models_have_own_unconditional_header_support(op):
    rows=[e for e in json.loads((HERE/'source-facts.json').read_text())['entries'] if e['opcode']==op]
    assert len(rows)==20
    for e in rows:
        assert e['target_model'].startswith('Q') and 'U' in e['target_model'] and 'UDV' not in e['target_model'] and 'UDPV' not in e['target_model']
        facts={f['id']:f for f in e['facts']}
        cpu=facts['authored:ql:cpu_applicability:constraints.cpu_availability']
        assert cpu['status']=='source_verified' and cpu['value']['available'] is True
        assert not cpu.get('conditions')
        typed=facts['authored:ql:native_arguments_s1_s2_d:parameters.typed_operands']
        assert typed['status']=='source_verified'
        assert typed['value']['native_form']==dict(operand_order=['S1','S2','D'],minimum_operands=3,maximum_operands=3)
        a,b,d=typed['value']['operands']
        assert a['type']==b['type']==('ANY32' if op.startswith('D') else 'ANY16')
        assert a['direction']==b['direction']=='input'
        assert d['type']=='BOOL' and d['direction']=='output' and d['occupied_bit_count']==3
        assert all(set(f.get('depends_on',[]))<=facts.keys() for f in e['facts'])
        assert all(p in ([211] if op.startswith('D') else [209,210]) for f in e['facts'] for s in f['sources'] if s['manual_id']=='sh080809engx' for p in s['pdf_pages'])

@pytest.mark.parametrize('opcode,page',[('CMP',209),('DCMP',211)])
def test_cmp_own_named_L_and_UDV_versions_and_old_Q_negative_are_preserved(opcode,page):
    from tools.fact_ql_behavior_review import Section,_prepare_xp_applicability,_cpu_conditions
    c=sqlite3.connect('file:D:/gxworks-agent-private/resources/knowledge/fx3u_knowledge.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row
    rows=[dict(r) for r in c.execute("select id,text,outline_path,instruction_opcode,pdf_page,pdf_page_end,chunk_type,revision,manual_number,source_applicability_json from chunks where manual_id='sh080809engx' and instruction_opcode=? order by pdf_page,id",(opcode,))];c.close()
    s=Section('sh080809engx','X','SH080809ENG','ladder',rows[0]['outline_path'],rows)
    s._flat=s.flat_rows()
    _prepare_xp_applicability({('sh080809engx',s.outline):s})
    for op in (opcode,opcode+'P'):
        a=s.symbol_applicability[op]
        assert a['Q00U']['state']=='supported' and a['Q00U']['conditions']==[]
        assert a['Q03UD']['state']=='supported' and a['Q03UD']['conditions']==[]
        assert a['Q02H']['state']=='unsupported' and a['Q02H']['conditions']==[]
        assert a['Q00J']['state']=='unsupported' and a['Q00J']['conditions']==[]
        assert a['L02']['state']=='conditional' and a['L02']['conditions']==[dict(kind='serial_number_minimum',minimum='16042',digits=5)]
        for model in ('Q03UDV','Q04UDPV'):
            assert a[model]['state']=='conditional' and a[model]['conditions']==[dict(kind='serial_number_minimum',minimum='16043',digits=5)]


def test_cmp_scope_repairs_only_the_original_eighty_not_full_family():
    rows=json.loads((HERE/'source-facts.json').read_text())['entries']
    selectors=json.loads((HERE/'owner-selectors.json').read_text())
    assert len(rows)==len(selectors)==80
    assert {(e['target_model'],e['opcode']) for e in rows}=={(e['target_model'],e['opcode']) for e in selectors}
    notes=json.loads((HERE/'source-notes.json').read_text())
    assert notes['own_instruction_sources'][0]['exclusive_word_only_present'] is False
    assert notes['scope']['not_republished_L_or_UDV_UDPV'] is True
