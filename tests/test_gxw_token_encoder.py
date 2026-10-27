"""Pure Ladder encoding against frozen native bytes and independent CSV facts."""
from dataclasses import replace
import json
from pathlib import Path
import re
import shlex
import struct
from zipfile import ZipFile

import pytest
from hypothesis import example, given, settings, strategies as st

from gxw.models import GXWFormatError
from gxw.token_encoder import encode_instruction
from gxw.token_listing import TokenInstruction, decode_token_listing, decode_token_program
from gxw.token_pou import parse_token_fragment
from tools.offline_inputs import private_path
from plc.target_identity import Q_CPUS, PARAMETER_ONLY


def instruction(raw, profile="fx3u", encoding="cp936"):
    data = bytes.fromhex(raw) if isinstance(raw, str) else raw
    listing = decode_token_program(parse_token_fragment(data, 0, len(data)),
                                   profile=profile, text_encoding=encoding)
    assert not listing.gaps and len(listing.records) == 1
    result, = listing.instructions
    return result


NATIVE_SLICES = json.loads(private_path("tests/fixtures/gxw_token_native_20260919.json").read_text("utf-8"))["cases"]

#: Encoding profiles are lexical grammars, not CPUs. Stored widths need the real
#: CPU identity, so a profile-only case names the menu target it stands for.
_PROFILE_CPU = {
    "fx": "FX3U/FX3UC", "fx3u": "FX3U/FX3UC", "fx1s": "FX1S", "fx3g": "FX3G/FX3GC",
    "q02": "Q02/Q02H", "q03udv": "Q03UDV", "l02": "L02/L02-P",
}

#: Frozen native groups identify their CPU by number; the remaining groups are
#: the FX3U/FX3UC menu target.
_GROUP_CPU = {518: "FX1S", 521: "FX3G/FX3GC"}


def _group_cpu(group):
    return _GROUP_CPU.get(group["cpu"], "FX3U/FX3UC")


def test_offline_source_grammar_covers_programmable_catalogue_without_native_admission(monkeypatch):
    from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES
    from gxw.token_profiles import source_token_profile, source_token_capability
    from gxw import native_diagnostics
    monkeypatch.setattr(native_diagnostics, 'native_code_reader_scope',
                        lambda *args: (_ for _ in ()).throw(AssertionError('native admission used offline')))
    for target in NATIVE_PROJECT_PROFILES:
        for member in [target.cpu, *target.cpu.removesuffix('(RemoteI/O)').split('/')]:
            if target.program_supported:
                profile = source_token_profile(member)
                assert profile == source_token_profile(member, language='sfc')
                assert source_token_capability(member)['native_validation'] == 'not_run'
                assert encode_instruction('LD', ['M0'], profile=profile, text_encoding='cp936', plc_model=member) == bytes.fromhex('03000304900004')
            else:
                assert source_token_capability(member)['status'] == 'unsupported'
                with pytest.raises(GXWFormatError, match='parameter-only'):
                    source_token_profile(member)


@pytest.mark.parametrize('cpu', [cpu for cpu in Q_CPUS if cpu not in PARAMETER_ONLY])
@pytest.mark.parametrize('encoding,text', [('cp936', '中文'), ('cp932', '日本'), ('cp949', '한글'), ('cp1252', 'café')])
def test_all_q_codepages_keep_labels_strings_titles_and_module_bits(cpu, encoding, text):
    from gxw.token_encoder import encode_text_record
    from gxw.token_profiles import source_token_profile
    profile = source_token_profile(cpu)
    payload = text.encode(encoding)
    assert len(payload) == 4
    # Header/operand extents come from the independently frozen Q encoder
    # forms; only text bytes differ across the four declared code pages.
    expected_label = bytes.fromhex('0400020407df') + payload + bytes.fromhex('07')
    expected_string = bytes.fromhex('054c05030507ee') + payload + bytes.fromhex('0704a80004')
    label = encode_instruction('LD', ["'" + text], profile=profile, plc_model=cpu, text_encoding=encoding)
    string = encode_instruction('$MOV', ['"' + text + '"', 'D0'], profile=profile, plc_model=cpu, text_encoding=encoding)
    assert label == expected_label and string == expected_string
    assert instruction(label, profile, encoding).args == ("'" + text,)
    assert instruction(string, profile, encoding).args == ('"' + text + '"', 'D0')
    for kind, code, field in [('statement', 0x80, 4), ('note', 0x82, 1)]:
        record = encode_text_record(kind, text, profile=profile, text_encoding=encoding)
        assert record == bytes((8, code, field)) + payload + bytes((8,))
        listing = decode_token_program(parse_token_fragment(record, 0, len(record)), profile=profile, text_encoding=encoding)
        assert listing.records[0].text == text
        longer = encode_text_record(kind, text + 'AB', profile=profile, text_encoding=encoding,
                                    source_record=listing.records[0])
        reread = decode_token_program(parse_token_fragment(longer, 0, len(longer)), profile=profile, text_encoding=encoding)
        assert reread.records[0].text == text + 'AB'
    assert encode_instruction('OUT', [r'U9\G36320.1'], profile=profile, plc_model=cpu,
                              text_encoding=encoding) == bytes.fromhex('0420030404f8090404f2010405abe08d05')


@pytest.mark.parametrize("case", NATIVE_SLICES, ids=lambda row: f"{row['case']}-{row['pou_offset']}")
def test_encode_matches_original_native_project_and_separate_csv_slice(case):
    assert encode_instruction(case["opcode"], case["operands"], profile="fx3u",
                              text_encoding="cp936", plc_model=_PROFILE_CPU['fx3u']) == bytes.fromhex(case["raw_hex"])


def test_each_literal_fact_is_backed_by_frozen_native_encoder_output():
    # Expected type bytes come from the retained vendor encoder, not this
    # encoder's output or a mnemonic D/P naming heuristic.
    expected, expected_headers = {}, {}
    root = private_path("research/results/token-20260919")
    bodies = []
    for name in ("native-conversion-corpus.json", "native-conversion-ladder-mode.json",
                 "native-operand-corpus-20260920.json"):
        for case in json.loads((root / name).read_text("utf-8"))["cases"]:
            if case.get("encode_result") == "0x00000000" and case.get("body_hex"):
                bodies.append((bytes.fromhex(case["body_hex"]), case["native_records"][0]))
    bodies.extend((bytes.fromhex(row["raw_hex"]), {"op": row["opcode"]}) for row in NATIVE_SLICES)
    for raw, native in bodies:
        listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), text_encoding="cp936")
        assert not listing.gaps
        first = listing.instructions[0]
        assert first.mnemonic == native["op"]
        if any(len(operand.tokens) > 1 for operand in first.operands):
            key = ":".join(".".join(f"{token.raw[1]:02x}" for token in operand.tokens)
                           for operand in first.operands)
            expected_headers.setdefault(first.mnemonic, {}).setdefault(key, set()).add(first.tokens[0].raw.hex())
        for index, operand in enumerate(first.operands):
            code = operand.tokens[-1].raw[1]
            if 0xe8 <= code <= 0xee:
                expected.setdefault(first.mnemonic, {}).setdefault(str(index), set()).add(f"{code:02x}")
    resource = Path(__file__).resolve().parents[1] / "src/gxw/templates/fx-token-encoder-forms.json"
    facts = json.loads(resource.read_text("utf-8"))
    assert facts["literal_types"] == {op: {index: sorted(codes) for index, codes in params.items()}
                                       for op, params in expected.items()}
    assert facts["literal_types"]["DADD"] == {"0": ["e9"], "1": ["e9"]}
    assert facts["literal_types"]["BMOV"] == {"2": ["e8"]}
    assert facts["header_forms"] == {op: {key: sorted(headers) for key, headers in forms.items()}
                                     for op, forms in expected_headers.items()}


def test_source_and_package_export_keep_encoder_facts_without_private_evidence(monkeypatch):
    from scripts.export_open_source import TEMPLATE_NAMES, candidates, safe_template_json
    from gxw import token_encoder
    root = Path(__file__).resolve().parents[1]
    name = 'fx-token-encoder-forms.json'
    assert name in TEMPLATE_NAMES
    resource = root / 'src/gxw/templates' / name
    assert resource in set(candidates(root))
    sanitized = safe_template_json(resource)
    facts = json.loads(sanitized)
    assert 'evidence' not in facts and b'research/evidence' not in sanitized
    original = json.loads(resource.read_bytes())
    assert facts['literal_types'] == original['literal_types']
    assert facts['header_forms'] == original['header_forms']
    assert facts['additional_source_forms'] == original['additional_source_forms']
    q_name = 'q-token-encoder-forms.json'
    q_resource = root / 'src/gxw/templates' / q_name
    assert q_name in TEMPLATE_NAMES and q_resource in set(candidates(root))
    q_export = safe_template_json(q_resource)
    q_original = json.loads(q_resource.read_bytes())
    assert json.loads(q_export) == {key: value for key, value in q_original.items() if key != 'evidence'}
    assert b'research/' not in q_export and b'gxworks-agent-private' not in q_export
    with monkeypatch.context() as patch:
        patch.setattr(token_encoder, 'template_bytes', lambda requested: sanitized if requested == name else None)
        token_encoder._encoder_facts.cache_clear()
        try:
            # The exact expected bytes are an independent native witness.
            case = next(row for row in NATIVE_SLICES if row['opcode'] == 'MOV')
            assert encode_instruction(case['opcode'], case['operands'], profile='fx3u', text_encoding='cp936', plc_model=_PROFILE_CPU['fx3u']) == bytes.fromhex(case['raw_hex'])
        finally:
            token_encoder._encoder_facts.cache_clear()


@pytest.mark.parametrize("value", (-32768, -1, 0, 255, 256, 32767))
def test_word_constants_match_frozen_native_operand_oracle(value):
    rows = json.loads(private_path("research/results/token-20260919/native-operand-corpus-20260920.json")
                      .read_text("utf-8"))["cases"]
    native = next(row for row in rows if row["text"] == f"MOV K{value} D100"
                  and row.get("encode_result") == "0x00000000")
    assert encode_instruction("MOV", [f"K{value}", "D100"], profile="fx3u", text_encoding=None, plc_model=_PROFILE_CPU['fx3u']) == bytes.fromhex(native["body_hex"])[:-3]


@pytest.mark.parametrize("name,exact,rejected", [
    ("native-conversion-ladder-mode.json", 782, 0),
    ("native-operand-corpus-20260920.json", 828, 51),
])
def test_supported_construction_corpus_matches_independent_native_bytes(name, exact, rejected):
    rows = json.loads((private_path("research/results/token-20260919") / name).read_text("utf-8"))["cases"]
    matched = refused = 0
    for row in rows:
        if row.get("encode_result") != "0x00000000" or not row.get("body_hex"):
            continue
        op, *args = shlex.split(row["text"], posix=False)
        try:
            encoded = encode_instruction(op, args, profile="fx3u", text_encoding="cp936", plc_model=_PROFILE_CPU['fx3u'])
        except GXWFormatError as exc:
            assert str(exc).startswith(("stored step width is unobserved", "unsupported operand spelling",
                                        "timer/counter OUT requires"))
            refused += 1
        else:
            assert encoded == bytes.fromhex(row["body_hex"])[:-3], row["text"]
            matched += 1
    # Pin the supported boundary so broad catch-and-ignore cannot pass.
    assert (matched, refused) == (exact, rejected)


