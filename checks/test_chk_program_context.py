"""Saved CHK source grammar is distinct from dynamic diagnostic execution."""
import copy
import pytest


def bound_program(rows, model='Q02H'):
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program, TokenInstruction
    from gxw.token_pou import parse_token_fragment
    from gxw.token_profiles import source_token_profile
    profile = source_token_profile(model)
    raw = b''.join(encode_instruction(op, args, profile=profile, plc_model=model,
                                     text_encoding='cp932') for op, args in rows)
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)),
                                   profile=profile, text_encoding='cp932')
    records = []
    for index, record in enumerate(listing.records):
        row = dict(record_index=index, source_offset=record.tokens[0].offset,
                   raw_hex=b''.join(t.raw for t in record.tokens).hex())
        if isinstance(record, TokenInstruction):
            row.update(kind='instruction', op=record.mnemonic, args=list(record.args))
        else:
            row.update(kind='label', op='', args=[], text=record.text)
        records.append(row)
    return dict(device_comments={}, rungs=[dict(rung_id=1, ordered_instructions=records)])


def context(program):
    from plc.ir import build_plc_ir
    ir = build_plc_ir(program, plc_model='Q02H', _canonicalize_devices=False)
    return ir, [f for f in ir['analysis']['findings'] if f['code'] == 'CHK_PROGRAM_CONTEXT']


def test_chk_saved_contacts_preserve_order_duplicates_and_ignored_numbering():
    from plc.ir import validate_plc_ir
    source = bound_program([('LD', ['T0']), ('CHKST', []), ('LD', ['X2']),
        ('AND', ['X2']), ('LDI', ['XA']), ('ANI', ['XC']), ('CHK', []), ('END', [])])
    ir, findings = context(source)
    validate_plc_ir(ir)
    assert len(findings) == 2
    for finding in findings:
        assert finding['complete_saved_program_source'] is True
        assert finding['severity'] == 'unverified'
        assert finding['context_checks']['local_source_pairing'] == 'passed'
        assert finding['context_checks']['check_condition_instructions_and_X_devices'] == 'passed'
        assert [c['operand'] for c in finding['condition_contacts']] == ['X2', 'X2', 'X0A', 'X0C']
        assert finding['ignored_condition_contact_numbers'] == [3, 4]
        assert finding['preceding_CHK_contacts_are_execution_gate'] is False
        assert finding['core_execution_established'] is False
        assert finding['current_SM710_verified'] is False
        record = source['rungs'][0]['ordered_instructions'][finding['record_index']]
        assert finding['source_offset'] == record['source_offset']
        assert 'raw_hex' not in finding


@pytest.mark.parametrize('rows,reason', [
    ([('CHKST', []), ('LD', ['M0']), ('CHK', []), ('END', [])], 'invalid_CHK_check_condition_instruction_or_non_X_device'),
    ([('CHKST', []), *[('LD', ['X0']) for _ in range(151)], ('CHK', []), ('END', [])], 'more_than_150_CHK_condition_contacts'),
    ([('CHKST', []), ('LD', ['X0']), ('CHK', []), ('CHKST', []), ('LD', ['X1']), ('CHK', []), ('END', [])], 'more_than_one_CHK_location_in_one_program'),
    ([('CHKST', []), ('END', [])], 'unpaired_CHKST'),
])
def test_chk_definite_saved_source_errors_reject_with_public_record_diagnostics(rows, reason):
    from plc.core import PLCCore
    from application.program_projection import issue_cards
    ir, findings = context(bound_program(rows))
    assert any(reason in f['requires'] and f['severity'] == 'error' for f in findings)
    result = PLCCore().validate_project(ir)
    assert result['valid'] is False
    actual = [f for f in result['findings'] if f['code'] == 'CHK_PROGRAM_CONTEXT']
    assert actual == findings
    cards = issue_cards(ir, actual)
    assert len(cards) == len(actual)
    assert all('Program source record: ' + str(f['record_index']) in card['evidence']
               for card, f in zip(cards, actual))


