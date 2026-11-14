"""A reviewed CPU exclusion stays an error when stored source is preserved."""
import pytest


@pytest.mark.parametrize('cpu', ['FX0', 'FX0S', 'FX0N'])
def test_ordered_cjp_known_cpu_exclusion_is_not_downgraded_to_unknown(cpu):
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    program = dict(device_comments={}, rungs=[dict(rung_id=1,
        ordered_instructions=[dict(op='LD', args=['X0']), dict(op='CJP', args=['P63'])])])
    ir = build_plc_ir(program, plc_model=cpu)
    result = PLCCore().validate_project(ir)
    assert result['valid'] is False and result['counts']['error'] >= 1
    finding, = [f for f in result['findings'] if f['code'] == 'SOURCE_INSTRUCTION_UNAVAILABLE']
    assert finding['opcode'] == 'CJP' and finding['rung_ids'] == [1]
    assert all(f['dimension'] == 'constraints.cpu_availability' and f['status'] == 'failed'
               and f['sources'] for f in finding['constraint_findings'])
    assert not any(f['code'] == 'SOURCE_RECORD_SEMANTICS_UNVERIFIED' and f.get('kind') == 'instruction'
                   for f in result['findings'])


def test_unresolved_instruction_without_cpu_exclusion_stays_unknown():
    from plc.ordered_source import source_coverage_findings
    networks = [dict(id='N0001', rung_id=1, ladder=dict(ordered_instructions=[
        dict(op='NO_SUCH_STORED_INSTRUCTION', args=[])]))]
    findings = source_coverage_findings(networks, plc_model='FX3U')
    assert not any(f['severity'] == 'error' for f in findings)
    assert any(f['code'] == 'SOURCE_RECORD_SEMANTICS_UNVERIFIED' for f in findings)