@settings(max_examples=100, deadline=None, derandomize=True)
@example(-32768, 0)
@example(32767, 65535)
@given(st.integers(-32768, 32767), st.integers(0, 65535))
def test_new_word_constant_and_device_decode_to_requested_values(value, address):
    raw = encode_instruction("MOV", [f"K{value}", f"D{address}"], profile="fx3u", text_encoding=None, plc_model=_PROFILE_CPU['fx3u'])
    decoded = instruction(raw)
    assert decoded.args == (f"K{value}", f"D{address}")
    assert decoded.step_width == 5


@settings(max_examples=100, deadline=None, derandomize=True)
@example(0, 0)
@example(0x80000000, 65535)
@example(0xb8c3cad0, 256)  # Native display rounds this real binary32 value.
@given(st.integers(0, 0xffffffff).filter(lambda bits: bits & 0x7f800000 != 0x7f800000),
       st.integers(0, 65535))
def test_editing_another_operand_preserves_every_finite_scientific_float_bit(bits, address):
    raw = bytes.fromhex("054c090f0507ed") + struct.pack("<I", bits) + bytes.fromhex("0704a86404")
    before = instruction(raw)
    edited = encode_instruction("DEMOV", [before.args[0], f"D{address}"], profile="fx3u",
                                text_encoding="cp936", source_instruction=before, plc_model=_PROFILE_CPU['fx3u'])
    after = instruction(edited)
    assert after.tokens[0].raw == before.tokens[0].raw
    assert after.operands[0].tokens == before.operands[0].tokens
    assert after.args[1] == f"D{address}"


def test_native_rounded_operand_corpus_noops_are_exactly_preserved():
    rows = json.loads(private_path("research/results/token-20260919/native-operand-corpus-20260920.json")
                      .read_text("utf-8"))["cases"]
    checked = 0
    for row in rows:
        if row.get("encode_result") != "0x00000000":
            continue
        body = bytes.fromhex(row["body_hex"])
        listing = decode_token_program(parse_token_fragment(body, 0, len(body)), text_encoding="cp936")
        assert not listing.gaps
        for record in listing.instructions:
            original = b"".join(token.raw for token in record.tokens)
            assert encode_instruction(record.mnemonic, record.args, profile="fx3u", text_encoding="cp936",
                                      source_instruction=record, plc_model=_PROFILE_CPU['fx3u']) == original
        checked += 1
    assert checked == 879


def test_decimal_float_is_not_silently_reencoded_through_python_rounding():
    source = instruction("054c090f0507ec53069e3f0704a86404")
    after = encode_instruction("DEMOV", [source.args[0], "D101"], profile="fx3u",
                               text_encoding=None, source_instruction=source, plc_model=_PROFILE_CPU['fx3u'])
    assert after == bytes.fromhex("054c090f0507ec53069e3f0704a86504")
    assert encode_instruction("DEMOV", ["E1.23456789", "D100"], profile="fx3u",
                              text_encoding=None, plc_model=_PROFILE_CPU['fx3u']) == bytes.fromhex("054c090f0507ec53069e3f0704a86404")
    assert encode_instruction("DEMOV", ["E1.234567+0", "D100"], profile="fx3u",
                              text_encoding=None, plc_model=_PROFILE_CPU['fx3u']) == bytes.fromhex("054c090f0507ed4b069e3f0704a86404")


def test_replacing_an_opcode_preserves_an_unchanged_compatible_float_operand():
    source = instruction("054c090f0507ec53069e3f0704a86404")
    after = encode_instruction("DEADD", [source.args[0], "D100", "D110"], profile="fx3u",
                               text_encoding=None, source_instruction=source, plc_model=_PROFILE_CPU['fx3u'])
    assert after == bytes.fromhex("05490d300507ec53069e3f0704a8640404a86e04")


def test_source_header_nonminimal_operands_and_aliases_are_preserved():
    # A source reader preserves a positive stored width even if it differs
    # from today's expected-width rule. Numeric aliases must not rewrite it.
    before = instruction("040007040590010005")
    assert encode_instruction("LD", ["M0001"], profile="fx3u", text_encoding=None,
                              source_instruction=before, plc_model=_PROFILE_CPU['fx3u']) == bytes.fromhex("040007040590010005")
    edited = encode_instruction("LD", ["M256"], profile="fx3u", text_encoding=None,
                                source_instruction=before, plc_model=_PROFILE_CPU['fx3u'])
    assert edited == bytes.fromhex("040007040590000105")
    special = instruction("03240304900204")
    assert encode_instruction("RST", ["M8123"], profile="fx3u", text_encoding=None,
                              source_instruction=special, plc_model=_PROFILE_CPU['fx3u']) == bytes.fromhex("042402040590bb1f05")


@pytest.mark.parametrize("profile,op,args,expected", [
    ("fx3u", "LD", ["X10"], "030003049c0804"),
    ("q02", "LD", ["X10"], "030003049c1004"),
    ("q03udv", "LD", ["X10"], "030003049c1004"),
    ("q03udv", "MOV", ["K1", "D0"], "054c02000504e8010404a80004"),
    ("l02", "MOV", ["K1", "D0"], "054c02000504e8010404a80004"),
    ("fx3u", "END", [], "033403"),
    ("q02", "END", [], "033403"),
    ("q03udv", "END", [], "04340204"),
    ("l02", "END", [], "033403"),
])
def test_cpu_grammars_keep_address_bases_and_original_header_subsets(profile, op, args, expected):
    assert encode_instruction(op, args, profile=profile, text_encoding=None, plc_model=_PROFILE_CPU[profile]) == bytes.fromhex(expected)


@pytest.mark.parametrize('profile,cpu,matched,refused', [
    ('fx1s', 518, 644, 3), ('fx3g', 521, 1090, 3),
    ('fx3u', 513, 87, 0), ('fx3u', 514, 379, 0), ('fx3u', 515, 410, 0),
    ('fx3u', 516, 577, 0), ('fx3u', 517, 910, 3), ('fx3u', 519, 750, 3), ('fx3u', 522, 982, 3),
])
def test_cpu_specific_construction_matches_independent_native_conversion(profile, cpu, matched, refused):
    import base64
    root = private_path(f'research/experiments/offline-token-20261021/fx-{cpu}')
    requests = json.loads((root / 'requests.json').read_text('utf-8'))['requests']
    results = [json.loads(line) for line in (root / 'native-stdout.jsonl').read_bytes().splitlines()]
    actual_count = rejected = 0
    for request, result in zip(requests, results, strict=True):
        if result['return_code'] != '0x00000000':
            continue
        op, *args = shlex.split(request['text'], posix=False)
        try:
            encoded = encode_instruction(op, args, profile=profile, text_encoding='cp936', plc_model=_GROUP_CPU.get(cpu, 'FX3U/FX3UC'))
        except GXWFormatError as error:
            # Native accepts these texts while dropping the trailing .F;
            # preserve the user's requested meaning instead of copying that
            # lossy conversion behaviour into the source writer.
            assert str(error).startswith('unsupported operand spelling:') and args[0].endswith('V1.F')
            rejected += 1
        else:
            assert encoded == base64.b64decode(result['output_base64'])[:-3], request['text']
            actual_count += 1
    assert actual_count == matched and rejected == refused
    assert encode_instruction('SET', ['M0'], profile=profile, text_encoding=None, plc_model=_GROUP_CPU.get(cpu, 'FX3U/FX3UC')) == bytes.fromhex('03230304900004')
    if profile == 'fx1s':
        with pytest.raises(GXWFormatError, match='unknown instruction'):
            encode_instruction('ADPRW', ['H1', 'H1', 'H0', 'H1', 'D0'], profile=profile, text_encoding=None, plc_model=_GROUP_CPU.get(cpu, 'FX3U/FX3UC'))


def test_all_original_q_source_instructions_have_exact_noop_encoding():
    count = 0
    with ZipFile(private_path("research/" + "evidence/gxw-q-token-grammar-20260927.zip")) as archive:
        for case in json.loads(archive.read("manifest.json"))["programs"]:
            encoding = case["context"]["text_encoding"]
            listing = decode_token_listing(archive.read(case["source"]), profile="q03udv", text_encoding=encoding)
            assert not listing.gaps
            for record in listing.instructions:
                assert encode_instruction(record.mnemonic, record.args, profile="q03udv", text_encoding=encoding,
                                          source_instruction=record, plc_model=_PROFILE_CPU['q03udv']) == b"".join(token.raw for token in record.tokens)
                count += 1
    assert count > 7000


def test_source_q_timer_role_is_preserved_and_new_role_uses_native_token_identity():
    source = instruction("0424040404c20004", "q03udv")
    assert encode_instruction("RST", ["T1"], profile="q03udv", text_encoding=None,
                              source_instruction=source, plc_model=_PROFILE_CPU['q03udv']) == bytes.fromhex("0424040404c20104")
    assert encode_instruction("RST", ["T1"], profile="q03udv", text_encoding=None, plc_model=_PROFILE_CPU['q03udv']) == bytes.fromhex("0424040404c20104")


Q_NATIVE = json.loads(private_path('tests/fixtures/gxw_q_token_native_20261021.json').read_text('utf-8'))


@pytest.mark.parametrize('group,accepted', list(zip(Q_NATIVE['construction'], (365, 311, 260, 306, 355, 356))),
                         ids=lambda row: row['cpu'] if isinstance(row, dict) else str(row))
def test_q_l_cpu_storage_groups_match_independent_native_construction_without_native_runtime(group, accepted, monkeypatch):
    import ctypes
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError('pure token encoding attempted a native operation')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(ctypes, 'CDLL', forbidden)
    monkeypatch.setattr(ctypes, 'WinDLL', forbidden)
    checked = 0
    for case in group['cases']:
        if case['return_code'] != '0x00000000':
            continue
        op, *args = shlex.split(case['text'], posix=False)
        actual = encode_instruction(op, args, profile=group['profile'], plc_model=group['cpu'], text_encoding='cp936')
        assert actual == bytes.fromhex(case['raw'])[:-group['end_bytes']], case['text']
        checked += 1
    assert checked == accepted


@pytest.mark.parametrize('group,accepted', list(zip(Q_NATIVE['holdouts'], (108, 108, 105, 108, 108, 105))),
                         ids=lambda row: row['cpu'] if isinstance(row, dict) else str(row))
def test_q_l_storage_width_holdouts_cover_compact_aliases_modifiers_and_variable_strings(group, accepted):
    checked = 0
    for case in group['cases']:
        if case['return_code'] != '0x00000000':
            continue
        op, *args = shlex.split(case['text'], posix=False)
        assert encode_instruction(op, args, profile=group['profile'], plc_model=group['cpu'],
            text_encoding='cp936') == bytes.fromhex(case['raw'])[:-group['end_bytes']], case['text']
        checked += 1
    assert checked == accepted


@pytest.mark.parametrize('group,accepted,refused', list(zip(Q_NATIVE['floating'], (335, 165, 335), (0, 1, 0))),
                         ids=lambda row: row['cpu'] if isinstance(row, dict) else str(row))
