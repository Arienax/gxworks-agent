"""Native fixture replay and adversarial preservation checks; no PLC/UI access."""
import base64
import csv
import io
import json
from pathlib import Path
import runpy
import struct
import zipfile

import pytest

from gxw.container import CompoundFile
from gxw.container_writer import replace_stream_within_allocation
from gxw.lossless import inspect_project, inspect_program, patch_structured_symbol_equal_size, patch_token_constant, sha256
from gxw.models import GXWFormatError
from gxw.project_metadata import logical_mapping, synchronize_history, read_project_text_context
from gxw.structured_pou import parse_structured_pou
from gxw.structured_pou_writer import serialize_structured_pou
from gxw.token_pou import parse_token_pou, frame_token_pou, parse_token_fragment
from gxw.token_listing import decode_token_listing, decode_token_program, TokenText, TokenLabel, TokenInstruction
from gxw.token_resource import parse_token_resource
from gxw.text_pou import parse_st_pou
from gxw.sfc_pou import parse_sfc_pou
from gxw.token_patch import TokenInstructionPatch, patch_token_instructions, TokenRecordSplice, splice_token_records

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((ROOT / "tests/fixtures/gxw_token_corpus.json").read_text(encoding="utf-8"))["cases"]
HARNESS = runpy.run_path(str(ROOT / "research/gxw_corpus.py"))
NATIVE_SLICES = json.loads((ROOT / "tests/fixtures/gxw_token_native_20260919.json").read_text(encoding="utf-8"))["cases"]
NATIVE_CONVERSION = json.loads((ROOT / "research/results/token-20260919/native-conversion-ladder-mode.json").read_text(encoding="utf-8"))
NATIVE_OPERANDS = json.loads((ROOT / "research/results/token-20260919/native-operand-corpus-20260920.json").read_text(encoding="utf-8"))
NATIVE_RESOURCES = json.loads((ROOT / "research/results/token-20260919/compiled-resource-corpus-20260920.json").read_text(encoding="utf-8"))
SIMPLE_SOURCES = json.loads((ROOT / "tests/fixtures/gxw_simple_ladder_sources.json").read_text(encoding="utf-8"))["cases"]
Q_EVIDENCE = ROOT / "research/evidence/gxw-q-token-grammar-20260927.zip"
Q02_EVIDENCE = ROOT / "research/evidence/gxw-q02-lexical-20260927.zip"
ST_TEXT_CONTROLS = json.loads((ROOT / "tests/fixtures/gxw_st_text_native.json").read_text(encoding="utf-8"))["cases"]
SFC_SOURCE_CONTROLS = json.loads((ROOT / "tests/fixtures/gxw_sfc_source_native.json").read_text(encoding="utf-8"))["cases"]
LIBRARY_TASKS = json.loads((ROOT / "tests/fixtures/gxw_library_tasks_native.json").read_text(encoding="utf-8"))["cases"]


def q_native_records(listing):
    """Transport the Core records into the independent vendor output shape."""
    result = []
    for record in listing.records:
        if isinstance(record, TokenInstruction):
            result.append(dict(kind="instruction", op=record.mnemonic, args=list(record.args)))
        elif isinstance(record, TokenText):
            result.append(dict(kind=record.role, text=record.text))
        elif isinstance(record, TokenLabel):
            result.append(dict(kind="label", text=record.text))
        else:
            result.append(None)
    return result


@pytest.mark.parametrize("case", ST_TEXT_CONTROLS, ids=lambda c: c["id"])
def test_st_source_utf16_is_preserved_independently_of_native_compiler_text(case):
    raw = base64.b64decode(case["program_base64"])
    assert sha256(raw) == case["program_sha256"]
    source = parse_st_pou(raw)
    image = inspect_program(raw, token_profile=None)
    assert image.layout == "structured-text" and image.reconstruct() == source.reconstruct() == raw
    assert source.code_units == case["units"]
    assert source.diagnostic == case["text_gap"]
    native = base64.b64decode(case["frontend_text_base64"])
    report = HARNESS["program_report"](raw, case["program"], [], token_profile=None)
    assert report["source_text"] == source.text and report["semantic_complete"] is False
    assert report["text_gaps"] == int(source.text is None)
    if source.text is None:
        assert image.regions[1].handling == "opaque-preserved" and image.diagnostics
    else:
        assert source.text.encode(case["context"]["text_encoding"]) + b"\0" == native
        assert image.regions[1].handling == "decoded" and not image.diagnostics
    if "saved_program_base64" in case:
        saved = parse_st_pou(base64.b64decode(case["saved_program_base64"]))
        assert saved.text_bytes == source.text_bytes


def test_st_native_success_does_not_hide_truncation_or_replace_original_unicode():
    def case(name):
        return next(c for c in ST_TEXT_CONTROLS if '/' + name + '/' in c["id"])

    nul, clean, unicode = [case(name) for name in ("embedded-nul", "without-nul", "unicode-comment")]
    source = parse_st_pou(base64.b64decode(nul["program_base64"]))
    assert source.text is None and "embedded NUL" in source.diagnostic
    assert "D0:=1;".encode("utf-16le") in source.text_bytes
    assert base64.b64decode(nul["frontend_text_base64"]) == b"Y0:=X0;\r\n\0"
    assert nul["compiled_pcode_base64"] == unicode["compiled_pcode_base64"]
    assert nul["compiled_pcode_base64"] != clean["compiled_pcode_base64"]
    astral = parse_st_pou(base64.b64decode(unicode["program_base64"]))
    assert "\U0001f680" in astral.text and astral.code_units == len(astral.text) + 2


@pytest.mark.parametrize("offset,replacement", [(54, b"\xc0"), (55, b"\xff"), (59, b"\xff"),
    (63, b"\x02"), (67, b"\xff\xff\xff\xff"), (-30, b"\x01"), (-1, b"\x01")])
def test_st_source_envelope_failure_preserves_the_whole_stream(offset, replacement):
    raw = bytearray(base64.b64decode(ST_TEXT_CONTROLS[0]["program_base64"]))
    start = offset if offset >= 0 else len(raw) + offset
    raw[start:start + len(replacement)] = replacement
    with pytest.raises(GXWFormatError):
        parse_st_pou(raw)
    image = inspect_program(raw, token_profile=None)
    assert image.layout == "unsupported" and image.reconstruct() == raw


@pytest.mark.parametrize("description,source_flag,trailer_flag", [("", 0, 1), ("A description", 1, 0), ("\U0001f680 description", 0, 1)])
def test_st_description_is_counted_in_code_units_and_flags_remain_raw(description, source_flag, trailer_flag):
    from gxw.declarations import _string

    original = base64.b64decode(ST_TEXT_CONTROLS[0]["program_base64"])
    text = parse_st_pou(original).text_bytes
    header_text = _string(description)
    raw = bytearray(original[:6] + header_text + original[12:])
    start = 54 + len(header_text)-6
    struct.pack_into("<I",raw,start+9,source_flag)
    struct.pack_into("<I",raw,len(raw)-4,trailer_flag)
    parsed = parse_st_pou(raw)
    image = inspect_program(raw,token_profile=None)
    assert parsed.text_offset == start+17 and parsed.text_bytes == text
    assert image.layout == "structured-text" and image.reconstruct() == bytes(raw)
    struct.pack_into("<I",raw,6,0xffffffff)
    with pytest.raises(GXWFormatError):
        parse_st_pou(raw)
    assert inspect_program(raw,token_profile=None).reconstruct() == raw


@pytest.mark.parametrize("case", LIBRARY_TASKS, ids=lambda c: c["task"])
def test_library_task_records_match_independent_native_frontend(case):
    reader = runpy.run_path(str(ROOT / "research/probe_gxw_task_graph.py"))["task_records"]
    raw = base64.b64decode(case["task_base64"])
    parsed = reader(raw)
    assert parsed["configuration_hex"] == case["native_configuration_hex"]
    assert parsed["uninterpreted_texts"] == case["native_texts"]
    assert [{k:r[k] for k in ("program_reference", "stored_order")} for r in parsed["entries"]] == case["native_entries"]
    assert bytes.fromhex(parsed["opaque_prefix_hex"]) + b"".join(bytes.fromhex(e["raw_hex"]) for e in parsed["entries"]) == raw
    # The config text FALSE is retained; it is not a proof that this task's
    # source is omitted from compilation or can safely be removed.
    assert all(e["secondary_text_role"] == "unknown" for e in parsed["entries"])


def test_native_program_check_cache_counterexample_is_kept_as_a_false_acceptance():
    evidence = json.loads((ROOT / "research/results/program-check-freshness-20260928.json").read_text(encoding="utf-8"))
    legacy, fresh, default = evidence["negative_controls"]
    assert len({c["input_sha256"] for c in (legacy, fresh, default)}) == 1
    assert len({c["pcode_sha256"] for c in (legacy, fresh, default)}) == 1
    assert all(sha256(base64.b64decode(c["pcode_base64"])) == c["pcode_sha256"] for c in (legacy, fresh, default))
    assert legacy["program_check_completed"] and not legacy["compiler_rejected"] and legacy["exported"]
    for control in (fresh, default):
        assert control["refresh_program_check"] and control["program_check_completed"] and control["compiler_rejected"]
        assert not control["exported"]
        assert any(r["code"] == 84710144 and r["arguments"] == ["Y000"] for r in control["native_errors"])


@pytest.mark.parametrize("cpu,profile,encoded", [(520, "fx3u", True), (518, "fx1s", False)])
def test_fx_modbus_spelling_is_not_a_cpu_instruction_availability_claim(cpu, profile, encoded):
    evidence = json.loads((ROOT / "research/results/token-20260919/native-fx-cpu-lexical-20260927.json").read_text(encoding="utf-8"))
    control = next(c for c in evidence["additional_controls"] if c["cpu"] == cpu)
    raw = bytes.fromhex(control["body_hex"])
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile=profile)
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))["native_il_projection"]
    assert q_native_records(listing) == oracle(base64.b64decode(control["native"][0]["output_base64"]), encoding="cp1252")["records"]
    assert [(i.mnemonic, i.args) for i in listing.instructions] == [("ADPRW", ("H1", "H1", "H0", "H1", "D0"))]
    assert listing.reconstruct() == raw
    assert (control["native"][1]["return_code"] == "0x00000000") == encoded


@pytest.mark.parametrize("case", SFC_SOURCE_CONTROLS, ids=lambda c: c["program_sha256"][:12])
def test_sfc_source_children_and_registrations_match_native_ownership(case):
    raw = base64.b64decode(case["program_base64"])
    assert sha256(raw) == case["program_sha256"]
    source = parse_sfc_pou(raw)
    children = [{k: c[k] for k in ("kind", "name", "number", "token_hex")} for c in source.layout["children"]]
    actions = [{k: a[k] for k in ("name", "number", "registrations")} for a in source.layout["actions"]]
    canonical = lambda rows: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
    assert canonical(children) == canonical(case["native_children"])
    assert canonical(actions) == canonical(case["native_actions"])
    image = inspect_program(raw, token_profile=case["context"]["token_profile"], text_encoding=case["context"]["text_encoding"])
    assert image.layout == "sfc-source" and image.reconstruct() == source.reconstruct() == raw
    assert list(source.diagnostics) == case["reference_gaps"]
    assert any(r.kind in ("sfc-graph", "sfc-graph-cache") and r.handling == "opaque-preserved" for r in image.regions)