def test_chk_partial_opaque_and_control_transfer_do_not_certify_context():
    base = bound_program([('CHKST', []), ('LD', ['X0']), ('CHK', []), ('END', [])])
    fragment = copy.deepcopy(base)
    for row in fragment['rungs'][0]['ordered_instructions']:
        for field in ('kind', 'record_index', 'source_offset', 'raw_hex'):
            row.pop(field, None)
    opaque = copy.deepcopy(base)
    opaque['rungs'][0]['ordered_instructions'][1].update(kind='opaque', op='', args=[], raw_hex='03ff03')
    jump = bound_program([('LD', ['X0']), ('CJ', ['P1']), ('CHKST', []),
        ('LD', ['X0']), ('CHK', []), ('P1', []), ('END', [])])
    for program in [fragment, opaque, jump]:
        _, findings = context(program)
        assert findings
        assert all(f['severity'] == 'unverified' for f in findings)
        assert all(f['context_checks']['local_source_pairing'] == 'unverified' for f in findings)
        assert all(f['control_flow_context_verified'] is False for f in findings)


def test_chk_pattern_preserves_dummy_coil_order_and_separates_end_gate():
    _, findings = context(bound_program([('LD', ['SM400']), ('CHKCIR', []),
        ('LD', ['X0Z0']), ('OUT', ['F7']), ('LDI', ['Y0Z0']), ('OUT', ['F7']),
        ('LD', ['SM400']), ('CHKEND', []), ('END', [])]))
    for finding in findings:
        assert [c['operand'] for c in finding['pattern_coils']] == ['F7', 'F7']
        assert [c['coil_number'] for c in finding['pattern_coils']] == [1, 2]
        assert finding['CHKEND_gate_record_indices'] == [6]
        assert finding['context_checks']['pattern_instructions_and_devices'] == 'passed'
        assert finding['dummy_F_actual_state_changes'] is False
        assert finding['severity'] == 'unverified'



def test_chk_reader_without_labels_keeps_real_saved_record_identity(monkeypatch):
    from types import SimpleNamespace
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxw.offline_project import read_ordered_program
    source = bound_program([('LD', ['T0']), ('CHKST', []), ('LD', ['X0']), ('CHK', []), ('END', [])])
    records = source['rungs'][0]['ordered_instructions']
    raw = b''.join(bytes.fromhex(row['raw_hex']) for row in records)
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile='q02', text_encoding='cp932')
    monkeypatch.setattr('gxw.token_listing.decode_token_listing', lambda *args, **kwargs: listing)
    project = SimpleNamespace(context=dict(cpu='Q02H', text_encoding='cp932'), streams={'MAIN': b''},
        select=lambda name: dict(language='ladder', source_name='MAIN', name='MAIN'),
        comments=lambda: ({}, [], []))
    result = read_ordered_program(project)
    ir = result['program_ir']
    assert ir['source']['format'] == 'instruction_sequence_v2'
    preserved = [row for net in ir['networks'] for row in net['ladder']['ordered_instructions']]
    assert [(r['record_index'], r['source_offset'], r['raw_hex']) for r in preserved] == [
        (r['record_index'], r['source_offset'], r['raw_hex']) for r in records]
    findings = [f for f in ir['analysis']['findings'] if f['code'] == 'CHK_PROGRAM_CONTEXT']
    assert len(findings) == 2 and all(f['complete_saved_program_source'] for f in findings)


@pytest.mark.parametrize('pattern,reason,check', [
    ([row for _ in range(10) for row in [('LD', ['X0Z0']), ('OUT', ['F7'])]],
     'more_than_9_CHK_pattern_coils', 'pattern_coil_limit'),
    ([('LD', ['X0Z0']), *[('AND', ['X0Z0']) for _ in range(256)], ('OUT', ['F7'])],
     'more_than_256_CHK_pattern_steps', 'pattern_step_limit'),
])
def test_chk_saved_pattern_limits_use_decoded_steps_and_coil_ordinals(pattern, reason, check):
    _, findings = context(bound_program([('CHKCIR', []), *pattern, ('CHKEND', []), ('END', [])]))
    assert len(findings) == 2
    assert all(f['severity'] == 'error' and reason in f['requires'] for f in findings)
    assert all(f['context_checks'][check] == 'failed' for f in findings)