def test_q_literal_roles_keep_binary32_binary64_and_cpu_specific_intermediate_rounding(group, accepted, refused):
    checked = rejected = 0
    for case in group['cases']:
        if case['return_code'] != '0x00000000':
            continue
        op, *args = shlex.split(case['text'], posix=False)
        if case['text'] == 'EMOV E2.2250738585072-308 D0' and group['cpu'] == 'Q02/Q02H':
            # The legacy converter silently underflows this nonzero input to
            # zero. The editor must reject that loss, not reproduce it.
            with pytest.raises(GXWFormatError, match='range|underflow'):
                encode_instruction(op, args, profile=group['profile'], plc_model=group['cpu'], text_encoding=None)
            rejected += 1
            continue
        assert encode_instruction(op, args, profile=group['profile'], plc_model=group['cpu'],
            text_encoding=None) == bytes.fromhex(case['raw'])[:-group['end_bytes']], case['text']
        checked += 1
    assert (checked, rejected) == (accepted, refused)


def test_q_binary64_display_matches_independent_native_formatter_and_keeps_source_bits():
    from gxw.token_q import q_operand_text
    assert len(Q_NATIVE['double_display']) == 170
    for case in Q_NATIVE['double_display']:
        raw = bytes.fromhex(case['source']['raw'])
        operand = raw[5:5 + raw[5]]
        assert q_operand_text(operand, text_encoding=None) == case['records'][0]['args'][0]
        source = instruction(raw[:-4], 'q03udv')
        edited = encode_instruction('EDMOV', [source.args[0], 'D7'], profile='q03udv', plc_model='Q03UDV',
            text_encoding=None, source_instruction=source)
        assert edited == raw[:-8] + bytes.fromhex('04a80704')


@settings(max_examples=60, deadline=None, derandomize=True, database=None)
@given(bits=st.integers(min_value=0, max_value=2**64 - 1).filter(lambda value: value >> 52 & 0x7ff != 0x7ff),
       address=st.integers(min_value=0, max_value=255))
def test_q_unchanged_binary64_operand_is_never_reencoded_from_its_rounded_display(bits, address):
    raw = bytes.fromhex('054c0510050be4') + bits.to_bytes(8, 'little') + bytes.fromhex('0b04a80004')
    source = instruction(raw, 'q03udv')
    after = encode_instruction('EDMOV', [source.args[0], f'D{address}'], profile='q03udv', plc_model='Q03UDV',
        text_encoding=None, source_instruction=source)
    assert after == raw[:-4] + bytes((4, 0xa8, address, 4))


@settings(max_examples=80, deadline=None, derandomize=True, database=None)
@given(value=st.integers(min_value=-2**31, max_value=2**31 - 1), address=st.integers(min_value=0, max_value=255),
       cpu=st.sampled_from(('Q02/Q02H', 'Q03UDV', 'L02/L02-P')))
def test_q_signed_dword_role_is_independent_of_literal_magnitude(value, address, cpu):
    size = 4 if value < 0 else max(1, (value.bit_length() + 7) // 8)
    # Independent oracle: native DMOV role is E9 even for K0/K1; signed
    # negatives occupy four bytes and positive payloads are zero-extended.
    literal = bytes((size + 3, 0xe9)) + value.to_bytes(size, 'little', signed=value < 0) + bytes((size + 3,))
    assert encode_instruction('DMOV', [f'K{value}', f'D{address}'], profile='q', plc_model=cpu,
        text_encoding=None) == bytes.fromhex('054c030105') + literal + bytes((4, 0xa8, address, 4))


@pytest.mark.parametrize('profile,cpu,expected', [
    ('q03udv', 'Q02/Q02H', '033403'), ('q', 'Q06UDV', '04340204'),
    ('q', 'Q04UDPV', '04340204'), ('q', 'Q00J', '033403'), ('l', 'L02-P', '033403'),
])
def test_q_exact_cpu_controls_end_storage_instead_of_the_lexical_profile(profile, cpu, expected):
    assert encode_instruction('END', [], profile=profile, plc_model=cpu, text_encoding=None) == bytes.fromhex(expected)


def test_q_source_header_is_recomputed_when_a_numeric_boundary_or_alias_changes_width():
    source = instruction('054c02000505b0ffff0504a80004', 'q03udv')
    assert encode_instruction('MOV', ['ZR65536', 'D0'], profile='q03udv', plc_model='Q03UDV',
        text_encoding=None, source_instruction=source) == bytes.fromhex('054c03000506b00000010604a80004')
    source = instruction('054903010504a8000404a8020404a80404', 'q03udv')
    assert encode_instruction('+', ['D0', 'D2', 'D0'], profile='q03udv', plc_model='Q03UDV',
        text_encoding=None, source_instruction=source) == bytes.fromhex('054904010504a8000404a8020404a80004')


@pytest.mark.parametrize('text', ['"' + 'A' * 33 + '"', '"' + '中' * 17 + '"'])
def test_q_string_limits_use_encoded_bytes_and_do_not_truncate(text):
    with pytest.raises(GXWFormatError, match='32 encoded bytes'):
        encode_instruction('$MOV', [text, 'D0'], profile='q03udv', plc_model='Q03UDV', text_encoding='cp936')


@pytest.mark.parametrize('group,accepted', list(zip(Q_NATIVE['named_operands'], (80, 56, 80))),
                         ids=lambda row: row['cpu'] if isinstance(row, dict) else str(row))
def test_q_named_call_storage_preserves_binding_requirements_and_matches_independent_native_bytes(group, accepted):
    from gxw.token_q import q_instruction_requirements
    checked = 0
    for case in group['cases']:
        if case['return_code'] != '0x00000000':
            continue
        op, *args = shlex.split(case['text'], posix=False)
        raw = encode_instruction(op, args, profile=group['profile'], plc_model=group['cpu'], text_encoding='cp936')
        assert raw == bytes.fromhex(case['raw'])[:-group['end_bytes']], case['text']
        requirements = q_instruction_requirements(raw[:raw[0]], profile=group['profile'])
        assert requirements['requires_named_call_binding'] is True
        assert requirements['native_compile'] == 'not_run' and requirements['execution'] == 'unverified'
        checked += 1
    assert checked == accepted


def test_q_version_condition_is_reported_separately_from_unchanged_lexical_storage():
    from gxw.token_q import q_instruction_requirements
    names = {'LDPI', 'LDFI', 'ORPI', 'ORFI', 'ANDPI', 'ANDFI'}
    observed = {}
    for state in ('v1', 'v25', 'empty', 'default'):
        for case in Q_NATIVE['version_states'][state]:
            op, *args = case['text'].split()
            if op not in names:
                continue
            if state in ('empty', 'default'):
                assert case['return_code'] == '0x04020003'
                continue
            assert case['return_code'] == '0x00000000'
            expected = bytes.fromhex(case['raw'])[:-4]
            observed.setdefault(op, set()).add(expected)
            assert encode_instruction(op, args, profile='q03udv', plc_model='Q03UDV', text_encoding=None) == expected
            requirements = q_instruction_requirements(expected[:expected[0]])
            assert requirements['requires_native_version_context'] is True
            assert requirements['native_compile'] == 'not_run' and requirements['execution'] == 'unverified'
    assert set(observed) == names and all(len(forms) == 1 for forms in observed.values())


@pytest.mark.parametrize('group', Q_NATIVE['labels'], ids=lambda row: row['cpu'])
def test_q_pointer_and_interrupt_labels_match_original_native_constructor_and_ranges(group):
    for case in group['cases']:
        if case['return_code'] == '0x00000000':
            assert encode_instruction(case['text'], [], profile=group['profile'], plc_model=group['cpu'],
                text_encoding=None) == bytes.fromhex(case['raw'])[:-group['end_bytes']]
        else:
            with pytest.raises(GXWFormatError, match='label|range'):
                encode_instruction(case['text'], [], profile=group['profile'], plc_model=group['cpu'], text_encoding=None)


@pytest.mark.parametrize('group', Q_NATIVE['allocation_contexts'],
                         ids=lambda row: row['cpu'] + '-' + row['context_name'])
def test_q_saved_extension_ranges_match_native_widths_and_reject_configured_capacity_overflow(group):
    from gxw.token_q_encoder import encode_q_header, encode_q_operand, q_project_storage_context
    context = q_project_storage_context(bytes.fromhex(group['parameter_hex']) + b'\xff\xff', cpu=group['cpu'])
    assert context.status == 'available' and context.has_extended_ranges is True
    accepted = refused = 0
    for case in group['results']:
        op, *args = shlex.split(case['text'], posix=False)
        operands = tuple(encode_q_operand(arg, op=op, index=index, profile='q', cpu=group['cpu'])
                         for index, arg in enumerate(args))
        if case['return_code'] != '0x00000000':
            with pytest.raises(GXWFormatError, match='capacity'):
                encode_q_header(op, args, operands, profile='q', cpu=group['cpu'], storage_context=context)
            refused += 1
            continue
        actual = encode_q_header(op, args, operands, profile='q', cpu=group['cpu'], storage_context=context) + b''.join(operands)
        assert actual == bytes.fromhex(case['raw'])[:-group['end_bytes']], case['text']
        accepted += 1
    assert (accepted, refused) == ((20, 0) if group['context_name'] == 'bank3-zr63' else (18, 2))


@settings(max_examples=60, deadline=None, derandomize=True, database=None)
@given(group_index=st.integers(min_value=0, max_value=8), address=st.integers(min_value=16, max_value=35))
def test_q_extension_region_width_is_bound_to_the_native_region_witness(group_index, address):
    from gxw.token_q_encoder import encode_q_header, encode_q_operand, q_storage_context
    group = Q_NATIVE['allocation_contexts'][group_index]
    context = q_storage_context(bytes.fromhex(group['parameter_hex']), cpu=group['cpu'])
    witness = next(case for case in group['results'] if case['text'] == 'MOV D16 D2')
    expected = bytes.fromhex(witness['raw'])[:-group['end_bytes']]
    # The independently captured header is invariant inside this measured
    # region. Only the original D token's one-byte address is replaced.
    expected = expected[:7] + bytes((address,)) + expected[8:]
    args = (f'D{address}', 'D2')
    operands = tuple(encode_q_operand(arg, op='MOV', index=index, profile='q', cpu=group['cpu'])
                     for index, arg in enumerate(args))
    actual = encode_q_header('MOV', args, operands, profile='q', cpu=group['cpu'], storage_context=context) + b''.join(operands)
    assert actual == expected


@pytest.mark.parametrize('raw,reason', [
    (None, 'missing'), (b'\xff\xff', 'one Q device-allocation'),
    (b'\x06\x00\x00\x20\x00\x00', 'terminal'),
    (b'\x06\x00\x00\x20\x00\x00\xff\xff\x00', 'after terminal'),
    (b'\x06\x00\x00\x20\x00\x00' * 2 + b'\xff\xff', 'one Q device-allocation'),
])
def test_q_missing_or_unimplemented_parameters_are_distinct_from_proven_no_extension(raw, reason):
    from gxw.token_q_encoder import encode_q_header, q_project_storage_context
    context = q_project_storage_context(raw, cpu='Q03UDV')
    assert context.status == 'unavailable' and context.has_extended_ranges is None
    assert reason in context.reason
    with pytest.raises(GXWFormatError, match='allocation context'):
        encode_q_header('MOV', ['D0', 'D2'], [bytes.fromhex('04a80004'), bytes.fromhex('04a80204')],
                        cpu='Q03UDV', storage_context=context)
    assert encode_q_header('LD', ['M0'], [bytes.fromhex('04900004')], cpu='Q03UDV', storage_context=context) == bytes.fromhex('030003')


def test_q_context_keeps_cpu_identity_and_all_original_parameter_rows():
    from gxw.token_q_encoder import encode_q_header, q_project_storage_context
    # Two ordinary capacities plus an unknown retained row, no extensions.
    record = bytes.fromhex('120000200300a8010020b4010010907f0100')
    context = q_project_storage_context(record + b'\xff\xff', cpu='Q02/Q02H')
    assert context.status == 'available' and context.has_extended_ranges is False
    assert context.parameter_record == record and context.ranges == ()
    assert context.cpu == 'Q02/Q02H' and context.as_dict()['ranges'] == []
    with pytest.raises(GXWFormatError, match='differs from the saved CPU'):
        encode_q_header('END', [], [], cpu='Q03UDV', storage_context=context)


def test_q_source_header_requires_context_when_original_width_cannot_be_explained():
    from gxw.token_q_encoder import encode_q_header, q_header_compatible, q_storage_context
    group = next(group for group in Q_NATIVE['allocation_contexts']
                 if group['group'] == 'q71' and group['context_name'] == 'bank2-zr64')
    context = q_storage_context(bytes.fromhex(group['parameter_hex']), cpu=group['cpu'])
    source = instruction('054c03000504a8100404a80204', 'q')
    before = (bytes.fromhex('04a81004'), bytes.fromhex('04a80204'))
    same_region = (bytes.fromhex('04a81104'), before[1])
    crossed = (bytes.fromhex('04a80f04'), before[1])
    assert not q_header_compatible(source, 'MOV', ['D17', 'D2'], same_region, profile='q', cpu='Q03UDV')
    with pytest.raises(GXWFormatError, match='allocation context'):
        encode_q_header('MOV', ['D17', 'D2'], same_region, profile='q', cpu='Q03UDV', source=source)
    assert q_header_compatible(source, 'MOV', ['D17', 'D2'], same_region, profile='q', cpu='Q03UDV', storage_context=context)
    assert not q_header_compatible(source, 'MOV', ['D15', 'D2'], crossed, profile='q', cpu='Q03UDV', storage_context=context)
    assert encode_q_header('MOV', ['D15', 'D2'], crossed, profile='q', cpu='Q03UDV', source=source,
                           storage_context=context) == bytes.fromhex('054c020005')


@pytest.mark.parametrize('case', [case for case in Q_NATIVE['extension_display'] if case['return_code'] == '0x00000000'],
                         ids=lambda row: hex(row['code']))
def test_q_ambiguous_extended_prefix_identity_survives_independent_destination_edits(case):
    raw = bytes.fromhex(case['raw'])[:-4]
    source = instruction(raw, 'q')
    expected, _ = case['projection']['records']
    assert source.mnemonic == expected['op'] and list(source.args) == expected['args']
    assert encode_instruction(source.mnemonic, source.args, profile='q', plc_model='Q03UDV',
                              text_encoding=None, source_instruction=source) == raw
    for first in (source.args[0], source.operands[0].storage_form()):
        changed = encode_instruction('MOV', [first, 'D3'], profile='q', plc_model='Q03UDV',
                                     text_encoding=None, source_instruction=source)
        assert changed == raw[:-2] + b'\x03\x04'
        after = instruction(changed, 'q')
        assert after.operands[0].storage_form() == source.operands[0].storage_form()
        assert after.operands[0].components(profile='q')[0]['role'] == 'extended_prefix'


def test_structured_operand_preserves_module_index_identity_from_native_construction():
    cases = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/native-prefix-results.json')
                       .read_text(encoding='utf-8'))
    witnesses = [row for row in cases if row.get('text') in ('MOV U0Z1\\G0 D2', 'MOV U0\\G0Z1 D2')]
    assert len(witnesses) == 2
    forms = []
    for row in witnesses:
        import base64
        assert row['result']['return_code'] == '0x00000000'
        raw = base64.b64decode(row['result']['output_base64'])[:-4]
        source = instruction(raw, 'q')
        form = source.operands[0].storage_form()
        forms.append(form)
        assert encode_instruction('MOV', [form, 'D2'], profile='q', plc_model='Q03UDV',
                                  text_encoding=None) == raw
        assert encode_instruction('MOV', [form, 'D3'], profile='q', plc_model='Q03UDV',
                                  text_encoding=None, source_instruction=source) == raw[:-2] + b'\x03\x04'
    assert forms[0]['text'] == forms[1]['text']
    assert forms[0]['token_codes'] != forms[1]['token_codes']