@pytest.mark.parametrize("change", ["cache", "graph-opcode", "child-size"])
def test_sfc_source_unknown_graph_and_malformed_children_remain_raw(change):
    raw = bytearray(next(base64.b64decode(c["program_base64"]) for c in SFC_SOURCE_CONTROLS
                         if parse_sfc_pou(base64.b64decode(c["program_base64"])).graph_tokens is not None))
    before = parse_sfc_pou(raw)
    graph = before.layout["graph"]
    if change == "cache":
        offset = graph["cache_offset"] + graph["cache_size"] - 1
        raw[offset] ^= 0xff
    elif change == "graph-opcode":
        raw[graph["code_offset"] + 1] = 0x7f
    else:
        struct.pack_into("<I", raw, before.layout["children"][0]["size_offset"], 0xffffffff)
    image = inspect_program(raw, token_profile="fx3u")
    assert image.reconstruct() == raw
    if change == "child-size":
        assert image.layout == "unsupported"
    else:
        assert image.layout == "sfc-source"
        assert image.projection.layout["children"] == before.layout["children"]
        if change == "graph-opcode":
            assert image.projection.graph_records[0]["kind"] == "opaque" and image.diagnostics
        else:
            region, = [r for r in image.regions if r.kind == "sfc-graph-cache"]
            assert region.handling == "opaque-preserved"
            assert region.raw[-1] == raw[offset]


def test_q_real_source_corpus_matches_independent_native_text_and_preserves_every_byte():
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))["native_il_projection"]
    with zipfile.ZipFile(Q_EVIDENCE) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for member in manifest["files"]:
            assert sha256(archive.read(member["path"])) == member["sha256"]
        count = 0
        for case in manifest["programs"]:
            metadata = archive.read(case["native_prefix"].removesuffix("/native") + "/metadata.prj")
            context = read_project_text_context(metadata)
            assert all(case["context"][k] == v for k, v in context.items())
            raw = archive.read(case["source"])
            assert sha256(raw) == case["source_sha256"]
            requests = json.loads(archive.read(case["native_prefix"] + "/requests.json"))["requests"]
            answers = [json.loads(s) for s in archive.read(case["native_prefix"] + "/native-stdout.jsonl").splitlines()]
            request, answer = requests[case["request_index"]], answers[case["request_index"]]
            listing = decode_token_listing(raw, profile="q03udv", text_encoding=case["context"]["text_encoding"])
            assert listing.profile == "q03udv" and not listing.gaps
            assert listing.source.body + b"\0" == base64.b64decode(request["input_base64"])
            assert answer["return_code"] == "0x00000000" and answer["consumed_bytes"] == len(listing.source.body)
            native = oracle(base64.b64decode(answer["output_base64"]), encoding=case["context"]["text_encoding"])
            assert not native["text_gaps"] and q_native_records(listing) == native["records"]
            assert listing.reconstruct() == raw
            assert reverse_q_boundaries(raw, listing.source) == [(t.offset, t.raw) for t in listing.source.tokens]
            image = inspect_program(raw, token_profile="q03udv", text_encoding=case["context"]["text_encoding"])
            assert image.layout == "ladder-token-q03udv" and not image.diagnostics
            assert image.reconstruct() == raw and image.projection == listing
            report = HARNESS["program_report"](raw, case["logical_name"], [],
                                               token_profile="q03udv", text_encoding=context["text_encoding"])
            assert report["instruction_gaps"] == report["critical_token_gaps"] == report["undecoded_texts"] == 0
            assert report["decoded_instructions"] == len(listing.instructions)
            assert report["framing_cross_check"] == "agrees"
            opaque = inspect_program(raw, token_profile=None)
            assert opaque.layout == "ladder-framed" and opaque.reconstruct() == raw
            assert all(r.handling == "opaque-preserved" for r in opaque.regions)
            # This profile cannot inherit the FX-only CSV model identity.
            with pytest.raises(GXWFormatError, match="CSV.*FX"):
                listing.csv_bytes()
            count += len(listing.records)
        assert manifest["summary"] == dict(programs=75, projects=35, records=7364)
        assert count == 7364


def reverse_q_boundaries(raw, source):
    return HARNESS["reverse_token_boundaries"](raw, body_end=source.body_end)


@pytest.mark.parametrize("control,accepted,rejected", [
    ("q-header-catalog-controls", 2618, 30), ("q-operand-catalog-controls", 354, 0),
])
def test_q_lexical_controls_replay_native_successes_and_version_dependent_failures(control, accepted, rejected):
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))["native_il_projection"]
    with zipfile.ZipFile(Q_EVIDENCE) as archive:
        prefix = "controls/" + control + "/native-decode/"
        requests = json.loads(archive.read(prefix + "requests.json"))["requests"]
        answers = [json.loads(s) for s in archive.read(prefix + "native-stdout.jsonl").splitlines()]
        counts = [0, 0]
        for request, answer in zip(requests, answers, strict=True):
            raw = base64.b64decode(request["input_base64"])[:-1]
            listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile="q03udv", text_encoding="cp949")
            assert listing.reconstruct() == raw
            if answer["return_code"] == "0x00000000":
                native = oracle(base64.b64decode(answer["output_base64"]), encoding="cp949")
                assert not listing.gaps and not native["text_gaps"]
                assert q_native_records(listing) == native["records"]
                counts[0] += 1
            else:
                assert answer["return_code"] == "0x04021003"
                assert len(listing.gaps) == 1 and "version context" in listing.gaps[0].reason
                with pytest.raises(GXWFormatError):
                    listing.instruction_ir()
                counts[1] += 1
        assert counts == [accepted, rejected]


@pytest.mark.parametrize("body", [
    "05fe01000504a80004",                 # unknown family plus its operand
    "054c02ff0504a8000404a80104",         # descriptor hole, no FX fallback
    "064c0200770604a8000404a80104",       # unknown header modifier
    "054c02000504a80004",                 # MOV with too few operands
    "054c02000507ec000000800704a80004",   # native Q rejects decimal negative zero
    "054c02000504f0000404f0010404a8000404a80104",  # duplicate index
    "054c02000504f00004",                 # trailing modifier
    "054c00000504a8000404a80104",         # zero stored width
    "054c80000504a8000404a80104",         # negative signed stored width
    "033c0304a80004",                     # label with a non-pointer operand
    "054c02000504df810404a80004",         # undecodable cp949 label
    "054c02000506ee4100420604a80004",     # embedded NUL string
])
def test_q_unknown_records_keep_their_operands_and_do_not_consume_the_next_header(body):
    raw = bytes.fromhex(body + "033403")
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile="q03udv", text_encoding="cp949")
    assert len(listing.gaps) == 1 and listing.reconstruct() == raw
    assert b"".join(t.raw for t in listing.gaps[0].tokens) == bytes.fromhex(body)
    assert len(listing.instructions) == 1 and listing.instructions[0].mnemonic == "END"
    assert listing.instructions[0].step is None
    with pytest.raises(GXWFormatError):
        listing.instruction_ir()


def test_q_profile_is_explicit_and_text_never_uses_the_host_codepage():
    raw = bytes.fromhex("030003049c1004")
    frame = parse_token_fragment(raw, 0, len(raw))
    assert decode_token_program(frame).instructions[0].args == ("X20",)
    assert decode_token_program(frame, profile="q03udv").instructions[0].args == ("X10",)
    with pytest.raises(GXWFormatError, match="unsupported token profile"):
        decode_token_program(frame, profile="Q")
    with pytest.raises(GXWFormatError, match="unsupported token profile"):
        inspect_program(raw, token_profile="Q")
    text = "제어".encode("cp949")
    text_token = bytes([len(text) + 4, 0x80, 0]) + text + bytes([len(text) + 4])
    label = bytes([len(text) + 3, 0xdf]) + text + bytes([len(text) + 3])
    body = text_token + bytes.fromhex("030003") + label + bytes.fromhex("033403")
    frame = parse_token_fragment(body, 0, len(body))
    unknown = decode_token_program(frame, profile="q03udv")
    assert unknown.records[0].text is None and len(unknown.gaps) == 1
    assert unknown.reconstruct() == body
    decoded = decode_token_program(frame, profile="q03udv", text_encoding="cp949")
    assert decoded.records[0].text == "제어" and decoded.instructions[0].args == ("'제어",)
    assert not decoded.gaps and decoded.reconstruct() == body


@pytest.mark.parametrize("control,profile,accepted,rejected", [
    ("q02-lexical-controls/native", "q02", 2597, 3),
    ("q-float-bitpattern-holdouts/native", "q02", 5606, 3004),
    ("q-float-bitpattern-holdouts/native-q03udv", "q03udv", 5606, 3004),
])
def test_q02_cpu_rules_and_float_display_match_independent_native_controls(control, profile, accepted, rejected):
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))["native_il_projection"]
    with zipfile.ZipFile(Q02_EVIDENCE) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        prefix = "controls/" + control + "/"
        for member in manifest["files"]:
            if member["path"].startswith(prefix):
                assert sha256(archive.read(member["path"])) == member["sha256"]
        requests = json.loads(archive.read(prefix + "requests.json"))["requests"]
        answers = [json.loads(line) for line in archive.read(prefix + "native-stdout.jsonl").splitlines()]
        counts = [0, 0]
        for request, answer in zip(requests, answers, strict=True):
            raw = base64.b64decode(request["input_base64"])[:-1]
            listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile=profile, text_encoding="cp949")
            assert listing.reconstruct() == raw and listing.profile == profile
            if answer["return_code"] == "0x00000000":
                assert answer["consumed_bytes"] == len(raw)
                native = oracle(base64.b64decode(answer["output_base64"]), encoding="cp949")
                assert not listing.gaps and q_native_records(listing) == native["records"]
                counts[0] += 1
            else:
                assert listing.gaps
                counts[1] += 1
        assert counts == [accepted, rejected]


