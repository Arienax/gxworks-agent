"""Existing lexical DABCD codec and source CSV paths; no Native execution claim."""
import csv


def test_dabcd_existing_q03udv_descriptor_keeps_exact_literal():
    from gxw.token_encoder import encode_instruction
    from gxw.token_listing import decode_token_program
    from gxw.token_pou import parse_token_fragment
    from gxw.token_q import q_instruction_header
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY

    form = DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form('DABCD', cpu='Q03UDV')
    assert form.opcode == form.base_mnemonic == 'DABCD'
    assert not form.double and not form.pulse
    args = ['D100', 'D200']
    encoded = encode_instruction('DABCD', args, profile='q03udv', plc_model='Q03UDV', text_encoding='cp932')
    # Shipped descriptor 59:0a spells this exact two-operand instruction.
    assert encoded[1] == 0x59 and encoded[3] == 0x0a
    assert q_instruction_header(encoded[:encoded[0]], profile='q03udv') == ('DABCD', 2)
    listing = decode_token_program(parse_token_fragment(encoded, 0, len(encoded)),
                                   profile='q03udv', text_encoding='cp932')
    assert not listing.gaps
    assert [(r.mnemonic, list(r.args)) for r in listing.instructions] == [('DABCD', args)]
    assert listing.reconstruct() == encoded


def test_dabcd_source_csv_ordered_ir_roundtrip_keeps_both_operands(tmp_path):
    from gxworks2.csv_manager import GXWORKS2_HEADER, GXWORKS2_COMMENT_HEADER
    from gxworks2.csv_importer import inspect_gxworks2_csv
    from gxworks2.csv_source_export import write_gxworks2_csv_from_source
    from plc.ir import ir_to_ladder, validate_plc_ir

    program, comments = tmp_path / 'MAIN.csv', tmp_path / 'COMMENT.csv'
    with program.open('w', encoding='utf-16', newline='') as out:
        writer = csv.writer(out, delimiter='\t', lineterminator='\r\n')
        writer.writerows([['MAIN'], ['PLC信息:', 'Q03UDV'], GXWORKS2_HEADER,
            ['', '', 'LD', 'SM400', '', '', ''],
            ['', '', 'DABCD', 'D100', '', '', ''],
            ['', '', '', 'D200', '', '', ''],
            ['', '', 'END', '', '', '', '']])
    with comments.open('w', encoding='utf-16', newline='') as out:
        csv.writer(out, delimiter='\t', lineterminator='\r\n').writerows(
            [['COMMENT'], GXWORKS2_COMMENT_HEADER])
    expected = [('LD', ['SM400']), ('DABCD', ['D100', 'D200']), ('END', [])]
    source = inspect_gxworks2_csv(program, comments, plc_model='Q03UDV', program_name='MAIN')
    assert source['program_ir'] is not None, source['coverage']
    target, target_comments = tmp_path / 'saved.csv', tmp_path / 'saved_comments.csv'
    write_gxworks2_csv_from_source(source, target, target_comments, plc_model='Q03UDV')
    reread = inspect_gxworks2_csv(target, target_comments, plc_model='Q03UDV', program_name='MAIN')
    for snapshot in (source, reread):
        assert snapshot['program_ir'] is not None, snapshot['coverage']
        validate_plc_ir(snapshot['program_ir'])
        assert [(r['op'], r['args']) for rung in ir_to_ladder(snapshot['program_ir'])['rungs']
                for r in rung['ordered_instructions']] == expected
        assert [(r['op'], r['args']) for network in snapshot['raw_networks']
                for r in network['raw_instructions']] == expected
        assert snapshot['coverage']['runtime_verified'] is False
