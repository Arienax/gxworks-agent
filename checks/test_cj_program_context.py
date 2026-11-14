"""CJ label binding proves local saved syntax, never dynamic reachability."""
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
    return ir, [f for f in ir['analysis']['findings'] if f['code'] == 'CJ_PROGRAM_CONTEXT']


@pytest.mark.parametrize('rows,target_index', [
    ([('LD', ['X0']), ('CJ', ['P1']), ('OUT', ['Y0']), ('P1', []), ('END', [])], 3),
    ([('P1', []), ('LD', ['X0']), ('CJ', ['P1']), ('END', [])], 0),
])
def test_cj_complete_saved_forward_backward_targets_only_prove_local_definition(rows, target_index):
    from plc.ir import validate_plc_ir
    source = bound_program(rows)
    ir, findings = context(source)
    validate_plc_ir(ir)
    assert len(findings) == 1
    finding = findings[0]
    assert finding['complete_saved_program_source'] is True
    assert finding['severity'] == 'unverified'
    assert finding['context_checks']['pointer_defined_in_same_program'] == 'passed'
    assert finding['context_checks']['pointer_definition_before_END'] == 'passed'
    assert finding['target_label_record_indices'] == [target_index]
    row = source['rungs'][0]['ordered_instructions'][finding['record_index']]
    assert finding['source_offset'] == row['source_offset']
    assert finding['context_checks']['pointer_numeric_domain'] == 'passed'
    assert finding['pointer_numeric_domain_verified'] is True
    assert finding['pointer_numeric_domain_source']['kind'] == 'cpu_program_pointer_domain'
    assert finding['pointer_numeric_domain_source']['domain']['sources']
    assert finding['pointer_allocation_verified'] is False
    assert 'pointer_numeric_domain_not_established' not in finding['requires']
    for field in ['control_flow_context_verified', 'dynamic_reachability_verified',
                  'current_scan_completion_verified', 'core_execution_established',
                  'special_pointer_values_verified',
                  'FEND_boundary_verified', 'CPU_error_row_applicability_established']:
        assert finding[field] is False
    assert 'raw_hex' not in finding


def test_cj_missing_saved_target_rejects_and_keeps_per_record_public_diagnostics():
    from plc.core import PLCCore
    from application.program_projection import issue_cards
    from agent_runtime.tool_context import build_tool_context
    from agent_runtime.program_tools import _get_diagnostics
    from application.projects import public
    ir, findings = context(bound_program([('LD', ['X0']), ('CJ', ['P1']),
                                         ('LD', ['X1']), ('CJ', ['P2']), ('END', [])]))
    assert len(findings) == 2 and all(f['severity'] == 'error' for f in findings)
    assert all('CJ_target_not_defined_before_END_in_this_program' in f['requires'] for f in findings)
    tool_context = build_tool_context(dict(plc_model='Q02H', target_mode='ladder'), program_ir=ir)
    result = public(_get_diagnostics(tool_context, {}))
    assert result['valid'] is False
    actual = [f for f in result['findings'] if f['code'] == 'CJ_PROGRAM_CONTEXT']
    assert actual == findings
    assert [f['record_index'] for f in actual] == [1, 3]
    assert all(f['CPU_error_row_applicability_established'] is False for f in actual)
    cards = issue_cards(ir, actual)
    assert len({card['id'] for card in cards}) == 2
    assert all('Program source record: ' + str(f['record_index']) in card['evidence']
               for card, f in zip(cards, actual))
    assert all('Program source offset: ' + str(f['source_offset']) in card['evidence']
               for card, f in zip(cards, actual))
    stale = copy.deepcopy(ir)
    stale['analysis']['findings'].append(copy.deepcopy(findings[0]))
    rejected = PLCCore().validate_project(stale)
    assert rejected['valid'] is False and 'findings' not in rejected


def test_cj_partial_opaque_tampered_or_after_END_labels_do_not_establish_saved_context():
    base = bound_program([('LD', ['X0']), ('CJ', ['P1']), ('P1', []), ('END', [])])
    fragment = copy.deepcopy(base)
    for row in fragment['rungs'][0]['ordered_instructions']:
        for field in ('record_index', 'source_offset', 'raw_hex'):
            row.pop(field, None)
    opaque = copy.deepcopy(base)
    opaque['rungs'][0]['ordered_instructions'][0].update(kind='opaque', op='', args=[], raw_hex='03ff03')
    tampered = copy.deepcopy(base)
    tampered['rungs'][0]['ordered_instructions'][2]['text'] = 'P2'
    after_end = bound_program([('LD', ['X0']), ('CJ', ['P1']), ('END', []), ('P1', [])])
    for program in [fragment, opaque, tampered, after_end]:
        _, findings = context(program)
        assert len(findings) == 1
        finding = findings[0]
        assert finding['severity'] == 'unverified'
        assert finding['complete_saved_program_source'] is False
        assert finding['context_checks']['pointer_defined_in_same_program'] == 'unverified'
        assert finding['dynamic_reachability_verified'] is False