def test_q02_descriptor_catalog_matches_native_queries_and_never_uses_q03_holes():
    catalog = json.loads((ROOT / "src/gxw/templates/q02-token-grammar.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(Q02_EVIDENCE) as archive:
        rows = [json.loads(line) for line in archive.read("descriptors/34/native-stdout.jsonl").splitlines()]
    assert len(rows) == 31110
    assert catalog["headers"] == {f"{r['family']:02x}:{r['variant']:02x}": [r["name"], bytes.fromhex(r["descriptor_hex"])[4]]
                                  for r in rows if r["result"] == "0x00000000"}
    for body, profile, mnemonic in [("054101000504a80004", "q02", "PCHK"),
                                    ("06400218ff0604a8000404a80104", "q03udv", "ED=")]:
        frame = parse_token_fragment(bytes.fromhex(body), 0, len(bytes.fromhex(body)))
        accepted = decode_token_program(frame, profile=profile)
        rejected = decode_token_program(frame, profile="q03udv" if profile == "q02" else "q02")
        assert not accepted.gaps and accepted.instructions[0].mnemonic == mnemonic
        assert len(rejected.gaps) == 1 and rejected.reconstruct() == frame.reconstruct()


def test_q_float_native_display_does_not_replace_raw_value_or_negative_zero():
    from gxw.token_q import q_operand_text

    for code, value, displayed in [(0xec, 1234500.5, "E1234501"), (0xed, -0.0, "E0.0")]:
        raw = bytes([7, code]) + struct.pack("<f", value) + b"\x07"
        assert q_operand_text(raw, text_encoding=None) == displayed
        body = bytes.fromhex("054c030205") + raw + bytes.fromhex("04a80004")
        listing = decode_token_program(parse_token_fragment(body, 0, len(body)), profile="q02")
        assert listing.instructions[0].args[0] == displayed
        assert listing.instructions[0].operands[0].tokens[0].raw == raw
        assert listing.reconstruct() == body


@pytest.mark.parametrize("change", ["length", "terminator", "utf16", "prefix", "truncated"])
def test_project_text_context_rejects_broken_metadata_without_guessing_a_cpu(change):
    with zipfile.ZipFile(Q_EVIDENCE) as archive:
        case = json.loads(archive.read("manifest.json"))["programs"][0]
        raw = bytearray(archive.read(case["native_prefix"].removesuffix("/native") + "/metadata.prj"))
    context = read_project_text_context(raw)
    if change == "length":
        struct.pack_into("<I", raw, 0, 0xffffffff)
    elif change == "terminator":
        raw[4 + 2 * struct.unpack_from("<I", raw)[0] - 2] = 65
    elif change == "utf16":
        raw[4:6] = b"\0\xd8"
    elif change == "prefix":
        raw[context["codepage_offset"] - 48] = 2
    else:
        raw = raw[:context["codepage_offset"] + 3]
    with pytest.raises(GXWFormatError):
        read_project_text_context(raw)


def test_project_context_keeps_unknown_codepages_and_cpus_explicit():
    from types import SimpleNamespace

    with zipfile.ZipFile(Q_EVIDENCE) as archive:
        case = json.loads(archive.read("manifest.json"))["programs"][0]
        raw = bytearray(archive.read(case["native_prefix"].removesuffix("/native") + "/metadata.prj"))
    offset = read_project_text_context(raw)["codepage_offset"]
    struct.pack_into("<I", raw, offset, 65001)
    stream = SimpleNamespace(layer="nested", raw=bytes(raw), logical_name="Project.prj")
    image = SimpleNamespace(streams=[stream])
    context = HARNESS["project_token_context"](image)
    assert context["token_profile"] == "q03udv" and context["text_encoding"] is None
    at = bytes(raw).index("Q03UDV".encode("utf-16le"))
    raw[at:at + 12] = "Q00XYZ".encode("utf-16le")
    stream.raw = bytes(raw)
    assert HARNESS["project_token_context"](image)["token_profile"] is None
    image.streams.append(stream)
    assert HARNESS["project_token_context"](image)["token_profile"] is None


def test_q_project_scan_and_cli_select_the_source_cpu_and_encoding(tmp_path):
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-callsite-inputs-20260927.zip") as archive:
        source = archive.read("research/experiments/sfc-graph-20260926/public-corpus-discovery/samples/scpi-SCPI_FB.gxw")
    report = HARNESS["analyze"](source)
    assert report["token_context"]["token_profile"] == "q03udv"
    assert report["token_context"]["text_encoding"] == "cp932"
    programs = [s["program"] for s in report["streams"] if "program" in s]
    assert len(programs) == 2 and all(p["layout"] == "ladder-token-q03udv" for p in programs)
    assert report["coverage"]["instruction_gaps"] == report["coverage"]["critical_token_gaps"] == 0
    assert report["coverage"]["decoded_instructions"] > 100
    path, output = tmp_path / "source.gxw", tmp_path / "listing.json"
    path.write_bytes(source)
    HARNESS["main"](["decode-tokens", str(path), "--program", "SCPI.Program.pou", "-o", str(output)])
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["token_profile"] == "q03udv" and result["text_encoding"] == "cp932"
    assert result["instruction_gaps"] == 0 and len(result["records"]) == 163
    assert any(r.get("op") == "G.INPUT" for r in result["records"])
    assert path.read_bytes() == source


@pytest.mark.parametrize("tail_bytes", [0, 110, 111, 112, 511, 512])
def test_native_nested_container_requires_payload_but_not_unused_sector_tail(tail_bytes):
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-partial-sector-20260927.zip") as archive:
        source = archive.read("native/native-saved.gxw")
        expected = archive.read("native/compiler_DZComp/CGTable.dat")
        manifest = json.loads(archive.read("manifest.json"))
    outer = CompoundFile(source)
    nested = outer.read_stream("_hdb")
    physical = logical_mapping(outer.read_stream("projectdatalist.xml"))["CGTable.dat"]
    assert len(nested) % 512 == 111
    assert sha256(expected) == manifest["observation"]["cg_sha256"]
    final_sector = nested[-111:] + bytes(512 - 111)
    candidate = nested[:-111] + final_sector[:tail_bytes]
    if tail_bytes < 111:
        with pytest.raises(GXWFormatError, match="outside the file"):
            CompoundFile(candidate).read_stream(physical)
    else:
        assert CompoundFile(candidate).read_stream(physical) == expected
        image = inspect_project(source)
        assert not image.diagnostics and not any(s.error for s in image.streams)
        assert image.reconstruct() == source
        assert next(s.raw for s in image.streams if s.logical_name == "CGTable.dat") == expected


def token_envelope(body):
    # Synthetic wrapper around untouched native token slices, not native proof.
    prefix = bytearray(79)
    struct.pack_into("<II", prefix, 55, len(body) + 20, len(body) + 20)
    return bytes(prefix) + body + bytes(24)


def splice_fixture(key):
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-record-splice-20260920.zip") as z:
        folder = "native" if key == "c" else "u-native"
        report = json.loads(z.read(f"splice/{folder}/patch.json"))
        edits = tuple(TokenRecordSplice(e["old_offset"], bytes.fromhex(e["old_raw_hex"]),
                                       bytes.fromhex(e["new_raw_hex"])) for e in report["edits"])
        return (z.read(f"splice/{key}-base.gxw"), edits, z.read(f"splice/{key}-input.gxw"),
                z.read(f"splice/{key}.gxw"), z.read(f"splice/{key}-reopened.csv"))


@pytest.mark.parametrize("key", ["c", "u"])
def test_record_splices_replay_native_add_remove_and_preserve_full_context(key):
    source, edits, expected, saved, native_csv = splice_fixture(key)
    result = splice_token_records(source, expected_sha256=sha256(source), logical_name="MAIN.Program.pou",
                                  edits=edits[::-1], text_encoding="cp936")
    assert result.data == expected
    def streams(raw):
        return {s.logical_name: s.raw for s in inspect_project(raw).streams if s.logical_name and s.raw is not None}
    before, patched, native = map(streams, (source, expected, saved))
    assert patched == dict(before, **{"MAIN.Program.pou": patched["MAIN.Program.pou"]})
    assert patched["MAIN.Program.pou"] == native["MAIN.Program.pou"]
    listing = decode_token_listing(native["MAIN.Program.pou"], text_encoding="cp936")
    align = runpy.run_path(str(ROOT / "research/align_gxw_tokens.py"))
    def rows(data):
        values = list(csv.reader(io.StringIO(data.decode("utf-16")), delimiter="\t"))[3:]
        for row in values:
            row[3] = align["canonical_operand"](row[3])
        return values
    assert rows(listing.csv_bytes()) == rows(native_csv)
    assert parse_token_resource(native["MAIN.res"]).code_regions[0].body == listing.source.body
    assert before["MAIN.res"] == patched["MAIN.res"] != native["MAIN.res"]
    # Reverse all splices using the new coordinates, including pure insertions
    # and deletions: logical POU bytes must return to the original source.
    reverse = tuple(TokenRecordSplice(e["new_offset"], bytes.fromhex(e["new_raw_hex"]),
                                     bytes.fromhex(e["old_raw_hex"])) for e in result.report["edits"])
    restored = splice_token_records(expected, expected_sha256=sha256(expected), logical_name="MAIN.Program.pou",
                                    edits=reverse, text_encoding="cp936")
    assert streams(restored.data) == before


def test_splice_native_opaque_context_proof_still_replays_without_family_recognition(monkeypatch):
    import gxw.token_pou as tokens
    # Replay the historical u experiment with the later family insight disabled.
    # Its 13-byte string header was never added to the exact observation table.
    assert "054c110305" not in tokens._NATIVE_ENCODINGS
    monkeypatch.delitem(tokens._NATIVE_FAMILIES, (5, 0x4C, 3))
    source, edits, expected, _, _ = splice_fixture("u")
    result = splice_token_records(source, expected_sha256=sha256(source), logical_name="MAIN.Program.pou",
                                  edits=edits, text_encoding="cp936")
    assert result.data == expected and result.report["source_gaps_preserved"] == 3


@pytest.mark.parametrize("case", ["hash", "empty", "duplicate", "overlap", "middle", "cut", "raw", "end",
                                  "after_end", "embedded_end", "gap", "operand", "bool_offset", "noop", "text_encoding"])
def test_record_splice_rejects_stale_unbound_overlapping_and_lossy_operations(case):
    source, edits, _, _, _ = splice_fixture("c")
    a, deletion, terminal = edits
    kwargs = dict(expected_sha256=sha256(source), logical_name="MAIN.Program.pou", edits=(a,), text_encoding="cp936")
    if case == "hash": kwargs["expected_sha256"] = "stale"
    elif case == "empty": kwargs["edits"] = ()
    elif case == "duplicate": kwargs["edits"] = (a, a)
    elif case == "overlap":
        # The second operation starts at the next instruction inside a removed span.
        kwargs["edits"] = (deletion, TokenRecordSplice(deletion.offset + 7, b"", a.replacement_raw))
    elif case == "middle": kwargs["edits"] = (TokenRecordSplice(a.offset + 1, b"", a.replacement_raw),)
    elif case == "cut": kwargs["edits"] = (TokenRecordSplice(deletion.offset, deletion.expected_raw[:-1], b""),)
    elif case == "raw": kwargs["edits"] = (TokenRecordSplice(deletion.offset, b"x" * len(deletion.expected_raw), b""),)
    elif case == "end": kwargs["edits"] = (TokenRecordSplice(terminal.offset, bytes.fromhex("033403"), b""),)
    elif case == "after_end": kwargs["edits"] = (TokenRecordSplice(terminal.offset + 3, b"", a.replacement_raw),)
    elif case == "embedded_end": kwargs["edits"] = (TokenRecordSplice(a.offset, b"", bytes.fromhex("033403")),)
    elif case == "gap": kwargs["edits"] = (TokenRecordSplice(a.offset, b"", bytes.fromhex("03fe03")),)
    elif case == "operand": kwargs["edits"] = (TokenRecordSplice(a.offset, b"", bytes.fromhex("049c0004")),)
    elif case == "bool_offset": kwargs["edits"] = (TokenRecordSplice(True, b"", a.replacement_raw),)
    elif case == "noop": kwargs["edits"] = (TokenRecordSplice(a.offset, b"", b""),)
    elif case == "text_encoding":
        kwargs.update(text_encoding=None, edits=(TokenRecordSplice(a.offset, b"", bytes.fromhex("0580004105")),))
    with pytest.raises(GXWFormatError):
        splice_token_records(source, **kwargs)


def test_explicit_opaque_deletion_cannot_rebind_a_neighboring_instruction():
    from gxw.lossless import _replace_token_program
    source = token_container_fixture()
    logical, _, raw = pou(source)
    # Synthetic opaque opcode + free operand; never represented as native-valid.
    mutant = token_envelope(bytes.fromhex("030003049c000403fe03049c0104033403"))
    source = _replace_token_program(source, logical, raw, mutant).data
    kwargs = dict(expected_sha256=sha256(source), logical_name=logical)
    with pytest.raises(GXWFormatError, match="rebound"):
        splice_token_records(source, **kwargs, edits=(TokenRecordSplice(86, bytes.fromhex("03fe03"), b""),))
    result = splice_token_records(source, **kwargs, edits=(TokenRecordSplice(86, bytes.fromhex("03fe03049c0104"), b""),))
    assert result.report["edits"][0]["explicitly_removed_opaque_records"] == 2
    assert decode_token_listing(pou(result.data)[2]).instruction_ir() == [
        {"op": "LD", "args": ["X0"]}, {"op": "END", "args": []}]


def test_native_string_headers_decode_by_identity_without_new_exact_observations():
    from gxw.token_pou import _NATIVE_ENCODINGS
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-record-splice-20260920.zip") as z:
        cases = json.loads(z.read("header-grammar/strings/probes.json"))["cases"]
    novel = set()
    for case in cases:
        body = bytes.fromhex(case["body_hex"])
        listing = decode_token_listing(token_envelope(body), text_encoding="cp936")
        assert not listing.gaps and listing.reconstruct() == token_envelope(body)
        assert [{"kind": "instruction", "op": i.mnemonic, "args": list(i.args)} for i in listing.instructions] == case["native_records"]
        if body[:body[0]].hex() not in _NATIVE_ENCODINGS: novel.add(body[:body[0]])
    assert len(cases) == 88 and len(novel) == 30


def test_timer_counter_header_shapes_keep_distinct_native_operand_bindings():
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-step-width-20260920.zip") as z:
        cases = json.loads(z.read("timer-controls/probes.json"))["cases"]
        saved = z.read("g.gxw")
        native_csv = z.read("g-reopened.csv")
    for case in cases:
        if "body_hex" not in case:
            continue
        raw = token_envelope(bytes.fromhex(case["body_hex"]))
        listing = decode_token_listing(raw, text_encoding="cp936")
        assert not listing.gaps and listing.reconstruct() == raw
        assert [{"kind": "instruction", "op": i.mnemonic, "args": list(i.args)} for i in listing.instructions] == case["native_records"]
    streams = {s.logical_name: s.raw for s in inspect_project(saved).streams if s.logical_name}
    listing = decode_token_listing(streams["MAIN.Program.pou"], text_encoding="cp936")
    assert listing.instructions[1].args == ("T10Z0", "K10")
    assert listing.instructions[1].step_width == 4 and listing.instructions[-1].step == 5
    assert list(csv.reader(io.StringIO(listing.csv_bytes().decode("utf-16")), delimiter="\t"))[3:] == list(
        csv.reader(io.StringIO(native_csv.decode("utf-16")), delimiter="\t"))[3:]


def test_native_width_rejections_remain_distinct_from_editor_check_acceptance():
    manifest = json.loads((ROOT / "research/results/token-20260919/step-width-manifest.json").read_text("utf-8"))
    with zipfile.ZipFile(ROOT / manifest["archive"]) as z:
        checks = json.loads(z.read("corpus-checks.json"))
        controls = json.loads(z.read("width-control-checks.json"))
        saved = z.read("b.gxw")
    failures = [c for c in checks if not c["unchanged"]]
    assert len(failures) == 4 and {c["return_code"] for c in failures} == {"0xFFFFFFF3"}
    assert all(c["canonical_bytes_restored"] for c in controls if c["mode"] == "recompute-width")
    assert all(c["native_value"] == -1 for c in controls if c["mode"] == "stored-steps" and c["width"] == 255)
    assert manifest["native_validation"]["cases"]["b"]["program_check_errors"] == 0
    raw = next(s.raw for s in inspect_project(saved).streams if s.logical_name == "MAIN.Program.pou")
    # Inspection faithfully reads the editor-preserved one-operand spelling;
    # it must not silently add a preset based on the conflicting internal call.
    assert decode_token_listing(raw).instructions[1].args == ("T10Z0",)


def test_public_machinecode_export_proof_is_independent_of_stored_step_widths():
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-machinecode-20260920.zip") as z:
        requests = json.loads(z.read("native-project-controls/requests.json"))["requests"]
        results = [json.loads(line) for line in z.read("native-project-controls/native-stdout.jsonl").splitlines()]
        mutants = json.loads(z.read("width-checks.json"))
        failed_abi = json.loads(z.read("initial/process.json"))
    assert failed_abi["returncode"] == 0xC0000005 and failed_abi["completed"] == 0
    for request, result in zip(requests, results):
        if request["case"] == "b":
            assert result["return_code"] == "0x04010004"
            continue
        assert result["return_code"] == "0x00000000"
        assert result["consumed_bytes"] == len(base64.b64decode(request["input_base64"]))
        assert result["native_value"] == request["source_steps"]
        assert len(base64.b64decode(result["output_base64"])) == 2 * result["native_value"]
    assert len(requests) == len(results) == 7
    outputs = {}
    for case in mutants:
        assert case["return_code"] == "0x00000000"
        outputs.setdefault(case["op"], set()).add((case["machine_words"], case["machine_bytes_hex"]))
    assert len(mutants) == 35 and all(len(v) == 1 for v in outputs.values())


def recorded_roundtrip_oracle(monkeypatch, archive, prefix, mutate=None):
    import importlib
    monkeypatch.syspath_prepend(str(ROOT / "research"))
    module = importlib.import_module("native_gxw_roundtrip")
    def replay(requests, directory, **kwargs):
        phase = Path(directory).name
        expected = json.loads(archive.read(f"{prefix}/{phase}/requests.json"))["requests"]
        assert requests == expected  # Bind replay to the full bytes, order and operation.
        rows = [json.loads(s) for s in archive.read(f"{prefix}/{phase}/native-stdout.jsonl").splitlines()]
        if mutate:
            mutate(phase, rows)
        return rows
    monkeypatch.setattr(module, "native_batch", replay)
    return module


def test_machine_roundtrip_replays_exact_normalized_text_loss_and_rejection(monkeypatch, tmp_path):
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-machine-roundtrip-20260920.zip") as z:
        module = recorded_roundtrip_oracle(monkeypatch, z, "controls")
        cases = json.loads(z.read("controls/cases.json"))
        observed = module.roundtrip_bodies(cases, tmp_path / "controls")
        archived = json.loads(z.read("controls/roundtrip.json"))
        # Bounded compiler fragments now have a Core projection without END.
        # Keep every recorded native fact/status unchanged; compare the newly
        # available Core fields independently instead of freezing the old error.
        for old, new in zip(archived["cases"], observed["cases"]):
            if "core_projection_error" in old:
                assert "core_projection_error" not in new
                assert old["index"] in range(8, 15)
                assert new["core_gaps"] == 0 and new["core_native_readings_agree"]
                old.pop("core_projection_error")
                old.update(core_gaps=0, core_native_readings_agree=True)
        assert observed == archived
        assert observed["summary"] == {"source-byte-identical": 6, "forward-rejected": 1,
                                       "source-text-omitted": 1, "source-normalized": 7}
        bad = next(c for c in observed["cases"] if c["status"] == "forward-rejected")
        assert bad["forward_error_byte_offset"] == 19
        note = next(c for c in observed["cases"] if c["status"] == "source-text-omitted")
        assert note["ordered_nontext_records_equal"] and not note["ordered_native_records_equal"]
        assert all(c["machinecode_bytes_equal"] for c in observed["cases"] if c is not bad)
        # These are recorded exports from one DLL, not live native execution.
        floats = json.loads(z.read("float-loss-controls/roundtrip.json"))["cases"]
        assert len(floats) == 22 and all(c["source_bytes_equal"] for c in floats)
        assert all(a["machinecode_sha256"] != b["machinecode_sha256"] for a, b in zip(floats[::2], floats[1::2]))


@pytest.mark.parametrize("fault", ["forward-rejected", "incomplete-consumption", "machinecode-differs"])
def test_new_encoding_guard_rejects_native_error_truncation_and_machine_change(monkeypatch, tmp_path, fault):
    prefix = "generate-" + ("incomplete" if fault == "forward-rejected" else "complete") + "/machinecode-roundtrip"
    def mutate(phase, rows):
        if fault == "incomplete-consumption" and phase == "forward":
            rows[0]["consumed_bytes"] -= 1
        if fault == "machinecode-differs" and phase == "recompiled":
            changed = bytearray(base64.b64decode(rows[0]["output_base64"]))
            changed[-1] ^= 1
            rows[0]["output_base64"] = base64.b64encode(changed).decode()
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-machine-roundtrip-20260920.zip") as z:
        module = recorded_roundtrip_oracle(monkeypatch, z, prefix, mutate)
        body = base64.b64decode(json.loads(z.read(f"{prefix}/cases.json"))[0]["body_base64"])
        with pytest.raises(ValueError, match="native machinecode roundtrip rejected"):
            module.require_machinecode_roundtrip([body], tmp_path / "guard")
    result = json.loads((tmp_path / "guard/roundtrip.json").read_text(encoding="utf-8"))
    assert result["cases"][0]["status"] == fault


@pytest.mark.parametrize("operation", ["generate", "patch"])
def test_new_instruction_workflows_reject_missing_timer_preset_before_composition(monkeypatch, tmp_path, operation):
    import importlib
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-machine-roundtrip-20260920.zip") as z:
        prefix = operation + "-incomplete"
        recorded_roundtrip_oracle(monkeypatch, z, prefix + "/machinecode-roundtrip")
        name = "generate_native_token_project" if operation == "generate" else "patch_native_token_instructions"
        module = importlib.import_module(name)
        native_prefix = prefix if operation == "generate" else prefix + "/encode"
        def encode(requests, directory, **kwargs):
            assert requests == json.loads(z.read(native_prefix + "/requests.json"))["requests"]
            Path(directory).mkdir(parents=True, exist_ok=True)
            return [json.loads(s) for s in z.read(native_prefix + "/native-stdout.jsonl").splitlines()]
        def should_not_compose(*args, **kwargs):
            pytest.fail("native-rejected new instructions reached project composition")
        monkeypatch.setattr(module, "native_batch", encode)
        monkeypatch.setattr(module, "_replace_token_program" if operation == "generate" else "patch_token_instructions", should_not_compose)
        with pytest.raises(ValueError, match="native machinecode roundtrip rejected"):
            if operation == "generate":
                with zipfile.ZipFile(ROOT / "research/evidence/gxw-step-width-20260920.zip") as seeds:
                    seed = seeds.read("empty.gxw")
                module.generate(seed, sha256(seed), "LD M8000\nOUT T10Z0\nEND", tmp_path / "generate")
            else:
                module.patch(z.read("editor-timer.gxw"), json.loads(z.read(prefix + "/plan.json")), tmp_path / "patch")


@pytest.mark.parametrize("header", ["054c000305", "054cff0305", "054c11ff05", "064c11037f06", "074c1103020007"])
def test_family_recognition_does_not_guess_unknown_flags_or_zero_width(header):
    # These are mutations, not encoder-produced accepted programs.
    raw = token_envelope(bytes.fromhex(header + "04ee410405a8900105033403"))
    listing = decode_token_listing(raw, text_encoding="cp936")
    assert listing.gaps and listing.reconstruct() == raw
    with pytest.raises(GXWFormatError): listing.csv_bytes()


def instruction_patch_fixture(key="a"):
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-instruction-patch-20260920.zip") as z:
        source = z.read(key + "/base.gxw")
        report = json.loads(z.read(key + "/patch.json"))
        edits = tuple(TokenInstructionPatch(e["old_offset"], bytes.fromhex(e["old_raw_hex"]),
                                           bytes.fromhex(e["new_raw_hex"])) for e in report["edits"])
        return source, edits, z.read(key + "/patched.gxw"), z.read(key + "/native-saved.gxw")


def test_multi_instruction_patch_replays_native_proof_in_original_coordinates():
    source, edits, expected, native = instruction_patch_fixture()
    # Reverse the edit order: both offsets remain bound to the original source.
    result = patch_token_instructions(source, expected_sha256=sha256(source), logical_name="MAIN.Program.pou",
                                      edits=edits[::-1], text_encoding="cp936")
    assert result.data == expected
    def streams(blob):
        return {s.logical_name: s.raw for s in inspect_project(blob).streams if s.logical_name and s.raw is not None}
    before, patched, saved = map(streams, (source, result.data, native))
    assert patched == dict(before, **{"MAIN.Program.pou": patched["MAIN.Program.pou"]})
    assert patched["MAIN.Program.pou"] == saved["MAIN.Program.pou"]
    assert before["MAIN.res"] == patched["MAIN.res"] != saved["MAIN.res"]
    compiled = parse_token_resource(saved["MAIN.res"])
    assert compiled.code_regions[0].body == parse_token_pou(saved["MAIN.Program.pou"]).body
    # Historical evidence replay only; this test does not invoke native code.
    assert result.report["validation"]["native_compile"] == "not_run"


@pytest.mark.parametrize("case", ["hash", "program", "empty", "duplicate", "offset", "operand_offset", "raw", "end", "two", "gap", "label", "delete", "bool_offset", "list_offset"])
def test_instruction_patch_refuses_unbound_or_ambiguous_targets_and_replacements(case):
    source, edits, _, _ = instruction_patch_fixture()
    a = edits[0]
    kwargs = dict(expected_sha256=sha256(source), logical_name="MAIN.Program.pou", edits=(a,), text_encoding="cp936")
    if case == "hash": kwargs["expected_sha256"] = "stale"
    elif case == "program": kwargs["logical_name"] = "MAIN.res"
    elif case == "empty": kwargs["edits"] = ()
    elif case == "duplicate": kwargs["edits"] = (a, a)
    else:
        offset, old, new = a.offset, a.expected_raw, a.replacement_raw
        if case == "offset": offset = 0
        elif case == "operand_offset": offset += old[0]
        elif case == "raw": old = old[:-1]
        elif case == "end": new = bytes.fromhex("033403")
        elif case == "two": new += new
        elif case == "gap": new = bytes.fromhex("037f03")
        elif case == "label": new = bytes.fromhex("033c0304d00004")
        elif case == "delete": new = b""
        elif case == "bool_offset": offset = True
        elif case == "list_offset": offset = []
        kwargs["edits"] = (TokenInstructionPatch(offset, old, new),)
    with pytest.raises(GXWFormatError):
        patch_token_instructions(source, **kwargs)


def test_instruction_patch_preserves_native_strings_when_their_encoding_is_unknown():
    source, edits, _, _ = instruction_patch_fixture()
    result = patch_token_instructions(source, expected_sha256=sha256(source), logical_name="MAIN.Program.pou",
                                      edits=(edits[0],))  # deliberately no text decoding
    def raw_pou(blob):
        return next(s.raw for s in inspect_project(blob).streams if s.logical_name == "MAIN.Program.pou")
    old, new = map(decode_token_listing, (raw_pou(source), raw_pou(result.data)))
    assert old.gaps and len(old.gaps) == len(new.gaps) == result.report["source_gaps_preserved"]
    assert [b"".join(t.raw for t in g.tokens) for g in old.gaps] == [b"".join(t.raw for t in g.tokens) for g in new.gaps]
    assert [b"".join(t.raw for t in r.tokens) for r in old.records[2:]] == [b"".join(t.raw for t in r.tokens) for r in new.records[2:]]


def test_native_conversion_corpus_matches_input_expectations_and_keeps_rejections():
    # Replays recorded vendor bytes; pytest does not execute the native DLL.
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))
    canonical = oracle["canonical_record"]
    passed, rejected = 0, []
    for case in NATIVE_CONVERSION["cases"]:
        if case["encode_result"] != "0x00000000":
            rejected.append(case)
            continue
        raw = token_envelope(bytes.fromhex(case["body_hex"]))
        listing = decode_token_listing(raw)
        ours = [{"kind": "instruction", **item} for item in listing.instruction_ir()]
        assert [canonical(r) for r in ours] == [canonical(r) for r in case["native_records"]], case["text"]
        assert listing.reconstruct() == raw
        passed += 1
    assert passed == 782 and len(rejected) == 9
    assert all("body_hex" not in case for case in rejected)


