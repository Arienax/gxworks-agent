"""Known native CPU exclusions must reach public APP and ordered validation."""
import pytest

CASES = [(cpu, op, ['D0', 'D2', 'M0'], 'sh080809engx', 'X', {209, 211})
         for cpu in ('Q00J', 'Q02H') for op in ('CMP', 'DCMP', 'CMPP', 'DCMPP')]
CASES += [(cpu, 'CML', ['D0', 'D10'], 'fx3_programming_r', 'R', {75, 76})
          for cpu in ('FX1N', 'FX1NC', 'FX1S')]


@pytest.mark.parametrize('cpu,opcode,args,manual,revision,pages', CASES,
                         ids=[cpu + '-' + op for cpu, op, *_ in CASES])
def test_known_native_cpu_exclusion_reaches_constraint_APP_and_ordered_public_errors(
        cpu, opcode, args, manual, revision, pages):
    from plc.instruction_constraints import check_instruction_constraints
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    values = (dict(S1=args[0], S2=args[1], D=args[2]) if opcode != 'CML'
              else dict(S=args[0], D=args[1]))
    report = check_instruction_constraints(opcode, values, plc_model=cpu, language='ladder',
                                          context={'project_mode': 'native_ladder_instruction'})
    assert report['status'] == 'failed', report
    errors = [f for f in report['findings'] if f.get('severity') == 'error'
              and f.get('dimension') == 'constraints.cpu_availability']
    assert errors, report
    own = [f for f in errors if any(s.get('manual_id') == manual and s.get('revision') == revision
                                    and pages.intersection(s.get('pdf_pages', []))
                                    for s in f.get('sources', []))]
    assert own, report
    reasons = {f['reason'] for f in own}
    app = dict(rung_id=1, branches=[dict(branch_id=1, y_offset_level=0,
        inputs=[dict(type='NO', address='X0')],
        outputs=[dict(type='APP_INSTR', opcode=opcode, operands=args)])])
    ordered = dict(rung_id=1, ordered_instructions=[dict(op='LD', args=['X0']),
        dict(op=opcode, args=args), dict(op='END', args=[])])
    for path, rung in [('APP', app), ('ordered', ordered)]:
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu,
                          _canonicalize_devices=False)
        result = public(PLCCore().validate_project(ir))
        assert result.get('valid') is False, (path, result)
        assert result.get('status') != 'unverified', (path, result)
        assert result.get('counts', {}).get('error', 0) > 0, (path, result)
        assert any(reason in str(result.get('error', '')) for reason in reasons), (path, result)



def _comparison_rungs(opcode, args):
    return [('APP', dict(rung_id=1, branches=[dict(branch_id=1, y_offset_level=0,
        inputs=[dict(type='NO', address='X0')],
        outputs=[dict(type='APP_INSTR', opcode=opcode, operands=args)])])),
        ('ordered', dict(rung_id=1, ordered_instructions=[dict(op='LD', args=['X0']),
            dict(op=opcode, args=args), dict(op='END', args=[])]))]


@pytest.mark.parametrize('cpu,opcode', [(cpu, op) for cpu in ('Q00U', 'Q03UD', 'Q100UDEH')
                                      for op in ('CMP', 'DCMP', 'CMPP', 'DCMPP')])
def test_universal_cmp_cpu_availability_and_APP_ordered_acceptance_without_serial_context(cpu, opcode):
    from plc.instruction_constraints import check_instruction_constraints
    from plc.ir import build_plc_ir, validate_plc_ir
    report = check_instruction_constraints(opcode, dict(S1='D0', S2='D10', D='M0'),
        plc_model=cpu, language='ladder', context={'project_mode': 'native_ladder_instruction'})
    cpu_checks = [c for c in report['checks'] if c.get('dimension') == 'constraints.cpu_availability'
                  and c.get('status') != 'not_applicable']
    assert cpu_checks and all(c['status'] == 'passed' for c in cpu_checks), report
    assert any(any(source.get('manual_id') == 'sh080809engx' and source.get('revision') == 'X'
                   and {209, 211}.intersection(source.get('pdf_pages', []))
                   for source in check.get('sources', [])) for check in cpu_checks), report
    # CPU eligibility is independent of other unverified execution constraints.
    for _, rung in _comparison_rungs(opcode, ['D0', 'D10', 'M0']):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu,
                          _canonicalize_devices=False)
        validate_plc_ir(ir)


def test_universal_cmp_result_occupies_three_bits_at_sourced_M_tail():
    from plc.device_profiles import DEVICE_LIMITS
    from plc.ir import build_plc_ir, validate_plc_ir
    cpu = 'Q03UD'
    maximum = DEVICE_LIMITS[cpu]['M']
    for _, rung in _comparison_rungs('CMP', ['D0', 'D10', f'M{maximum - 2}']):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu,
                          _canonicalize_devices=False)
        validate_plc_ir(ir)
        assert {f'M{number}' for number in range(maximum - 2, maximum + 1)} <= set(ir['devices'])
    for _, rung in _comparison_rungs('CMP', ['D0', 'D10', f'M{maximum - 1}']):
        with pytest.raises(ValueError, match='Operand memory extent exceeds the verified device allocation'):
            ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu,
                              _canonicalize_devices=False)
            validate_plc_ir(ir)



@pytest.mark.parametrize('cpu,opcode,fact_id,manual,revision,pages', [
    ('FX0', 'DCMLP', 'authored:applied:cml_native_unavailable@jy992d48301j',
     'jy992d48301j', 'J', {145}),
    ('FX1NC', 'CMLP', 'authored:applied:cml_native_unavailable@fx3_programming_r',
     'fx3_programming_r', 'R', {76, 256, 257}),
])
def test_new_own_CML_variant_exclusions_reach_public_APP_and_ordered(
        cpu, opcode, fact_id, manual, revision, pages):
    from plc.instruction_constraints import check_instruction_constraints
    from plc.ir import build_plc_ir
    from plc.core import PLCCore
    from application.projects import public
    args = ['D0', 'D10']
    report = check_instruction_constraints(opcode, dict(S=args[0], D=args[1]), plc_model=cpu,
        language='ladder', context={'project_mode': 'native_ladder_instruction'})
    assert report['status'] == 'failed', report
    own_errors = [f for f in report['findings'] if f.get('fact_id') == fact_id
                  and f.get('dimension') == 'constraints.cpu_availability'
                  and f.get('severity') == 'error' and f.get('status') == 'failed']
    assert own_errors, report
    assert any(any(source.get('manual_id') == manual and source.get('revision') == revision
                   and pages.intersection(source.get('pdf_pages', []))
                   for source in finding.get('sources', [])) for finding in own_errors), report
    for path, rung in _comparison_rungs(opcode, args):
        ir = build_plc_ir(dict(device_comments={}, rungs=[rung]), plc_model=cpu,
                          _canonicalize_devices=False)
        result = public(PLCCore().validate_project(ir))
        assert result.get('valid') is False, (path, result)
        assert result.get('status') != 'unverified', (path, result)
        assert result.get('counts', {}).get('error', 0) > 0, (path, result)
        assert any(finding['reason'] in str(result.get('error', '')) for finding in own_errors), (path, result)