@pytest.mark.parametrize('value', [
    {'text': 'D0', 'token_codes': []}, {'text': 'D0', 'token_codes': ['a8', 'f0']},
    {'text': 'D0', 'token_codes': ['90']}, {'text': 'D0', 'token_codes': ['unknown']},
    {'text': 'D0', 'token_codes': [168]}, {'text': 'D0', 'token_codes': ['a8'], 'raw_hex': '04a80004'},
])
def test_structured_operands_cannot_inject_bytes_or_disagree_with_the_lexical_form(value):
    with pytest.raises(GXWFormatError):
        encode_instruction('MOV', [value, 'D2'], profile='q', plc_model='Q03UDV', text_encoding=None)


@pytest.mark.parametrize('group', json.loads(private_path(
    'research/experiments/offline-corpus-ladder-20261021/native-fx-source-results.json').read_text('utf-8')),
    ids=lambda row: str(row['cpu']))
def test_author_fx_mc_mcr_next_and_inline_text_flags_match_independent_native_controls(group):
    import base64
    from gxw.token_encoder import encode_text_record
    from gxw.token_listing import TokenText
    profile = 'fx1s' if group['cpu'] == 518 else 'fx3u'
    accepted = 0
    for case in group['rows']:
        if case['result']['return_code'] != '0x00000000':
            assert case['text'] == 'MC N0 M100Z1'
            continue
        accepted += 1
        if case['mode'] == 'encode':
            op, *args = case['text'].split()
            assert encode_instruction(op, args, profile=profile, text_encoding='cp936', plc_model=_group_cpu(group)) == base64.b64decode(
                case['result']['output_base64'])[:-3]
        else:
            raw = bytes.fromhex(case['raw'])[:-1]
            listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)),
                                           profile=profile, text_encoding='cp936')
            assert not listing.gaps
            if isinstance(listing.records[0], TokenText):
                source = listing.records[0]
                edited = encode_text_record(source.role, 'Updated inline text', text_encoding='cp936',
                                            source_record=source)
                assert edited[2] == source.tokens[0].raw[2]
                assert edited[3:-1] == b'Updated inline text'
    assert accepted == 11


_INLINE_TEXT_FIXTURE = json.loads(private_path('tests/fixtures/gxw_inline_text_native_20261021.json').read_text('utf-8'))
_INLINE_TEXT_CASES = _INLINE_TEXT_FIXTURE['author_records'] + _INLINE_TEXT_FIXTURE['native_controls']


@pytest.mark.parametrize('case', _INLINE_TEXT_FIXTURE['native_controls'], ids=lambda row: row['case'])
def test_new_inline_text_uses_independently_compiled_external_flags_and_q_statement_extent(case):
    from gxw.token_encoder import encode_text_record
    from gxw.token_listing import TokenText
    raw = bytes.fromhex(case['raw_hex'])
    source, = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile=case['profile'],
                                  text_encoding=case['text_encoding']).records
    assert isinstance(source, TokenText) and source.text == case['text']
    assert encode_text_record(source.role, source.text, profile=case['profile'],
        text_encoding=case['text_encoding'], source_record=source) == raw
    assert case['compile_result'] == 'completed_accepted'
    if case['new_default']:
        assert encode_text_record(case['kind'], case['text'], profile=case['profile'],
            text_encoding=case['text_encoding']) == raw


@settings(max_examples=55, deadline=None, derandomize=True)
@given(case=st.sampled_from(_INLINE_TEXT_CASES), length=st.integers(0, 251))
def test_inline_text_expansion_keeps_original_flags_and_updates_only_q_integrated_statement_extent(case, length):
    from gxw.token_encoder import encode_text_record
    from gxw.token_patch import edit_token_records
    raw = bytes.fromhex(case['raw_hex'])
    profile, encoding = case['profile'], case['text_encoding']
    body = bytes.fromhex('0202') + raw + bytes.fromhex('033403')
    listing = decode_token_program(parse_token_fragment(body, 0, len(body)), profile=profile, text_encoding=encoding)
    assert not listing.gaps
    source = listing.records[1]
    assert encode_text_record(source.role, source.text, profile=profile, text_encoding=encoding,
                              source_record=source) == raw
    text = 'x' * length
    result = edit_token_records(listing, [{'operation': 'replace', 'record_index': 1,
        'expected': {'kind': source.role, 'text': source.text, 'raw_hex': raw.hex()},
        'instructions': [{'kind': source.role, 'text': text}]}], text_encoding=encoding, plc_model="FX3U/FX3UC")
    assert result.records[0] == bytes.fromhex('0202') and result.records[2] == bytes.fromhex('033403')
    edited = result.records[1]
    assert edited[0] == edited[-1] == length + 4
    assert edited[3:-1] == text.encode(encoding)
    expected_field = ((length + 5) // 2 if profile == 'q' and source.role == 'statement' and raw[2] > 1
                      else raw[2])
    assert edited[2] == expected_field


@pytest.mark.parametrize('profile,encoding,text', [('fx3u', 'cp936', '说明'), ('q', 'cp949', '설명')])
def test_inline_text_extent_uses_project_encoded_bytes_and_rejects_overflow_without_truncation(profile, encoding, text):
    from gxw.token_encoder import encode_text_record
    raw = encode_text_record('statement', text, profile=profile, text_encoding=encoding)
    assert raw[0] == raw[-1] == len(text.encode(encoding)) + 4
    assert raw[3:-1].decode(encoding) == text
    with pytest.raises(GXWFormatError, match='251-byte'):
        encode_text_record('statement', text * 126, profile=profile, text_encoding=encoding)


@settings(max_examples=40, deadline=None, derandomize=True)
@given(bits=st.integers(0, 0xffffffff).filter(lambda value: value & 0x7f800000 != 0x7f800000),
       order=st.permutations([1, 2, 3]))