def test_native_operand_corpus_binds_groups_without_losing_float_or_string_bytes():
    canonical = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))["canonical_record"]
    passed, refused = 0, 0
    for case in NATIVE_OPERANDS["cases"]:
        if "native_records" not in case:
            assert case["encode_result"] != "0x00000000" and "body_hex" not in case
            refused += 1
            continue
        raw = token_envelope(bytes.fromhex(case["body_hex"]))
        listing = decode_token_listing(raw, text_encoding="cp936")
        ours = [canonical({"kind": "instruction", **r}) for r in listing.instruction_ir()]
        assert ours == [canonical(r) for r in case["native_records"]], case["text"]
        assert listing.reconstruct() == raw
        for instruction in listing.instructions:
            assert tuple(t for group in instruction.operands for t in group.tokens) == instruction.tokens[1:]
        passed += 1
    assert (passed, refused) == (879, 1146)


@pytest.mark.parametrize("tokens", [
    "04f00004",                       # missing base
    "04f0000404f1040404900004",       # unobserved modifier order
    "04f0000404f4000404a80004",       # two index modifiers
    "04f1090404900004",              # unobserved digit-group count
    "04f1040404a80004",              # digit group on a word device
    "04f2000404900004",              # unobserved bit-selector base
    "04f0080404a80004",              # unobserved index number
])
def test_unobserved_modifier_shapes_remain_gaps_and_keep_following_instruction(tokens):
    raw = token_envelope(bytes.fromhex("030003" + tokens + "032003049d0104033403"))
    listing = decode_token_listing(raw)
    assert listing.gaps and listing.reconstruct() == raw
    assert [i.mnemonic for i in listing.instructions] == ["OUT", "END"]
    assert all(i.step is None for i in listing.instructions)