def test_cj_duplicate_target_is_ambiguous_without_own_duplicate_error_rule():
    from plc.ir import validate_plc_ir
    ir, findings = context(bound_program([('P1', []), ('LD', ['X0']), ('CJ', ['P1']),
                                         ('P1', []), ('END', [])]))
    validate_plc_ir(ir)
    assert len(findings) == 1
    assert findings[0]['severity'] == 'unverified'
    assert findings[0]['target_label_record_indices'] == [0, 3]
    assert 'ambiguous_pointer_definition' in findings[0]['requires']
    assert findings[0]['context_checks']['pointer_defined_in_same_program'] == 'unverified'


def test_cj_rule_does_not_borrow_FX_P63_or_unreviewed_cpu_context():
    from plc.ordered_source import _cj_context_rule
    assert _cj_context_rule('CJ', ['P63'], 'FX3U') is None
    assert _cj_context_rule('CJ', ['P1'], 'UNKNOWN_CPU') is None


@pytest.mark.parametrize('cpu', ['Q02H', 'Q03UDV'])
def test_cj_direct_pointer_codec_and_source_csv_roundtrip(tmp_path, cpu):
    import csv
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxw.token_profiles import source_token_profile
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from gxworks2.csv_manager import GXWORKS2_HEADER, GXWORKS2_COMMENT_HEADER
    from plc.ir import ir_to_ladder
    profile = source_token_profile(cpu)
    encoded = encode_instruction('CJ', ['P1'], profile=profile, plc_model=cpu, text_encoding='cp932')
    decoded = decode_token_program(parse_token_fragment(encoded, 0, len(encoded)),
                                   profile=profile, text_encoding='cp932')
    assert not decoded.gaps
    assert [(r.mnemonic, list(r.args)) for r in decoded.instructions] == [('CJ', ['P1'])]
    assert decoded.reconstruct() == encoded
    rows = [('LD', ['X0']), ('CJ', ['P1']), ('END', [])]
    program = tmp_path / 'CJ_SOURCE.csv'
    comments = tmp_path / 'CJ_COMMENT.csv'
    with program.open('w', encoding='utf-16', newline='') as out:
        writer = csv.writer(out, delimiter='\t', lineterminator='\r\n')
        writer.writerows([['MAIN'], ['PLC信息:', cpu], GXWORKS2_HEADER])
        writer.writerows([['', '', op, args[0] if args else '', '', '', ''] for op, args in rows])
    with comments.open('w', encoding='utf-16', newline='') as out:
        csv.writer(out, delimiter='\t', lineterminator='\r\n').writerows([['COMMENT'], GXWORKS2_COMMENT_HEADER])
    source = inspect_gxworks2_csv(program, comments, plc_model=cpu, program_name='MAIN')
    assert source['program_ir'] is not None
    saved, saved_comments = tmp_path / 'saved.csv', tmp_path / 'saved_comments.csv'
    write_gxworks2_csv_from_source(source, saved, saved_comments, plc_model=cpu)
    reread = inspect_gxworks2_csv(saved, saved_comments, plc_model=cpu, program_name='MAIN')
    assert reread['program_ir'] is not None
    actual = [(row['op'], row['args']) for rung in ir_to_ladder(reread['program_ir'])['rungs']
              for row in rung['ordered_instructions']]
    assert actual == rows
    findings = [f for f in reread['program_ir']['analysis']['findings'] if f['code'] == 'CJ_PROGRAM_CONTEXT']
    assert len(findings) == 1
    # CSV rows do not invent a native saved-record binding or defined target.
    assert findings[0]['complete_saved_program_source'] is False
    assert findings[0]['context_checks']['pointer_defined_in_same_program'] == 'unverified'
    assert reread['coverage']['runtime_verified'] is False



def test_cj_absent_cpu_pointer_domain_keeps_numeric_check_unverified(monkeypatch):
    monkeypatch.setattr('plc.device_profiles.program_pointer_domain', lambda model: None)
    _, findings = context(bound_program([('LD', ['X0']), ('CJ', ['P1']), ('P1', []), ('END', [])]))
    assert len(findings) == 1
    finding = findings[0]
    assert finding['context_checks']['pointer_defined_in_same_program'] == 'passed'
    assert finding['context_checks']['pointer_numeric_domain'] == 'unverified'
    assert finding['pointer_numeric_domain_verified'] is False
    assert finding['pointer_numeric_domain_source'] is None
    assert finding['pointer_allocation_verified'] is False
    assert 'pointer_numeric_domain_not_established' in finding['requires']