def test_record_moves_preserve_original_float_bits_and_requested_execution_order(bits, order):
    from gxw.token_patch import edit_token_records
    chunks = [bytes.fromhex('03000304900004'),
              bytes.fromhex('054c090f0507ed') + struct.pack('<I', bits) + bytes.fromhex('0704a86404'),
              bytes.fromhex('0202'), bytes.fromhex('032003049d0004'), bytes.fromhex('033403')]
    raw = b''.join(chunks)
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile='fx3u', text_encoding='cp936')
    assert not listing.gaps
    edits = [{'operation': 'move_before', 'record_index': index, 'target_record_index': 4,
              'expected': {'op': listing.records[index].mnemonic, 'args': list(listing.records[index].args),
                           'raw_hex': chunks[index].hex()}} for index in order]
    result = edit_token_records(listing, edits, text_encoding='cp936', plc_model='FX3U')
    assert result.records == tuple([chunks[0], *(chunks[index] for index in order), chunks[4]])
    assert result.body == b''.join(result.records)
    assert chunks[1] in result.records
    source = listing.records[1]
    expanded = edit_token_records(listing, [{'operation': 'replace', 'record_index': 1,
        'expected': {'op': source.mnemonic, 'args': list(source.args)},
        'instructions': [{'op': source.mnemonic, 'args': [operand.storage_form() for operand in source.operands]},
                         {'op': 'NOP', 'args': []}]}], text_encoding='cp936', plc_model="FX3U/FX3UC")
    assert expanded.records[1] == chunks[1]
    assert expanded.records[:1] + expanded.records[3:] == tuple(chunks[:1] + chunks[2:])


def test_empty_existing_fb_fragment_accepts_source_bound_end_boundary_without_creating_a_program_end():
    from gxw.token_patch import edit_token_records
    empty = decode_token_program(parse_token_fragment(b'', 0, 0), profile='fx3u', text_encoding='cp936')
    edit = {'operation': 'insert_before', 'record_index': 0, 'expected': {'kind': 'end_boundary'},
            'instructions': [{'op': 'LD', 'args': ['M0']}, {'op': 'OUT', 'args': ['Y0']}]}
    with pytest.raises(GXWFormatError, match='out-of-range'):
        edit_token_records(empty, [edit], text_encoding='cp936', plc_model="FX3U/FX3UC")
    result = edit_token_records(empty, [edit], text_encoding='cp936', allow_end_boundary=True, plc_model="FX3U/FX3UC")
    assert result.body == bytes.fromhex('03000304900004032003049d0004')
    assert len(result.records) == 2


@pytest.mark.parametrize('relative', [
    'marouane-daoudi/automatisme-lab-projects/01-traffic-lights/traffic_lights.gxw',
    'marouane-daoudi/automatisme-lab-projects/02-car-parking/PARKING.gxw',
    'marouane-daoudi/automatisme-lab-projects/03-washing-machine/Machine_a_laver.gxw',
    'marouane-daoudi/automatisme-lab-projects/04-burglar-alarm/burglar_alarm.gxw',
])
def test_fx1s_author_programs_can_construct_existing_stack_and_timer_forms(relative):
    from gxw.offline_project import read_offline_project
    raw = (Path('D:/download/GXW_samples_part_01_of_07/github/files') / relative).read_bytes()
    project = read_offline_project(raw)
    logical = next(row['source_name'] for row in project.sources() if row['language'] == 'ladder')
    listing = decode_token_listing(project.streams[logical], profile='fx1s', text_encoding=project.context['text_encoding'])
    witnesses = [row for row in listing.instructions if row.mnemonic in {'MPS', 'MRD', 'MPP', 'ORB', 'ANB', 'END'}
                 or row.mnemonic == 'OUT' and len(row.args) == 2]
    assert witnesses
    for source in witnesses:
        # The expected bytes are untouched author records, not this encoder.
        expected = b''.join(token.raw for token in source.tokens)
        assert encode_instruction(source.mnemonic, source.args, profile='fx1s', text_encoding='cp936', plc_model=_PROFILE_CPU['fx1s']) == expected
        if source.mnemonic == 'OUT':
            changed = encode_instruction('OUT', [source.args[0], 'K7'], profile='fx1s', text_encoding='cp936',
                                         source_instruction=source, plc_model=_PROFILE_CPU['fx1s'])
            assert instruction(changed, 'fx1s').args == (source.args[0], 'K7')


@pytest.mark.parametrize('profile', ['fx1s', 'fx3g'])
def test_common_fx_zero_operand_and_timer_storage_is_not_limited_by_per_cpu_first_instruction_samples(profile):
    assert encode_instruction('NOP', [], profile=profile, text_encoding=None, plc_model=_PROFILE_CPU[profile]) == bytes.fromhex('0202')
    assert encode_instruction('MPS', [], profile=profile, text_encoding=None, plc_model=_PROFILE_CPU[profile]) == bytes.fromhex('031a03')
    assert encode_instruction('MRD', [], profile=profile, text_encoding=None, plc_model=_PROFILE_CPU[profile]) == bytes.fromhex('031b03')
    assert encode_instruction('MPP', [], profile=profile, text_encoding=None, plc_model=_PROFILE_CPU[profile]) == bytes.fromhex('031c03')
    assert encode_instruction('OUT', ['T0', 'K100'], profile=profile, text_encoding=None, plc_model=_PROFILE_CPU[profile]) == bytes.fromhex(
        '0421030404c2000404e86404')


@pytest.mark.parametrize('group,accepted', list(zip(json.loads(private_path(
    'research/experiments/offline-corpus-ladder-20261021/native-fx-additional-results.json').read_text('utf-8')),
    (16, 16, 20, 35, 36, 24, 38, 38, 38, 36))), ids=lambda row: str(row['cpu']) if isinstance(row, dict) else None)
def test_additional_fx_source_forms_match_separate_cpu_native_controls(group, accepted):
    import base64
    profile = {518: 'fx1s', 521: 'fx3g'}.get(group['cpu'], 'fx3u')
    matched = 0
    assert group['returncode'] == 0
    for case in group['rows']:
        result = case['result']
        if result['return_code'] != '0x00000000':
            continue
        op, *args = case['text'].split()
        expected = base64.b64decode(result['output_base64'])[:-3]
        assert encode_instruction(op, args, profile=profile, text_encoding='cp936', plc_model=_group_cpu(group)) == expected, case['text']
        matched += 1
    assert matched == accepted


@pytest.mark.parametrize('filename,expected', [
    ('native-fx-parameters-results.json', [3, 3, 27, 32, 35, 50, 55, 57, 57, 55]),
    ('native-fx-second-sources-results.json', [0, 0, 15, 15, 15, 17, 17, 17, 17, 17]),
])
def test_fx_new_drive_pulse_and_counter_parameter_forms_match_native_controls(filename, expected):
    import base64
    groups = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/' + filename).read_text('utf-8'))
    matched = []
    for group in groups:
        count = 0
        profile = {518: 'fx1s', 521: 'fx3g'}.get(group['cpu'], 'fx3u')
        for case in group['rows']:
            if case['result']['return_code'] != '0x00000000':
                continue
            op, *args = case['text'].split()
            actual = encode_instruction(op, args, profile=profile, text_encoding='cp936', plc_model=_group_cpu(group))
            assert actual == base64.b64decode(case['result']['output_base64'])[:-3], case['text']
            count += 1
        matched.append(count)
    assert matched == expected


_FX_LEGAL_FORM_GROUP = json.loads(private_path(
    'research/experiments/offline-corpus-ladder-20261021/native-fx-legal-forms-results.json'
).read_text('utf-8'))[0]
_FX_LEGAL_FORM_CASES = [case for case in _FX_LEGAL_FORM_GROUP['rows']
                       if not case['form'].startswith('inline_text')]


def test_fx_buffer_memory_indexed_constants_and_drive_v_roles_match_independent_native_records():
    import base64
    matched, refused = 0, 0
    for case in _FX_LEGAL_FORM_CASES:
        op, *args = case['text'].split()
        if case['result']['return_code'] != '0x00000000':
            with pytest.raises(GXWFormatError):
                encode_instruction(op, args, profile='fx3u', plc_model='FX3U', text_encoding='cp936')
            refused += 1
            continue
        expected = base64.b64decode(case['result']['output_base64'])[:-3]
        actual = encode_instruction(op, args, profile='fx3u', plc_model='FX3U', text_encoding='cp936')
        assert actual == expected
        decoded = instruction(expected)
        forms = [operand.storage_form() for operand in decoded.operands]
        assert encode_instruction(op, forms, profile='fx3u', plc_model='FX3U', text_encoding='cp936') == expected
        assert encode_instruction(op, forms, profile='fx3u', plc_model='FX3U', text_encoding='cp936',
                                  source_instruction=decoded) == expected
        for operand in decoded.operands:
            if '\\' in operand.text:
                assert operand.components(profile='fx3u')[0]['role'] == 'module_prefix'
        matched += 1
    assert (matched, refused) == (73, 6)


@settings(max_examples=45, deadline=None, derandomize=True)
@given(case_index=st.integers(0, 78))
def test_fx_new_operand_form_replacements_match_independent_native_pairs(case_index):
    import base64
    positives = [case for case in _FX_LEGAL_FORM_CASES if case['result']['return_code'] == '0x00000000']
    target = positives[case_index % len(positives)]
    op = target['text'].split()[0]
    original = next(case for case in positives if case['text'].split()[0] == op)
    before = instruction(base64.b64decode(original['result']['output_base64'])[:-3])
    expected = base64.b64decode(target['result']['output_base64'])[:-3]
    after = instruction(expected)
    assert encode_instruction(op, [operand.storage_form() for operand in after.operands], profile='fx3u',
                              plc_model='FX3U', text_encoding='cp936', source_instruction=before) == expected


@pytest.mark.parametrize('form', [
    {'text': 'U0\\G0Z0', 'token_codes': ['f0', 'f8', 'ab']},
    {'text': 'U0\\G0', 'token_codes': ['f8', 'a8']},
    {'text': 'K6V6', 'token_codes': ['f0', 'e8']},
])
def test_fx_structured_operand_cannot_move_an_index_to_the_unit_or_change_its_role(form):
    with pytest.raises(GXWFormatError):
        encode_instruction('MOV', [form, 'D0'], profile='fx3u', plc_model='FX3U', text_encoding='cp936')