def test_string_operand_requires_encoding_and_csv_preserves_spaces_and_backslash():
    align = runpy.run_path(str(ROOT / "research/align_gxw_tokens.py"))
    for case in NATIVE_OPERANDS["cases"]:
        if not case.get("native_records") or not case["text"].startswith("$MOV"):
            continue
        raw = token_envelope(bytes.fromhex(case["body_hex"]))
        opaque = decode_token_listing(raw)
        assert opaque.gaps and opaque.reconstruct() == raw
        listing = decode_token_listing(raw, text_encoding="cp936")
        csv_instructions, _ = align["read_csv"](listing.csv_bytes())
        assert csv_instructions[0]["operands"] == list(listing.instructions[0].args)


def test_float_display_is_explicitly_not_raw_float_reconstruction():
    case = next(c for c in NATIVE_OPERANDS["cases"] if c["text"] == "DEMOV E0.0000001234567 D100")
    listing = decode_token_listing(token_envelope(bytes.fromhex(case["body_hex"])))
    operand = listing.instructions[0].operands[0]
    assert operand.text == "E0.0000001235"
    assert struct.pack("<f", float(operand.text[1:])) != operand.tokens[0].raw[2:-1]


def test_compiled_resource_regions_match_independent_native_il_with_labels():
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))
    checked = 0
    for case in NATIVE_RESOURCES["cases"]:
        raw = base64.b64decode(case["raw_base64"])
        assert sha256(raw) == case["resource_sha256"]
        resource = parse_token_resource(raw)
        assert resource.reconstruct() == raw
        # The frozen oracle used this historical field name for the suffix;
        # it is not independent evidence of source-program ownership.
        assert resource.observed_cached_names[0] == case["sources"][0]["observed_program_name"].removesuffix(".Program.pou")
        for region, recorded in zip(resource.code_regions, case["regions"]):
            listing = decode_token_program(region, text_encoding="cp936")
            assert listing.reconstruct() == raw and not listing.gaps
            response = recorded["native_response"]
            native = oracle["native_il_records"](base64.b64decode(response["output_base64"]))
            assert native == recorded["native_records"]
            assert [oracle["canonical_record"](r) for r in oracle["listing_records"](listing)] == [oracle["canonical_record"](r) for r in native]
            assert response["consumed_bytes"] == len(region.body)
            if any(isinstance(r, TokenLabel) for r in listing.records):
                with pytest.raises(GXWFormatError, match="labels"):
                    listing.instruction_ir()
                rows = list(csv.reader(io.StringIO(listing.csv_bytes().decode("utf-16")), delimiter="\t"))[3:]
                assert [row[2] for row in rows if row[2].startswith(("P", "I")) and row[2][1:].isdigit()] == [r.text for r in listing.records if isinstance(r, TokenLabel)]
            checked += 1
    assert checked == 29


def test_native_nop_and_pointer_interrupt_labels_are_complete_source_partitions():
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))
    for case in NATIVE_RESOURCES["label_controls"]:
        raw = token_envelope(bytes.fromhex(case["body_hex"]))
        listing = decode_token_listing(raw)
        assert oracle["listing_records"](listing) == case["native_records"]
        assert listing.reconstruct() == raw
        assert HARNESS["reverse_token_boundaries"](raw) == [(t.offset, t.raw) for t in listing.source.tokens]
        if isinstance(listing.records[0], TokenLabel):
            assert listing.instructions[0].step == listing.records[0].step_width


def test_control_flow_generation_has_native_csv_label_steps_without_losing_labels():
    from gxw.lossless import _replace_token_program
    align = runpy.run_path(str(ROOT / "research/align_gxw_tokens.py"))
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))
    manifest = json.loads((ROOT / "research/results/token-20260919/control-flow-manifest.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(ROOT / manifest["archive"]) as z:
        empty, generated, saved = [z.read("samples/" + n) for n in ("empty.gxw", "generated.gxw", "native-saved.gxw")]
        native_csv, controls = z.read("native/reopened.csv"), json.loads(z.read("controls/probe.json"))
    def program(blob):
        return next(s.raw for s in inspect_project(blob).streams if s.logical_name == "MAIN.Program.pou")
    result = _replace_token_program(empty, "MAIN.Program.pou", program(empty), program(generated))
    assert result.data == generated and program(generated) == program(saved)
    assert result.report["allocations"]["program"] == "extend_mini_in_root"
    listing = decode_token_listing(program(saved))
    assert [(r.text, r.step, r.step_width) for r in listing.records if isinstance(r, TokenLabel)] == [("P256", 11, 2), ("P0", 16, 1), ("I1", 20, 1)]
    assert listing.instructions[-1].step == 24
    with pytest.raises(GXWFormatError, match="labels"):
        listing.instruction_ir()
    ours = list(csv.reader(io.StringIO(listing.csv_bytes(title="c").decode("utf-16")), delimiter="\t"))
    native = list(csv.reader(io.StringIO(native_csv.decode("utf-16")), delimiter="\t"))
    canonical = lambda rows: [[align["canonical_operand"](value) for value in row] for row in rows if any(row)]
    assert canonical(ours) == canonical(native)
    alignment = align["align"](saved, native_csv)
    assert alignment["status"] == "agrees" and alignment["encoded_steps"] == 25
    for case in controls:
        projection = decode_token_listing(token_envelope(bytes.fromhex(case["body_hex"])))
        assert not projection.gaps
        assert [oracle["canonical_record"](r) for r in oracle["listing_records"](projection)] == [oracle["canonical_record"](r) for r in case["native_records"]]


def test_actual_native_patch_retains_old_compiled_code_until_native_conversion():
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-lossless-20260919.zip") as z:
        images = [z.read("samples/" + name) for name in ("p.gxw", "p_compile.gxw")]
    readings = []
    for source in images:
        image = inspect_project(source)
        stream = next(s for s in image.streams if (s.logical_name or "").endswith(".res"))
        resource = parse_token_resource(stream.raw)
        program = next(s for s in image.streams if s.logical_name == "1.Program.pou")
        assert "X2" in [n.symbol for n in parse_structured_pou(program.raw).nodes]
        readings.append(decode_token_program(resource.code_regions[0]).instructions[0].args)
    assert readings == [("X1",), ("X2",)]


@pytest.mark.parametrize("legacy", [False, True])
def test_resource_unknown_suffix_survives_but_broken_code_bounds_are_rejected(legacy):
    raw = base64.b64decode(NATIVE_RESOURCES["cases"][0]["raw_base64"])
    if legacy:
        # Second prefix independently captured in the FX3U SFC empty-task
        # control. The remaining frame and opaque bytes must stay untouched.
        prefix = bytes.fromhex("01000000000001000000000000000000000000000200000001000000010000000000")
        raw = prefix + raw[len(prefix):]
    resource = parse_token_resource(raw)
    opaque = raw[:resource.suffix_offset] + b"future suffix"
    parsed = parse_token_resource(opaque)
    assert parsed.reconstruct() == opaque and parsed.observed_cached_names is None
    for position, value in ((54, 0xFFFFFFFF), (58 + len(resource.code_regions[0].body), 1)):
        broken = bytearray(raw)
        struct.pack_into("<I", broken, position, value)
        with pytest.raises(GXWFormatError):
            parse_token_resource(bytes(broken))


@pytest.mark.parametrize("manifest_name,instructions,steps", [
    ("native-manifest.json", 24, 112), ("operand-native-manifest.json", 16, 90),
])
def test_native_token_generation_archive_reproduces_cross_allocation_source_bytes(manifest_name, instructions, steps):
    from gxw.lossless import _replace_token_program
    manifest = json.loads((ROOT / "research/results/token-20260919" / manifest_name).read_text(encoding="utf-8"))
    archive = ROOT / manifest["archive"]
    assert sha256(archive.read_bytes()) == manifest["archive_sha256"]
    with zipfile.ZipFile(archive) as z:
        for name, expected in manifest["files"].items():
            assert sha256(z.read(name)) == expected["sha256"]
        baseline, generated, saved = [z.read("samples/" + name) for name in ("empty.gxw", "generated.gxw", "native-saved.gxw")]
        native_csv = z.read("native/reopened.csv")
    def source(raw):
        outer = CompoundFile(raw)
        stream = logical_mapping(outer.read_stream("projectdatalist.xml"))["MAIN.Program.pou"]
        return CompoundFile(outer.read_stream("_hdb")).read_stream(stream)
    result = _replace_token_program(baseline, "MAIN.Program.pou", source(baseline), source(generated))
    assert result.data == generated
    assert result.report["allocations"] == {"program": "grow_root_and_tables", "outer": "append_regular", "history": "existing_allocation"}
    assert source(generated) == source(saved)
    assert len(decode_token_listing(source(saved), text_encoding="cp936").instructions) == instructions
    alignment = runpy.run_path(str(ROOT / "research/align_gxw_tokens.py"))["align"](saved, native_csv)
    assert alignment["status"] == "agrees" and alignment["encoded_steps"] == steps


@pytest.mark.parametrize("case", NATIVE_SLICES, ids=lambda c: c["opcode"] + "@" + str(c["pou_offset"]))
def test_native_exported_instruction_slices_decode_to_the_vendor_reading(case):
    body = bytes.fromhex(case["raw_hex"])
    if case["opcode"] != "END":
        body += bytes.fromhex("033403")
    raw = token_envelope(body)
    listing = decode_token_listing(raw)
    assert not listing.gaps and listing.reconstruct() == raw
    instruction = listing.instructions[0]
    # Native device zero-padding and leading hex zeroes are lexical aliases.
    align = runpy.run_path(str(ROOT / "research/align_gxw_tokens.py"))
    canonical = align["canonical_operand"]
    assert instruction.mnemonic == case["opcode"]
    assert [canonical(x) for x in instruction.args] == case["operands"]
    assert instruction.step_width == case["step_width"]


def test_opaque_instruction_prevents_false_complete_export_and_absolute_steps():
    raw = token_envelope(bytes.fromhex("03fe03049c0104032003049d0104033403"))
    listing = decode_token_listing(raw)
    assert listing.reconstruct() == raw
    assert listing.gaps and listing.instructions[0].step is None
    assert listing.instructions[0].mnemonic == "OUT"
    with pytest.raises(GXWFormatError):
        listing.instruction_ir()
    with pytest.raises(GXWFormatError):
        listing.csv_bytes()


def test_bad_instruction_arity_is_retained_without_stealing_the_next_opcode():
    raw = token_envelope(bytes.fromhex("054c05000504e80104032003049d0104033403"))
    listing = decode_token_listing(raw)
    assert listing.gaps[0].reason.startswith("MOV:")
    assert [i.mnemonic for i in listing.instructions] == ["OUT", "END"]
    assert listing.reconstruct() == raw


def test_text_requires_explicit_decodable_codepage_and_keeps_its_raw_bytes():
    text = "电机".encode("cp936")
    raw = token_envelope(bytes([len(text) + 4, 0x80, 0]) + text + bytes([len(text) + 4]) + bytes.fromhex("033403"))
    opaque = decode_token_listing(raw)
    assert isinstance(opaque.records[0], TokenText) and opaque.records[0].text is None
    with pytest.raises(GXWFormatError):
        opaque.csv_bytes()
    decoded = decode_token_listing(raw, text_encoding="cp936")
    assert decoded.records[0].text == "电机" and decoded.reconstruct() == raw
    assert not decode_token_listing(raw, text_encoding="ascii").records[0].text


@pytest.mark.parametrize("case", CORPUS, ids=lambda c: c["source"])
def test_native_token_corpus_reconstruction_and_separate_res_bytes(case):
    raw, resource = base64.b64decode(case["program_base64"]), base64.b64decode(case["res_base64"])
    assert sha256(raw) == case["program_sha256"]
    assert sha256(resource) == case["res_sha256"]
    p = parse_token_pou(raw)
    assert p.reconstruct() == raw
    assert p.body in resource
    assert HARNESS["reverse_token_boundaries"](raw) == [(t.offset, t.raw) for t in p.tokens]
    image = inspect_program(raw)
    assert image.layout == "ladder-token" and image.reconstruct() == raw


def token_case(prefix):
    return base64.b64decode(next(c for c in CORPUS if c["source"].startswith(prefix))["program_base64"])


NATIVE_OPAQUE_TOKEN_TRAILER = bytes.fromhex("00000000000000002ce03301a6add460c4e0330100000000")


@pytest.mark.parametrize("trailer_bytes", [20, 24])
def test_native_nonzero_token_trailer_is_preserved_without_entering_the_body(trailer_bytes):
    # Both extents retained these nonzero bytes through a fresh FX3U compile,
    # full ProgramCheck, save and reopen. The trailer's meaning is unknown.
    control = frame_token_pou(token_case("02_"))
    trailer = NATIVE_OPAQUE_TOKEN_TRAILER[:trailer_bytes]
    raw = control.raw[:control.body_end] + trailer
    framed = frame_token_pou(raw)
    assert framed.body == control.body and framed.tokens == control.tokens
    assert framed.reconstruct() == raw
    listing = decode_token_listing(raw, profile="fx3u")
    assert not listing.gaps and listing.reconstruct() == raw
    for profile in (None, "fx3u", "fx") if trailer_bytes == 24 else (None, "fx3u"):
        image = inspect_program(raw, token_profile=profile)
        assert image.reconstruct() == raw
        assert image.regions[-1].offset == control.body_end
        assert image.regions[-1].raw == trailer
        assert image.regions[-1].handling == "opaque-preserved"
    if trailer_bytes == 24:
        assert parse_token_pou(raw).body == control.body
    else:
        with pytest.raises(GXWFormatError):
            parse_token_pou(raw)


@pytest.mark.parametrize("trailer_bytes", [19, 21, 23, 25])
def test_token_source_rejects_unobserved_trailer_extents(trailer_bytes):
    control = frame_token_pou(token_case("02_"))
    raw = control.raw[:control.body_end] + bytes(trailer_bytes)
    for parser in (parse_token_pou, frame_token_pou):
        with pytest.raises(GXWFormatError):
            parser(raw)
    image = inspect_program(raw)
    assert image.layout == "unsupported"
    assert image.reconstruct() == raw and len(image.regions) == 1


@pytest.mark.parametrize("profile", ["fx3u", "fx1s"])
@pytest.mark.parametrize("trailer_bytes,prefix_word", [(20, 0), (20, 1), (24, 0), (24, 1)])
def test_cpu_bound_fx_source_frames_keep_every_byte_and_unknown_record(profile, trailer_bytes, prefix_word):
    # Envelope variants observed in the independent real-source compiler
    # cross-check; derive the controlled body from the existing native corpus.
    raw = bytearray(token_case("02_"))
    struct.pack_into("<I", raw, 63, prefix_word)
    if trailer_bytes == 20:
        del raw[-4:]
        with pytest.raises(GXWFormatError):
            decode_token_listing(raw)
    listing = decode_token_listing(raw, profile=profile)
    assert listing.profile == profile and not listing.gaps
    assert listing.reconstruct() == raw
    image = inspect_program(raw, token_profile=profile)
    assert image.layout == "ladder-token-" + profile
    assert image.reconstruct() == raw and not image.diagnostics
    assert len(raw) - listing.source.body_end == trailer_bytes
    opaque = inspect_program(raw, token_profile=None)
    assert opaque.layout == "ladder-framed"
    assert all(r.handling == "opaque-preserved" for r in opaque.regions)
    # An unknown token stays at its exact source offset. CPU selection is not
    # permission to skip bytes, invent an instruction or export complete IR.
    raw[80] = 0x7f
    unknown = inspect_program(raw, token_profile=profile)
    assert unknown.reconstruct() == raw and unknown.projection.gaps
    assert unknown.regions[1].handling == "opaque-preserved"
    with pytest.raises(GXWFormatError):
        unknown.projection.instruction_ir()


@pytest.mark.parametrize("cpu,profile", [(520, "fx3u"), (518, "fx1s")])
def test_real_fx_ret_header_matches_independent_native_encode_and_decode(cpu, profile):
    evidence = json.loads((ROOT / "research/results/token-20260919/native-fx-cpu-lexical-20260927.json").read_text(encoding="utf-8"))
    raw = bytes.fromhex(evidence["ret_body_hex"])
    native = evidence["ret"][str(cpu)]
    assert native["encode"]["return_code"] == native["decode"]["return_code"] == "0x00000000"
    assert base64.b64decode(native["encode"]["output_base64"]) == raw
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))["native_il_projection"]
    listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile=profile)
    assert q_native_records(listing) == oracle(base64.b64decode(native["decode"]["output_base64"]), encoding="cp936")["records"]
    assert [(r.mnemonic, r.args, r.step) for r in listing.instructions] == [("RET", (), 0), ("END", (), 1)]
    assert listing.reconstruct() == raw
    if profile == "fx1s":
        with pytest.raises(GXWFormatError):
            listing.csv_bytes()  # No FX1S native CSV header has been observed.


@pytest.mark.parametrize("case", json.loads((ROOT / "tests/fixtures/gxw_fx_legacy_native.json").read_text(encoding="utf-8"))["cases"],
                         ids=lambda c: c["id"])
def test_fx1s_legacy_source_edits_match_native_save_and_reopen(case):
    raw = base64.b64decode(case["program_base64"])
    original = decode_token_listing(raw, profile=case["profile"])
    assert not original.gaps and original.reconstruct() == raw
    assert len(raw) - original.source.body_end == 20
    expected = {"baseline": "K2", "same-size-k3": "K3", "grow-k300": "K300"}[case["id"]]
    assert original.instructions[1].args == (expected, "D3")
    for phase in case["phases"]:
        assert phase["outcome"]["open_succeeded"] and phase["outcome"]["compile_completed"]
        assert phase["outcome"]["program_check_completed"] and not phase["outcome"]["compiler_rejected"]
        saved = base64.b64decode(phase["program_base64"])
        listing = decode_token_listing(saved, profile=case["profile"])
        assert listing.source.body == original.source.body == base64.b64decode(phase["pcode_base64"])
        assert len(saved) - listing.source.body_end == 24
        assert listing.reconstruct() == saved