@settings(max_examples=50, deadline=None, derandomize=True)
@example(32767)
@example(32768)
@example(-32768)
@example(-32769)
@given(st.integers(-2147483648, 2147483647))
def test_fx_32_bit_counter_preset_promotes_literal_without_changing_its_five_step_storage(value):
    # These boundaries and bytes were obtained separately for C200 and C235
    # through the native FX constructor; the property varies the integer value.
    code, size = (0xe8, 2) if -32768 <= value <= 32767 else (0xe9, 4)
    count = size if value < 0 else max(1, (value.bit_length() + 7) // 8)
    payload = value.to_bytes(count, 'little', signed=value < 0)
    expected = bytes.fromhex('0421050404c5c804') + bytes((count + 3, code)) + payload + bytes((count + 3,))
    actual = encode_instruction('OUT', ['C200', f'K{value}'], profile='fx3u', text_encoding=None, plc_model=_PROFILE_CPU['fx3u'])
    assert actual == expected
    assert instruction(actual).args == ('C200', f'K{value}')


def test_fx_counter_edit_recomputes_width_and_does_not_retain_an_invalid_wide_preset():
    ordinary = instruction('0421030404c5c70404e80104')
    wide = encode_instruction('OUT', ['C200', 'K1'], profile='fx3u', text_encoding=None,
                              source_instruction=ordinary, plc_model=_PROFILE_CPU['fx3u'])
    assert wide == bytes.fromhex('0421050404c5c80404e80104')
    restored = encode_instruction('OUT', ['C199', 'K1'], profile='fx3u', text_encoding=None,
                                  source_instruction=instruction(wide), plc_model=_PROFILE_CPU['fx3u'])
    assert restored == bytes.fromhex('0421030404c5c70404e80104')
    with pytest.raises(GXWFormatError, match='range'):
        encode_instruction('OUT', ['C199', 'K32768'], profile='fx3u', text_encoding=None,
                           source_instruction=instruction('0421050404c5c80405e9008005'), plc_model=_PROFILE_CPU['fx3u'])


def test_fx_missing_timer_preset_is_saved_only_as_its_distinct_draft_source_record():
    rows = json.loads(private_path('research/results/token-20260919/native-operand-corpus-20260920.json').read_text('utf-8'))['cases']
    observed = 0
    for row in rows:
        if row['text'] not in {'OUT T10Z0', 'OUT T10V1', 'OUT C10Z0', 'OUT C10V1'}:
            continue
        assert row['encode_result'] == '0x00000000'
        op, *args = row['text'].split()
        with pytest.raises(GXWFormatError, match='preset'):
            encode_instruction(op, args, profile='fx3u', text_encoding=None, plc_model=_PROFILE_CPU['fx3u'])
        actual = encode_instruction(op, args, profile='fx3u', text_encoding=None, allow_draft=True, plc_model=_PROFILE_CPU['fx3u'])
        assert actual == bytes.fromhex(row['body_hex'])[:-3]
        assert instruction(actual).args == tuple(args)
        observed += 1
    assert observed == 4


def test_structured_fx_float_and_counter_constant_keep_their_selected_identity_when_the_value_changes():
    import base64
    rows = json.loads(private_path('research/results/token-20260919/native-operand-corpus-20260920.json').read_text('utf-8'))['cases']
    before = next(row for row in rows if row['text'] == 'DEMOV E1.0-1 D100')
    target = next(row for row in rows if row['text'] == 'DEMOV E1.0+0 D100')
    original = instruction(bytes.fromhex(before['body_hex'])[:-3])
    actual = encode_instruction('DEMOV', [{'text': 'E1.0', 'token_codes': ['ed']}, 'D100'],
        profile='fx3u', text_encoding=None, source_instruction=original, plc_model=_PROFILE_CPU['fx3u'])
    assert actual == bytes.fromhex(target['body_hex'])[:-3]
    groups = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/native-fx-parameters-results.json').read_text('utf-8'))
    rows = next(group['rows'] for group in groups if group['cpu'] == 520)
    before = next(row for row in rows if row['text'] == 'OUT C200 K32768')
    target = next(row for row in rows if row['text'] == 'DANDP K1 D0 D2')
    original = instruction(base64.b64decode(before['result']['output_base64'])[:-3])
    integer = instruction(base64.b64decode(target['result']['output_base64'])[:-3]).operands[0]
    actual = encode_instruction('OUT', ['C200', {'text': 'K1', 'token_codes': ['e9']}],
        profile='fx3u', text_encoding=None, source_instruction=original, plc_model=_PROFILE_CPU['fx3u'])
    assert actual == original.tokens[0].raw + b''.join(token.raw for token in original.operands[0].tokens + integer.tokens)
    assert instruction(actual).operands[1].storage_form() == {'text': 'K1', 'token_codes': ['e9']}


def test_structured_q_macro_literal_selection_is_not_replaced_by_the_default_narrow_or_decimal_form():
    import base64
    groups = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/native-q-macro-holdout-results.json').read_text('utf-8'))
    cpus = {209: 'Q03UDV', 34: 'Q02/Q02H', 48: 'Q00J', 65: 'Q02PH', 112: 'Q03UD', 161: 'L02/L02-P'}
    for group in groups:
        end = 4 if group['cpu'] == 209 else 3
        before = next(row for row in group['rows'] if row['text'] == 'M.NAME E1.25 D0')
        target = next(row for row in group['rows'] if row['text'] == 'M.NAME E1.0+0 D0')
        original = instruction(base64.b64decode(before['result']['output_base64'])[:-end], 'q')
        expected = base64.b64decode(target['result']['output_base64'])[:-end]
        identity = instruction(expected, 'q').operands[0].storage_form()
        identity['text'] = 'E1.0'
        actual = encode_instruction('M.NAME', [identity, 'D0'], profile='q', plc_model=cpus[group['cpu']],
            text_encoding='cp936', source_instruction=original)
        assert actual == expected
        wide = next(row for row in group['rows'] if row['text'] == 'M.NAME K32768 D0')
        source = instruction(base64.b64decode(wide['result']['output_base64'])[:-end], 'q')
        changed = encode_instruction('M.NAME', [{'text': 'K1', 'token_codes': ['e9']}, 'D0'], profile='q',
            plc_model=cpus[group['cpu']], text_encoding='cp936', source_instruction=source)
        assert instruction(changed, 'q').operands[0].storage_form() == {'text': 'K1', 'token_codes': ['e9']}
        assert changed[:changed[0]] == source.tokens[0].raw


@pytest.mark.parametrize('filename,expected', [
    ('native-q-remaining-results.json', (90, 90, 90, 90, 90, 90)),
    ('native-q-macro-holdout-results.json', (96, 88, 88, 88, 92, 92)),
    ('native-q-macro-arity-results.json', (14, 14, 14, 14, 14, 14)),
])
def test_q_macro_source_forms_match_independent_native_cpu_controls(filename, expected):
    import base64
    from gxw.token_q import q_instruction_requirements
    groups = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/' + filename).read_text('utf-8'))
    cpus = {209: 'Q03UDV', 34: 'Q02/Q02H', 48: 'Q00J', 65: 'Q02PH', 112: 'Q03UD', 161: 'L02/L02-P'}
    matched = []
    for group in groups:
        count = 0
        for case in group['rows']:
            if not re.match(r'[MU]P?\.', case['text']) or case['result']['return_code'] != '0x00000000':
                continue
            op, *args = re.findall(r'"[^"]*"|[^ ]+', case['text'])
            if op.startswith(('U.', 'UP.')) and not args:
                # The vendor constructor returns a U header declaring one
                # operand but supplies none. Keep this malformed constructor
                # result out of the source-format positive cases.
                with pytest.raises(GXWFormatError, match='arity'):
                    encode_instruction(op, args, profile='q', plc_model=cpus[group['cpu']], text_encoding='cp936')
                continue
            end_bytes = 4 if group['cpu'] == 209 else 3
            raw = base64.b64decode(case['result']['output_base64'])[:-end_bytes]
            actual = encode_instruction(op, args, profile='q', plc_model=cpus[group['cpu']], text_encoding='cp936')
            assert actual == raw, (group['cpu'], case['text'])
            decoded = instruction(actual, 'q')
            assert decoded.step_width == 1
            requirements = q_instruction_requirements(decoded.tokens[0].raw, profile='q')
            assert requirements['requires_named_call_binding']
            assert requirements['native_compile'] == 'not_run'
            count += 1
        matched.append(count)
    assert tuple(matched) == expected


def test_q_macro_prefix_identities_construct_and_edit_without_guessing_from_display():
    groups = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/native-q-macro-prefix-results.json').read_text('utf-8'))
    count = 0
    for group in groups:
        cpu = 'Q03UDV' if group['cpu'] == 209 else 'Q02/Q02H'
        for case in group['rows']:
            if case['mode'] != 'decode':
                assert case['result']['return_code'] != '0x00000000'
                continue
            assert case['result']['return_code'] == '0x00000000'
            op = 'M.NAME' if case['family'] == 0x78 else 'U.NAME'
            codes = [f'{case["prefix"]:02x}', 'a8']
            first = {'text': f'.{case["value"]:X}\\D0', 'token_codes': codes}
            original = bytes.fromhex(case['raw'])[:-4]
            actual = encode_instruction(op, [first, 'D2'], profile='q', plc_model=cpu, text_encoding='cp936')
            assert actual == original
            source = instruction(original, 'q')
            changed = encode_instruction(op, [{'text': '.101\\D0', 'token_codes': codes}, 'D2'],
                profile='q', plc_model=cpu, text_encoding='cp936', source_instruction=source)
            assert changed[:original[0]] == original[:original[0]]
            decoded = instruction(changed, 'q')
            assert decoded.operands[0].storage_form() == {'text': '.101\\D0', 'token_codes': codes}
            assert decoded.step_width == source.step_width == 1
            count += 1
    assert count == 48


def test_q_extended_prefix_widths_match_the_independent_native_source_dispatcher():
    import base64
    groups = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/native-q-prefix-width-results.json').read_text('utf-8'))
    cpus = {209: 'Q03UDV', 34: 'Q02/Q02H', 48: 'Q00J', 65: 'Q02PH', 112: 'Q03UD', 161: 'L02/L02-P'}
    devices = {0xa8: 'D0', 0xb4: 'W0', 0xab: 'G0', 0xa0: 'B0'}
    accepted = refused = 0
    for group in groups:
        for case in group['rows']:
            if 'family' not in case:
                op, *args = case['text'].split()
            else:
                kind = case['family']
                op = kind + '.NAME' if kind in ('M', 'U') else kind
                args = [{'text': f'.{case["value"]:X}\\{devices[case["base"]]}',
                         'token_codes': [f'{case["prefix"]:02x}', f'{case["base"]:02x}']}, 'D2']
                if kind == '+':
                    args.append('D4')
            if case['result']['return_code'] != '0x00000000':
                # The observed ordinary MOV/+ parameter forms fail the
                # native source-width stage; formatter acceptance did not
                # establish these as constructible ordinary source operands.
                assert case['family'] in ('MOV', '+')
                with pytest.raises(GXWFormatError):
                    encode_instruction(op, args, profile='q', plc_model=cpus[group['cpu']], text_encoding='cp936')
                refused += 1
                continue
            actual = encode_instruction(op, args, profile='q', plc_model=cpus[group['cpu']], text_encoding='cp936')
            assert actual == base64.b64decode(case['result']['output_base64'])
            accepted += 1
    assert (accepted, refused) == (732, 720)


def test_q_named_label_first_arguments_and_ff_prefixes_match_independent_native_source_controls():
    import base64
    from gxw.token_q import q_instruction_requirements
    groups = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/native-q-named-label-results.json').read_text('utf-8'))
    cpus = {209: 'Q03UDV', 34: 'Q02/Q02H', 48: 'Q00J', 65: 'Q02PH', 112: 'Q03UD', 161: 'L02/L02-P'}
    matched = 0
    for group in groups:
        for case in group['rows']:
            assert case['result']['return_code'] == '0x00000000'
            if case['mode'] == 'encode':
                op, *args = case['text'].split()
                expected = base64.b64decode(case['result']['output_base64'])[:-(4 if group['cpu'] == 209 else 3)]
            else:
                op = ('S' if case['family'] == 0x70 else 'Z') + ('P' if case['pulse'] == 2 else '') + '.USER'
                args = [{'text': f'.{case["value"]:X}\\W0', 'token_codes': ['ff', 'b4']}, 'D2']
                expected = base64.b64decode(case['result']['output_base64'])
            actual = encode_instruction(op, args, profile='q', plc_model=cpus[group['cpu']], text_encoding='cp936')
            assert actual == expected
            source = instruction(actual, 'q')
            requirement = q_instruction_requirements(source.tokens[0].raw, profile='q',
                operand_tokens=[token.raw for operand in source.operands for token in operand.tokens])
            assert requirement['requires_named_call_binding']
            if case['mode'] == 'encode':
                assert requirement['requires_label_binding']
            changed = tuple(operand.storage_form() for operand in source.operands)
            changed = ({**changed[0], 'text': changed[0]['text'].replace('Target', 'LongerTarget')}, *changed[1:])
            result = encode_instruction(op, changed, profile='q', plc_model=cpus[group['cpu']], text_encoding='cp936',
                                        source_instruction=source)
            assert result[:actual[0]] == actual[:actual[0]]
            matched += 1
    assert matched == 192


_INDEXED_LITERAL_GROUPS = json.loads(private_path(
    'research/experiments/offline-corpus-ladder-20261021/native-q-indexed-literals-results.json'
).read_text('utf-8'))
_INDEXED_LITERAL_CPUS = {209: 'Q03UDV', 34: 'Q02/Q02H', 48: 'Q00J',
                         65: 'Q02PH', 112: 'Q03UD', 161: 'L02/L02-P'}


@pytest.mark.parametrize('group,accepted', list(zip(_INDEXED_LITERAL_GROUPS, (313, 185, 185, 185, 185, 185))),
                         ids=lambda value: str(value['cpu']) if isinstance(value, dict) else str(value))
def test_q_indexed_integer_literals_match_independent_native_cpu_and_parameter_roles(group, accepted):
    import base64
    matched = 0
    for case in group['rows']:
        op, *args = case['text'].split()
        if case['result']['return_code'] != '0x00000000':
            with pytest.raises(GXWFormatError):
                encode_instruction(op, args, profile='q', plc_model=_INDEXED_LITERAL_CPUS[group['cpu']],
                                   text_encoding='cp936')
            continue
        expected = base64.b64decode(case['result']['output_base64'])[:-(4 if group['cpu'] == 209 else 3)]
        actual = encode_instruction(op, args, profile='q', plc_model=_INDEXED_LITERAL_CPUS[group['cpu']],
                                    text_encoding='cp936')
        assert actual == expected
        decoded = instruction(expected, 'q')
        forms = [operand.storage_form() for operand in decoded.operands]
        assert encode_instruction(op, forms, profile='q', plc_model=_INDEXED_LITERAL_CPUS[group['cpu']],
                                  text_encoding='cp936') == expected
        assert all([token.raw[1] for token in operand.tokens][0] == 0xf0
                   for operand in decoded.operands if re.fullmatch(r'[KH].*Z[0-9]+', operand.text))
        matched += 1
    assert matched == accepted


@settings(max_examples=65, deadline=None, derandomize=True)
@given(group_index=st.integers(0, 5), case_index=st.integers(0, 336))
def test_q_indexed_literal_replacements_retain_record_identity_and_recompute_width_from_native_pairs(group_index, case_index):
    import base64
    group = _INDEXED_LITERAL_GROUPS[group_index]
    positives = [case for case in group['rows'] if case['result']['return_code'] == '0x00000000']
    target = positives[case_index % len(positives)]
    op = target['text'].split()[0]
    original = next(case for case in positives if case['text'].split()[0] == op)
    end = 4 if group['cpu'] == 209 else 3
    before = instruction(base64.b64decode(original['result']['output_base64'])[:-end], 'q')
    expected = base64.b64decode(target['result']['output_base64'])[:-end]
    after = instruction(expected, 'q')
    forms = [operand.storage_form() for operand in after.operands]
    assert encode_instruction(op, forms, profile='q', plc_model=_INDEXED_LITERAL_CPUS[group['cpu']],
                              text_encoding='cp936', source_instruction=before) == expected


@pytest.mark.parametrize('cpu,op,form', [
    ('Q02/Q02H', 'DMOV', {'text': 'K1Z0', 'token_codes': ['f0', 'e9']}),
    ('Q02/Q02H', 'DMOV', {'text': 'K1Z0', 'token_codes': ['f0', 'e8']}),
    ('Q03UDV', 'MOV', {'text': 'K1Z0', 'token_codes': ['f0', 'e9']}),
    ('Q03UDV', 'S.MYFUNC', {'text': 'K1Z0', 'token_codes': ['f0', 'e8']}),
    ('L02/L02-P', 'S.MYFUNC', {'text': 'H1Z0', 'token_codes': ['f0', 'eb']}),
    ('Q03UDV', 'MOV', {'text': 'K1Z0', 'token_codes': ['e8', 'f0']}),
])
def test_q_structured_indexed_literals_cannot_bypass_cpu_width_or_component_order(cpu, op, form):
    with pytest.raises(GXWFormatError):
        encode_instruction(op, [form, 'D0'], profile='q', plc_model=cpu, text_encoding='cp936')


def test_q_pchk_and_composite_double_index_match_manual_qualified_native_controls():
    import base64
    from gxw.token_q import q_instruction_requirements
    groups = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/native-q-pchk-zz-results.json').read_text('utf-8'))
    cpus = {209: 'Q03UDV', 34: 'Q02/Q02H', 48: 'Q00J', 65: 'Q02PH', 112: 'Q03UD', 161: 'L02/L02-P'}
    counts = []
    for group in groups:
        matched = 0
        for case in group['rows']:
            if case['result']['return_code'] != '0x00000000':
                continue
            op, *args = re.findall(r'"[^"]*"|[^ ]+', case['text'])
            actual = encode_instruction(op, args, profile='q', plc_model=cpus[group['cpu']], text_encoding='cp936')
            assert actual == base64.b64decode(case['result']['output_base64'])[:-(4 if group['cpu'] == 209 else 3)]
            decoded = instruction(actual, 'q')
            requirement = q_instruction_requirements(decoded.tokens[0].raw, profile='q',
                operand_tokens=[token.raw for operand in decoded.operands for token in operand.tokens])
            assert requirement['requires_double_index_configuration'] is ('ZZ' in case['text'])
            assert requirement['requires_program_file_binding'] is ('PCHK' in op)
            matched += 1
        counts.append(matched)
    assert counts == [25, 24, 0, 24, 0, 0]


@settings(max_examples=45, deadline=None, derandomize=True)
@given(name=st.text(alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ测试', min_size=1, max_size=16),
       operation=st.sampled_from(('LDPCHK', 'ANDPCHK', 'ORPCHK')))
def test_q_pchk_filename_extent_uses_encoded_bytes_and_preserves_neighboring_records(name, operation):
    payload = name.encode('cp936')
    size = len(payload) + 3
    modifier = {'LDPCHK': 0x10, 'ANDPCHK': 0x11, 'ORPCHK': 0x12}[operation]
    expected = bytes((6, 0x41, (size - 2) // 2 + 2, 0, modifier, 6, size, 0xee)) + payload + bytes((size,))
    actual = encode_instruction(operation, [f'"{name}"'], profile='q', plc_model='Q02/Q02H', text_encoding='cp936')
    assert actual == expected
    assert instruction(actual, 'q').args == (f'"{name}"',)


def test_q_unknown_higher_parameter_banks_preserve_native_known_allocation_widths():
    import base64
    from gxw.token_q_encoder import q_storage_context
    groups = json.loads(private_path('research/experiments/offline-corpus-ladder-20261021/native-q-bank-results.json').read_text('utf-8'))
    cpus = {209: 'Q03UDV', 34: 'Q02/Q02H', 112: 'Q03UD', 161: 'L02/L02-P'}
    matched = refused = 0
    baseline = {}
    for group in groups:
        raw = bytes.fromhex(group['parameter_hex'])
        context = q_storage_context(raw, cpu=cpus[group['cpu']])
        assert context.parameter_record == raw and context.status == 'available'
        observed = [case['result'] for case in group['rows']]
        if group['case'] == 'original':
            baseline[group['cpu']] = observed
        else:
            assert observed == baseline[group['cpu']]
        for case in group['rows']:
            if case['result']['return_code'] != '0x00000000':
                assert case['text'] == 'MOV ZR65536 D0'
                refused += 1
                continue
            op, *args = case['text'].split()
            actual = encode_instruction(op, args, profile='q', plc_model=cpus[group['cpu']],
                text_encoding='cp936', storage_context=context)
            assert actual == base64.b64decode(case['result']['output_base64'])[:-(4 if group['cpu'] == 209 else 3)]
            matched += 1
    assert (matched, refused) == (96, 16)


@pytest.mark.parametrize('cpu', ['Q02/Q02H', 'Q03UDV', 'L02/L02-P'])
def test_q_extreme_float_exponents_fail_as_format_errors(cpu):
    with pytest.raises(GXWFormatError, match='range|underflow'):
        encode_instruction('EMOV', ['E1.0-999999999', 'D0'], profile='q', plc_model=cpu, text_encoding=None)


@pytest.mark.parametrize('value', ['E1.0-999999999', 'E1.0+999999999'])
def test_fx_extreme_float_exponents_fail_as_format_errors(value):
    with pytest.raises(GXWFormatError, match='range|underflow|non-finite'):
        encode_instruction('DEMOV', [value, 'D0'], profile='fx3u', text_encoding=None, plc_model=_PROFILE_CPU['fx3u'])


@pytest.mark.parametrize('group', Q_NATIVE['symbolic_operands'], ids=lambda row: row['cpu'])
def test_q_symbolic_source_operands_keep_native_widths_and_require_current_declaration_binding(group):
    from gxw.token_q import q_instruction_requirements
    assert len(group['results']) == 7
    for case in group['results']:
        assert case['return_code'] == '0x00000000'
        op, *args = case['text'].split()
        raw = encode_instruction(op, args, profile='q', plc_model=group['cpu'], text_encoding='cp936')
        assert raw == bytes.fromhex(case['raw'])[:-group['end_bytes']]
        source = instruction(raw, 'q')
        requirements = q_instruction_requirements(source.tokens[0].raw, profile='q',
            operand_tokens=[token.raw for operand in source.operands for token in operand.tokens])
        assert requirements['requires_label_binding'] is True
        assert requirements['native_compile'] == 'not_run' and requirements['execution'] == 'unverified'
        renamed = tuple(arg.replace("'Word", "'LongerWord") for arg in args)
        if renamed != tuple(args):
            edited = encode_instruction(op, renamed, profile='q', plc_model=group['cpu'], text_encoding='cp936',
                                        source_instruction=source)
            assert instruction(edited, 'q').args == renamed
            assert edited[:raw[0]] == raw[:raw[0]]


def test_q_requirements_accept_old_lexical_catalog_without_optional_version_fields(monkeypatch):
    import gxw.token_q as module
    original = module._catalog
    monkeypatch.setattr(module, '_catalog', lambda profile: {key: value for key, value in original(profile).items()
                                                           if key != 'version_dependent_families'})
    assert module.q_instruction_requirements(bytes.fromhex('033403'), profile='l02')['requires_native_version_context'] is False


@pytest.mark.parametrize("op,args,profile", [
    ("MOV", ["K32768", "D0"], "fx3u"),
    ("MOV", ["K-32769", "D0"], "fx3u"),
    ("MOV", ["H10000", "D0"], "fx3u"),
    ("MOV", ["K1", "D65536"], "fx3u"),
    ("LD", ["X8"], "fx3u"),
    ("LD", ["D0Z8"], "fx3u"),
    ("OUT", ["C200", "D0Z1"], "fx3u"),
    ("OUT", ["T10Z0"], "fx3u"),
    ("MOV", ["K1"], "fx3u"),
    ("UNOBSERVED", [], "fx3u"),
    ("ADD", ["D0", "D1", "D2"], "q02"),
    ("DEMOV", ["E1.0-100", "D0"], "fx3u"),
    ("DEMOV", ["E1.0+100", "D0"], "fx3u"),
])
def test_unobserved_ambiguous_and_out_of_range_forms_fail_explicitly(op, args, profile):
    with pytest.raises(GXWFormatError):
        encode_instruction(op, args, profile=profile, text_encoding=None, plc_model=_PROFILE_CPU[profile])


def test_source_fields_and_explicit_project_encoding_are_checked():
    source = instruction("054c05000504e8010404a80004")
    forged = replace(source, mnemonic="DMOV")
    with pytest.raises(GXWFormatError, match="disagree"):
        encode_instruction("MOV", source.args, profile="fx3u", text_encoding=None,
                           source_instruction=forged, plc_model=_PROFILE_CPU['fx3u'])
    with pytest.raises(GXWFormatError, match="encoding"):
        encode_instruction("MOV", source.args, profile="fx3u", text_encoding="not-a-codepage", plc_model=_PROFILE_CPU['fx3u'])
    with pytest.raises(GXWFormatError):
        encode_instruction("MOV", "K1 D0", profile="fx3u", text_encoding=None, plc_model=_PROFILE_CPU['fx3u'])


def test_fx_encoding_needs_the_saved_cpu_instead_of_defaulting_to_fx3u():
    with pytest.raises(GXWFormatError, match="saved CPU identity"):
        encode_instruction('LD', ['M0'], profile='fx3u', text_encoding=None)
    with pytest.raises(GXWFormatError, match="saved CPU identity"):
        encode_instruction('SET', ['S100'], profile='fx3u', text_encoding=None,
                          source_instruction=instruction("03230304900004"))


def test_fx_family_rule_constructs_an_older_menu_target_and_names_its_scope():
    from plc.instruction_steps import instruction_step_width

    width = instruction_step_width('SET', ['S100'], plc_model='FX1N')
    assert (width.steps, width.scope) == (2, "family")
    assert any("FX3U/FX3UC" in item for item in width.evidence)
    assert encode_instruction('SET', ['S100'], profile='fx3u', text_encoding=None,
                              plc_model='FX1N') == bytes.fromhex("0423020404986404")


def test_fx_encoding_rejects_a_cpu_from_another_family():
    with pytest.raises(GXWFormatError, match="differs from the stored CPU family"):
        encode_instruction('LD', ['M0'], profile='fx3u', text_encoding=None, plc_model='Q03UDV')


def test_direct_state_relay_coil_writes_the_two_step_stored_width():
    # JY997D16601-R 7.16: OUT/SET/RST on a direct S target are stored in the wider
    # form. These bytes are corroborated by the native sample, where the same
    # coils land on the export's own steps; a regression here renumbers every
    # later instruction of the program.
    from plc.instruction_steps import instruction_step_width

    for opcode, expected in (("OUT", "0420020404980004"), ("SET", "0423020404980004"),
                             ("RST", "0424020404980004")):
        raw = encode_instruction(opcode, ["S0"], profile="fx3u", text_encoding="cp936",
                                 plc_model="FX3U/FX3UC")
        assert raw.hex() == expected
        assert instruction_step_width(opcode, ["S0"], plc_model="FX3U").steps == 2
    # A direct S contact keeps the one-step form the native export shows.
    assert encode_instruction("LD", ["S0"], profile="fx3u", text_encoding="cp936",
                              plc_model="FX3U/FX3UC").hex() == "03000304980004"


def test_writer_width_is_the_shared_service_answer_for_variable_and_counter_forms():
    from plc.instruction_steps import instruction_step_width

    for opcode, args in (("$MOV", ['"AB"', "D0"]), ("$MOV", ["D0", "D2"]),
                         ("OUT", ["C200", "K1"]), ("OUT", ["C10", "K1"])):
        for model in ("FX3U", "FX3UC"):
            encoded = encode_instruction(opcode, args, profile="fx3u", text_encoding="cp932",
                                         plc_model=model)
            shared = instruction_step_width(opcode, args, plc_model=model,
                                            text_encoding="cp932")
            assert encoded[2] == shared.steps, (opcode, args, model, encoded.hex())


def test_the_storage_context_type_has_one_definition():
    """Core and the writer share one allocator type, not two same-named classes."""
    import pytest

    from gxw import token_q_encoder
    from plc.storage.context import QStorageContext, allocation_state

    assert token_q_encoder.QStorageContext is QStorageContext
    parsed = QStorageContext("Q03UDV", None, ())
    extended = QStorageContext("Q03UDV", b"record", ((0xa8, 0, 2048, 0x4000),))
    unknown = QStorageContext("Q03UDV", b"record", None, reason="unreadable")
    # The four states stay distinct; unknown never becomes the default allocation.
    assert allocation_state(None) == "not_provided"
    assert allocation_state(parsed) == "none"
    assert allocation_state(extended) == "extended"
    assert allocation_state(unknown) == "unavailable"
    assert parsed.has_extended_ranges is False
    assert extended.has_extended_ranges is True
    assert unknown.has_extended_ranges is None


def test_the_allocation_classifier_is_shared_and_encodes_through_the_caller():
    """One implementation, driven by the encoded operand instead of the spelling."""
    from gxw.token_q_encoder import allocation_dependent_operands as writer_view
    from plc.storage import operands_q

    assert operands_q.named_call_family("M.P0") == 0x78
    assert operands_q.named_call_family("D.P1") == 0x74
    # A placeholder family stores one word whatever its arguments name.
    assert writer_view(["D0"], op="M.P0") == ()
    assert writer_view(["D0"], op="U.P0") == ()
    # The hex spelling of one word is one device, and a module prefix is another unit.
    same = writer_view(["WA00", "W0A00"], op="MOV")
    assert same == ("WA00", "W0A00")
    assert writer_view(["K10", "H12", "U0\\G0"], op="MOV") == ()
    # Indirect, indexed and bit-selected D operands still read the local allocation.
    assert writer_view(["@D12000", "D0.5", "D0Z1"], op="MOV") == ("@D12000", "D0.5", "D0Z1")
    # The shared rule takes the encoder from its caller and owns no encoding itself.
    calls = []

    def fake_encode(text, **kwargs):
        calls.append((text, kwargs.get("op")))
        return bytes([0x03, 0xa8, 0x03])

    assert operands_q.allocation_dependent_operands(["D0"], encode=fake_encode, op="MOV") == ("D0",)
    assert calls == [("D0", "MOV")]


def test_the_storage_rules_load_without_the_project_writer():
    """Storage rules are importable when gxw and gxworks2 are unimportable."""
    import subprocess
    import sys
    from pathlib import Path as _Path

    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        if name.split('.')[0] in {'gxw', 'gxworks2'}:\n"
        "            raise ImportError('blocked: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "from plc.storage.context import QStorageContext, allocation_state\n"
        "from plc.storage.operands_q import named_call_family\n"
        "assert allocation_state(QStorageContext('Q03UDV', None, ())) == 'none'\n"
        "assert named_call_family('M.P0') == 0x78\n"
        "print('ok')\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               cwd=str(_Path.cwd()), env={"PYTHONPATH": str(_Path.cwd() / "src"),
                                                          "SYSTEMROOT": "C:\\Windows"})
    assert completed.returncode == 0, completed.stderr
    assert "ok" in completed.stdout


def test_a_framing_failure_is_reported_as_a_format_error_at_the_old_entry():
    """Framing is one of the shared rule failures, so the adapter converts it too."""
    import pytest

    from gxw import token_q_encoder
    from gxw.models import GXWFormatError
    from plc.storage import operands_q, q_encoder
    from plc.storage.errors import StorageFramingError, StorageRuleError

    assert issubclass(StorageFramingError, StorageRuleError)
    assert operands_q.StorageFramingError is StorageFramingError
    assert q_encoder.StorageFramingError is StorageFramingError
    with pytest.raises(StorageFramingError):
        operands_q.encoded_tokens(b"\x04\xa8\x00\x03")
    # The old entry points keep reporting the project format error.
    # Converted at the adapter: the caller only has to know the format error.
    with pytest.raises(GXWFormatError):
        token_q_encoder._tokens(b"\x04\xa8\x00\x03")
    with pytest.raises(GXWFormatError):
        token_q_encoder.encode_q_header("NOPE", ["K1"], (b"\x03\x00\x03",),
                                        profile="q03udv", cpu="Q03UDV")


def test_the_shared_storage_module_exports_only_what_it_owns():
    """The parameter readers stay in the GXW adapter, so they are not exported here."""
    from plc.storage import q_encoder

    for name in q_encoder.__all__:
        assert hasattr(q_encoder, name), name
    assert "q_storage_context" not in q_encoder.__all__
    assert "q_project_storage_context" not in q_encoder.__all__


def test_one_storage_selection_answers_the_width_and_the_header():
    """Core reads steps from the selection; the writer encodes the same selection."""
    import inspect

    from gxw import token_q_encoder
    from plc.instruction_steps import instruction_step_width
    from plc.storage import q_encoder

    forms = [
        ("MOV", ["K10", "D0"], "Q03UDV"),
        ("MOV", ["K10", "D0"], "Q02/Q02H"),
        ("MOV", ["K10", "D0"], "L02/L02-P"),
        ("DMOV", ["D0", "D2"], "Q03UDV"),
        ("MOVP", ["K5", "D10"], "Q03UDV"),
        ("CALL", ["P0"], "Q03UDV"),
        ("END", [], "Q02/Q02H"),
        ("BMOV", ["D0", "D10", "K4"], "Q02/Q02H"),
    ]
    for op, args, cpu in forms:
        profile = q_encoder.storage_profile(cpu)
        encoded = tuple(q_encoder.encode_q_operand(value, op=op, index=index, profile=profile,
                                                   cpu=cpu)
                        for index, value in enumerate(args))
        selection = q_encoder.select_q_storage_form(op, args, encoded, profile=profile,
                                                    cpu=cpu, storage_context=None)
        assert selection.steps == instruction_step_width(
            op, args, plc_model=cpu, assume_default_allocation=True).steps, (op, cpu)
        assert selection.header == q_encoder.encode_q_header(op, args, encoded,
                                                             profile=profile, cpu=cpu), (op, cpu)
        assert selection.family and selection.profile == profile

    # The source-record policy is the adapter's, not the shared rules'.
    assert not hasattr(q_encoder, "q_header_compatible")
    assert hasattr(token_q_encoder, "q_header_compatible")
    assert "source" not in inspect.signature(q_encoder.encode_q_header).parameters
    assert "source" in inspect.signature(token_q_encoder.encode_q_header).parameters
    assert "select_q_storage_form" in q_encoder.__all__