@pytest.mark.parametrize("cpu,profile", [("FX3U/FX3UC", "fx3u"), ("FX1S", "fx1s"), ("FX3G", "fx3g"), ("FX1N", None)])
def test_source_cpu_metadata_selects_fx_profiles_without_name_guessing(cpu, profile):
    from types import SimpleNamespace

    def metadata_string(text):
        return struct.pack("<I", len(text) + 1) + (text + "\0").encode("utf-16le")

    raw = metadata_string("") + metadata_string(cpu) + b"\x01" + bytes(47) + struct.pack("<I", 936)
    image = SimpleNamespace(streams=[SimpleNamespace(layer="nested", raw=raw, logical_name="Project.prj")])
    context = HARNESS["project_token_context"](image)
    assert context["cpu"] == cpu and context["token_profile"] == profile
    assert context["text_encoding"] == "gb18030"


@pytest.mark.parametrize("cpu,profile", [(521, "fx3g"), (520, "fx3u"), (518, "fx1s")])
def test_fx_extended_devices_and_edge_opcodes_match_independent_native_controls(cpu, profile):
    evidence = json.loads((ROOT / "tests/fixtures/gxw_fx3g_lexical_native.json").read_text(encoding="utf-8"))
    oracle = runpy.run_path(str(ROOT / "research/native_gxw_tokens.py"))["native_il_projection"]
    for case in evidence["cases"]:
        raw = bytes.fromhex(case["body_hex"])
        native = case["native"][str(cpu)]
        assert native["return_code"] == "0x00000000" and native["consumed_bytes"] == len(raw)
        listing = decode_token_program(parse_token_fragment(raw, 0, len(raw)), profile=profile)
        assert listing.reconstruct() == raw
        # The vendor prints even three-byte addresses and modified interrupt
        # labels. They remain unsupported here; lexical acceptance does not
        # establish valid CPU ranges or permission to expand the reader.
        supported = ("width" not in case or case["width"] <= 2 and (
            case["code"] == 0xAF and case["modifier"] != "04f20004" or
            case["code"] == 0xD1 and not case["modifier"]))
        if supported:
            assert not listing.gaps
            assert q_native_records(listing) == oracle(base64.b64decode(native["output_base64"]), encoding="cp1252")["records"]
        else:
            assert listing.gaps
            with pytest.raises(GXWFormatError):
                listing.instruction_ir()
        if profile == "fx3g":
            with pytest.raises(GXWFormatError):
                listing.csv_bytes()  # FX3G CSV header is not independently observed.


@pytest.mark.parametrize("case", SIMPLE_SOURCES, ids=lambda c: c["id"])
def test_native_q_and_legacy_sources_are_framed_without_fx_semantic_promotion(case):
    raw = base64.b64decode(case["program_base64"])
    assert sha256(raw) == case["program_sha256"]
    framed = frame_token_pou(raw)
    assert framed.reconstruct() == raw
    assert sha256(framed.body) == case["body_sha256"]
    assert len(framed.body) == case["native_consumed_bytes"]
    assert case["native_return_code"] == "0x00000000"
    assert len(framed.tokens) == case["token_count"]
    assert len(raw) - framed.body_end == case["trailer_bytes"]
    assert HARNESS["reverse_token_boundaries"](raw, body_end=framed.body_end) == [
        (t.offset, t.raw) for t in framed.tokens]
    image = inspect_program(raw)
    assert image.layout == "ladder-framed" and image.reconstruct() == raw
    assert all(r.handling == "opaque-preserved" for r in image.regions)
    # Framing must not enable the existing FX lexical/writer entry point.
    with pytest.raises(GXWFormatError):
        parse_token_pou(raw)
    report = HARNESS["program_report"](raw, case["logical_name"], [])
    assert report["critical_token_gaps"] == len(framed.tokens)
    assert report["decoded_instructions"] == 0
    assert report["framing_cross_check"] == "agrees"


@pytest.mark.parametrize("offset,value", [(54, 0), (55, 0), (63, 2), (64, 1), (67, 0), (79, 0)])
def test_framing_only_source_rejects_broken_envelopes_and_boundaries(offset, value):
    raw = bytearray(base64.b64decode(SIMPLE_SOURCES[1]["program_base64"]))
    raw[offset] = value
    with pytest.raises(GXWFormatError):
        frame_token_pou(raw)
    image = inspect_program(raw)
    assert image.layout == "unsupported"
    assert image.reconstruct() == raw and len(image.regions) == 1


def test_observed_zero_prefix_keeps_original_bytes_and_body_boundaries():
    raw = bytearray(base64.b64decode(SIMPLE_SOURCES[1]["program_base64"]))
    control = frame_token_pou(raw)
    raw[63:67] = b"\0" * 4
    framed = frame_token_pou(raw)
    assert framed.reconstruct() == bytes(raw)
    assert framed.body == control.body
    assert (framed.body_offset, framed.body_end) == (control.body_offset, control.body_end)
    image = inspect_program(raw)
    assert image.layout == "ladder-framed" and image.reconstruct() == bytes(raw)
    assert all(r.handling == "opaque-preserved" for r in image.regions)


def test_annotations_preserve_numeric_base_and_signed_width_without_guessing_roles():
    tokens = parse_token_pou(token_case("07_")).tokens
    assert tokens[1].annotation() == {"kind": "operand", "device_type": "X", "numeric_value": 8}
    for prefix, value, width in [("27_", -1, 16), ("28_", 32768, 32), ("32_", 32767, 32), ("33_", -1, 32)]:
        annotation = parse_token_pou(token_case(prefix)).tokens[3].annotation()
        assert annotation == {"kind": "operand", "constant_type": "K", "width_bits": width, "numeric_value": value}


def test_unknown_token_is_not_dropped_or_promoted_to_instruction():
    raw = bytearray(token_case("02_"))
    raw[80] = 0xFE
    p = parse_token_pou(bytes(raw))
    assert p.tokens[0].annotation()["kind"] == "opaque"
    assert p.reconstruct() == raw


@pytest.mark.parametrize("offset,value", [(79, 0), (81, 4), (55, 0)])
def test_broken_framing_becomes_whole_opaque_stream_never_resynchronizes(offset, value):
    raw = bytearray(token_case("02_"))
    raw[offset] = value
    with pytest.raises(GXWFormatError):
        parse_token_pou(bytes(raw))
    image = inspect_program(bytes(raw))
    assert image.layout == "unsupported"
    assert image.reconstruct() == raw and len(image.regions) == 1


def native_source():
    with zipfile.ZipFile(ROOT / "research/evidence/gxw-abi-checkpoint-20260919.zip") as z:
        return z.read("frozen/set_spaced_compile.gxw")


@pytest.mark.parametrize("program_segment,label_segment", [("程序", "标签"), ("", "")])
def test_corpus_counts_localized_programs_without_changing_payloads(program_segment, label_segment):
    from gxw.container_writer import replace_project_stream
    from gxw.project_metadata import current_rows

    source = native_source()
    xml = CompoundFile(source).read_stream("projectdatalist.xml")
    _, encoding = current_rows(xml, "DSPROJECTDATA", "D_Projectdata")
    text = xml.decode(encoding).replace(".Program.pou", f".{program_segment}.pou").replace(
        ".Labels.lh", f".{label_segment}.lh")
    changed, _ = replace_project_stream(source, "projectdatalist.xml", text.encode(encoding))
    before, after = HARNESS["analyze"](source), HARNESS["analyze"](changed)
    assert after["source_replay"] == "byte-identical"
    for key in ("programs", "program_layout_gaps", "recognized_objects", "known_node_projections"):
        assert after["coverage"][key] == before["coverage"][key]
    programs = [s for s in after["streams"] if "program" in s]
    assert programs and all(s["logical_name"].endswith(f".{program_segment}.pou") for s in programs)
    before_payloads = {(s.layer, s.name): s.raw for s in inspect_project(source).streams if s.layer == "nested"}
    after_payloads = {(s.layer, s.name): s.raw for s in inspect_project(changed).streams if s.layer == "nested"}
    assert after_payloads == before_payloads
    comparisons = HARNESS["compare"](changed, changed)["known_structured_projections"]
    assert all(s["logical_name"] in comparisons for s in programs)


@pytest.mark.parametrize("folder", ["8", "99"])
def test_corpus_does_not_interpret_sfc_or_unknown_metadata_as_label_tables(folder):
    from gxw.container_writer import replace_project_stream
    from gxw.project_metadata import current_rows

    source = native_source()
    xml = CompoundFile(source).read_stream("projectdatalist.xml")
    rows, encoding = current_rows(xml, "DSPROJECTDATA", "D_Projectdata")
    edits, names = [], []
    for row in rows:
        fields = row.fields()
        name = fields["szName"].text.strip()
        if name.endswith(".Labels.lh"):
            field = fields["ucFolderType"]
            edits.append((field.content_start, field.content_end, folder.encode(encoding)))
            names.append(name)
    assert names
    for start, end, replacement in sorted(edits, reverse=True):
        xml = xml[:start] + replacement + xml[end:]
    changed, _ = replace_project_stream(source, "projectdatalist.xml", xml)
    report = HARNESS["analyze"](changed)
    declarations = [s for s in report["streams"] if s["logical_name"] in names]
    assert declarations and all("declaration_rows" not in s for s in declarations)
    assert all(s["handling"] == "opaque-preserved" and s["parse_gap"] for s in declarations)


@pytest.mark.parametrize("folder,suffix,has_source", [("92", "lnb", True), ("83", "lbo", False), ("99", "lnb", False)])
def test_corpus_library_source_selection_uses_metadata_and_keeps_companions_opaque(folder, suffix, has_source):
    from gxw.container_writer import replace_project_stream
    from gxw.project_metadata import current_rows

    source = native_source()
    xml = CompoundFile(source).read_stream("projectdatalist.xml")
    rows, encoding = current_rows(xml, "DSPROJECTDATA", "D_Projectdata")
    edits, names = [], []
    for row in rows:
        fields = row.fields()
        logical = fields["szName"].text.strip()
        if logical.endswith(".pou"):
            target = logical[:-4] + "\\Example." + suffix
            names.append(target)
            for key, value in (("szName", target), ("ucFolderType", folder)):
                field = fields[key]
                edits.append((field.content_start, field.content_end, value.encode(encoding)))
    assert names
    for start, end, replacement in sorted(edits, reverse=True):
        xml = xml[:start] + replacement + xml[end:]
    changed, _ = replace_project_stream(source, "projectdatalist.xml", xml)
    report = HARNESS["analyze"](changed)
    targets = [s for s in report["streams"] if s["logical_name"] in names]
    assert len(targets) == len(names)
    assert all(("program" in s) == has_source for s in targets)
    assert report["coverage"]["library_programs"] == (len(names) if has_source else 0)
    if not has_source:
        assert all(s["handling"] == "opaque-preserved" for s in targets)
    assert {(s.layer,s.name):s.raw for s in inspect_project(source).streams if s.layer=="nested"} == {
        (s.layer,s.name):s.raw for s in inspect_project(changed).streams if s.layer=="nested"}


def token_container_fixture(trailer=None):
    """Synthetic container wrapper around the native MOV K10 D1 token POU."""
    source = native_source()
    outer = CompoundFile(source)
    logical = "1.Program.pou"
    stream = logical_mapping(outer.read_stream("projectdatalist.xml"))[logical]
    nested = outer.read_stream("_hdb")
    before = CompoundFile(nested).read_stream(stream)
    after = token_case("10_")
    if trailer is not None:
        assert len(trailer) == 24
        after = after[:-24] + trailer
    history = synchronize_history(outer.read_stream("history.xml"), {logical: (stream, before, after)})[0]
    source = replace_stream_within_allocation(source, "_hdb", replace_stream_within_allocation(nested, stream, after, allow_shrink=True))
    return replace_stream_within_allocation(source, "history.xml", history)


@pytest.mark.parametrize("value,resize", [(11, False), (256, True), (-1, True)])
@pytest.mark.parametrize("trailer", [bytes(24), NATIVE_OPAQUE_TOKEN_TRAILER], ids=["zero", "native-nonzero"])
def test_constant_patch_preserves_every_other_token_and_stream(value, resize, trailer):
    source = token_container_fixture(trailer)
    logical, stream, original = pou(source)
    before = parse_token_pou(original)
    token = next(t for t in before.tokens if t.annotation().get("constant_type") == "K")
    result = patch_token_constant(source, expected_sha256=sha256(source), logical_name=logical,
        token_offset=token.offset, old_value=10, new_value=value, allow_resize=resize)
    _, _, updated = pou(result.data)
    after = parse_token_pou(updated)
    assert updated[after.body_end:] == original[before.body_end:] == trailer
    index = before.tokens.index(token)
    assert after.tokens[index].annotation()["numeric_value"] == value
    assert [t.raw for i, t in enumerate(before.tokens) if i != index] == [t.raw for i, t in enumerate(after.tokens) if i != index]
    outer_a, outer_b = CompoundFile(source), CompoundFile(result.data)
    for entry in outer_a.iter_streams():
        if entry.name not in ("_hdb", "history.xml"):
            assert outer_a.read_entry(entry) == outer_b.read_stream(entry.name)
    a, b = CompoundFile(outer_a.read_stream("_hdb")), CompoundFile(outer_b.read_stream("_hdb"))
    for entry in a.iter_streams():
        if entry.name != stream:
            assert a.read_entry(entry) == b.read_stream(entry.name)


@pytest.mark.parametrize("override", [{"expected_sha256": "stale"}, {"old_value": 9},
    {"new_value": 256}, {"new_value": 32768, "allow_resize": True}, {"token_offset": 79}])
def test_constant_patch_rejects_wrong_target_width_and_numeric_type(override):
    source = token_container_fixture()
    logical, _, original = pou(source)
    token = next(t for t in parse_token_pou(original).tokens if t.annotation().get("constant_type") == "K")
    args = dict(expected_sha256=sha256(source), logical_name=logical, token_offset=token.offset, old_value=10, new_value=11)
    args.update(override)
    with pytest.raises(GXWFormatError):
        patch_token_constant(source, **args)


def pou(source):
    outer = CompoundFile(source)
    logical = "1.Program.pou"
    name = logical_mapping(outer.read_stream("projectdatalist.xml"))[logical]
    inner = CompoundFile(outer.read_stream("_hdb"))
    return logical, name, inner.read_stream(name)


def test_inventory_keeps_every_raw_stream_and_physical_provenance():
    pytest.importorskip("olefile")
    source = native_source()
    image = inspect_project(source)
    assert not image.diagnostics and image.reconstruct() == source
    assert len(image.streams) == 58
    outer = CompoundFile(source)
    for s in image.streams:
        container = source if s.layer == "outer" else outer.read_stream("_hdb")
        assert b"".join(container[e.container_offset:e.container_offset + e.length] for e in s.extents) == s.raw
    assert HARNESS["independent_cfb"](source)["status"] == "agrees"


def test_known_contact_patch_preserves_native_unknown_set_and_every_unrelated_byte():
    pytest.importorskip("olefile")
    source = native_source()
    logical, _, raw = pou(source)
    program = parse_structured_pou(raw)
    node = next(n for n in program.nodes if n.kind_code == 3)
    opaque = next(n for n in program.nodes if n.kind_code == 7).raw
    result = patch_structured_symbol_equal_size(source, expected_sha256=sha256(source),
        logical_name=logical, node_offset=node.offset, old_symbol=node.symbol, new_symbol="X2")
    assert opaque in pou(result.data)[2]
    allowed = result.report["writer_touch"]["allowed_ranges"]
    assert all(a == b or any(s["offset"] <= i < s["offset"] + s["length"] for s in allowed)
               for i, (a, b) in enumerate(zip(source, result.data)))
    assert result.report["validation"]["native_compile"] == "not_run"
    assert HARNESS["independent_cfb"](result.data)["status"] == "agrees"
    assert HARNESS["independent_cfb"](CompoundFile(result.data).read_stream("_hdb"))["status"] == "agrees"


def test_unsupported_known_class_record_is_opaque_but_does_not_hide_neighbor():
    source = native_source()
    logical, name, raw = pou(source)
    program = parse_structured_pou(raw)
    node = next(n for n in program.nodes if n.kind_code == 3)
    unsupported = next(n for n in program.nodes if n.kind_code == 7)
    mutant = bytearray(raw)
    # A future node layout with an unsupported port descriptor, same valid
    # outer record framing. Do not claim this synthetic mutant is native-valid.
    struct.pack_into("<I", mutant, unsupported.offset + unsupported.record_length - 32, 20)
    with pytest.raises(GXWFormatError, match="port size"):
        parse_structured_pou(bytes(mutant))
    image = inspect_program(bytes(mutant))
    assert image.layout == "structured"
    assert len(image.projection.unknown_records) == 1
    assert any(n.offset == node.offset for n in image.projection.nodes)
    assert image.reconstruct() == mutant
    with pytest.raises(GXWFormatError):
        serialize_structured_pou(image.projection)  # strict production writer unchanged
    outer = CompoundFile(source)
    inner = replace_stream_within_allocation(outer.read_stream("_hdb"), name, bytes(mutant))
    source = replace_stream_within_allocation(source, "_hdb", inner)
    result = patch_structured_symbol_equal_size(source, expected_sha256=sha256(source),
        logical_name=logical, node_offset=node.offset, old_symbol=node.symbol, new_symbol="X2")
    patched = inspect_program(pou(result.data)[2]).projection
    assert patched.unknown_records[0].raw == image.projection.unknown_records[0].raw


@pytest.mark.parametrize("override", [{"expected_sha256": "bad"}, {"new_symbol": "X100"}, {"node_offset": 0}, {"new_symbol": "X\0"}])
def test_targeted_patch_rejects_stale_ambiguous_or_resizing_requests(override):
    source = native_source()
    logical, _, raw = pou(source)
    node = next(n for n in parse_structured_pou(raw).nodes if n.kind_code == 3)
    args = dict(expected_sha256=sha256(source), logical_name=logical, node_offset=node.offset,
                old_symbol=node.symbol, new_symbol="X2")
    args.update(override)
    with pytest.raises(GXWFormatError):
        patch_structured_symbol_equal_size(source, **args)


def test_corrupt_container_still_preserved_without_claiming_parse_success():
    source = b"not a CFB"
    image = inspect_project(source)
    assert image.reconstruct() == source and image.diagnostics
    result = HARNESS["compare"](source, source)
    assert result["bytes"] == "byte-identical" and result["structure"] == "not-checkable"


def test_failure_capture_replay_binds_bytes_and_never_invents_native_pass(tmp_path):
    source = tmp_path / "bad.gxw"
    source.write_bytes(b"broken")
    target = tmp_path / "failure"
    HARNESS["capture"](source, target, stage="compile", reason="recorded failure", copy_source=True)
    result = HARNESS["replay"](target / "case.json")
    assert result["current"]["diagnostics"] and result["native_validation"].startswith("not_run")
    (target / "source.gxw").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash changed"):
        HARNESS["replay"](target / "case.json")


def test_scanner_deduplicates_without_losing_provenance_or_failure_cases(tmp_path):
    for name in ("a.gxw", "b.gxw"):
        (tmp_path / name).write_bytes(b"broken")
    report = HARNESS["scan"]([tmp_path])
    assert report["summary"]["source_count"] == 2
    assert report["summary"]["unique_projects"] == 1
    assert len(report["cases"][0]["sources"]) == 2 and report["failure_index"]


def test_independent_reader_detects_a_production_reader_fault(monkeypatch):
    pytest.importorskip("olefile")
    original = CompoundFile.read_entry
    def bad_reader(self, entry):
        raw = original(self, entry)
        return raw[:-1] if entry.name == "history.xml" else raw
    monkeypatch.setattr(CompoundFile, "read_entry", bad_reader)
    assert HARNESS["independent_cfb"](native_source())["status"] == "differs"


def test_writer_rejection_does_not_change_parser_verdict(monkeypatch):
    def unsupported_writer(program):
        raise GXWFormatError("unmodeled writer field")
    monkeypatch.setitem(HARNESS["program_report"].__globals__, "serialize_structured_pou", unsupported_writer)
    logical, _, raw = pou(native_source())
    report = HARNESS["program_report"](raw, logical, [])
    assert report["strict_parser"] == "accepted"
    assert report["writer_roundtrip"] == "unsupported"
    assert report["writer_reason"] == "unmodeled writer field"


def test_all_evidence_manifests_include_nested_archives_and_external_screenshots():
    audit = runpy.run_path(str(ROOT / "research/audit_gxw_knowledge.py"))
    reports = audit["verify_evidence_manifests"]()
    assert len(reports) >= 6
    assert sum(r["verified_members"] for r in reports) >= 95
    assert sum(r["verified_external_screenshots"] for r in reports) == 17


def test_failure_corpus_attestations_are_repo_relative_and_hash_bound():
    manifest = json.loads((ROOT / "research/corpus/failures.json").read_text(encoding="utf-8"))
    for case in manifest["cases"]:
        source_path = case["source"]["path"]
        assert "\\" not in source_path and ":" not in source_path.split("/", 1)[0]
        for observation in case.get("recorded_observations", []):
            path = observation["path"]
            assert "\\" not in path and ":" not in path.split("/", 1)[0]
            assert sha256((ROOT / path).read_bytes()) == observation["sha256"]


def test_recorded_native_failures_are_kept_even_when_parsing_succeeds():
    report = HARNESS["scan"]([ROOT / "research/corpus/failures.json"])
    failures = [f for f in report["failure_index"] if f["stage"] == "compile"]
    assert len(failures) == 3
    assert all(f["replay"] == "native-not-run" for f in failures)
    assert report["summary"]["program_layouts"] == {"structured": 3}
    assert report["summary"]["coverage_totals"]["native_validated_in_this_run"] == 0


def test_native_patch_attestation_and_archive_bind_exact_reproducible_bytes():
    manifest = json.loads((ROOT / "research/results/lossless-20260919/native-manifest.json").read_text(encoding="utf-8"))
    archive = ROOT / manifest["archive"]
    assert sha256(archive.read_bytes()) == manifest["archive_sha256"]
    with zipfile.ZipFile(archive) as z:
        for member, expected in manifest["files"].items():
            assert sha256(z.read(member)) == expected["sha256"]
        before, patched, compiled = [z.read("samples/" + name) for name in ("base.gxw", "p.gxw", "p_compile.gxw")]
    logical, _, raw = pou(before)
    node = next(n for n in parse_structured_pou(raw).nodes if n.kind_code == 3)
    result = patch_structured_symbol_equal_size(before, expected_sha256=sha256(before),
        logical_name=logical, node_offset=node.offset, old_symbol="X1", new_symbol="X2")
    assert result.data == patched
    assert pou(patched)[2] == pou(compiled)[2]
    # These are historical assertions, not a native UI execution by pytest.
    assert manifest["native_validation"]["compile_all"] == {"errors": 0, "warnings": 0, "check_warnings": 0}
    assert manifest["native_validation"]["reopen"] is True
