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
from gxw.project_metadata import logical_mapping, synchronize_history
from gxw.structured_pou import parse_structured_pou
from gxw.structured_pou_writer import serialize_structured_pou
from gxw.token_pou import parse_token_pou
from gxw.token_listing import decode_token_listing, decode_token_program, TokenText, TokenLabel
from gxw.token_resource import parse_token_resource
from gxw.token_patch import TokenInstructionPatch, patch_token_instructions, TokenRecordSplice, splice_token_records

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((ROOT / "tests/fixtures/gxw_token_corpus.json").read_text(encoding="utf-8"))["cases"]
HARNESS = runpy.run_path(str(ROOT / "research/gxw_corpus.py"))
NATIVE_SLICES = json.loads((ROOT / "tests/fixtures/gxw_token_native_20260919.json").read_text(encoding="utf-8"))["cases"]
NATIVE_CONVERSION = json.loads((ROOT / "research/results/token-20260919/native-conversion-ladder-mode.json").read_text(encoding="utf-8"))
NATIVE_OPERANDS = json.loads((ROOT / "research/results/token-20260919/native-operand-corpus-20260920.json").read_text(encoding="utf-8"))
NATIVE_RESOURCES = json.loads((ROOT / "research/results/token-20260919/compiled-resource-corpus-20260920.json").read_text(encoding="utf-8"))


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
        assert resource.observed_program_names[0] + ".Program.pou" == case["sources"][0]["observed_program_name"]
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
        program_name = resource.observed_program_names[0] + ".Program.pou"
        program = next(s for s in image.streams if s.logical_name == program_name)
        assert "X2" in [n.symbol for n in parse_structured_pou(program.raw).nodes]
        readings.append(decode_token_program(resource.code_regions[0]).instructions[0].args)
    assert readings == [("X1",), ("X2",)]


def test_resource_unknown_suffix_survives_but_broken_code_bounds_are_rejected():
    raw = base64.b64decode(NATIVE_RESOURCES["cases"][0]["raw_base64"])
    resource = parse_token_resource(raw)
    opaque = raw[:resource.suffix_offset] + b"future suffix"
    parsed = parse_token_resource(opaque)
    assert parsed.reconstruct() == opaque and parsed.observed_program_names is None
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


@pytest.mark.parametrize("offset,value", [(79, 0), (81, 4), (55, 0), (-1, 1)])
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


def token_container_fixture():
    """Synthetic container wrapper around the native MOV K10 D1 token POU."""
    source = native_source()
    outer = CompoundFile(source)
    logical = "1.Program.pou"
    stream = logical_mapping(outer.read_stream("projectdatalist.xml"))[logical]
    nested = outer.read_stream("_hdb")
    before = CompoundFile(nested).read_stream(stream)
    after = token_case("10_")
    history = synchronize_history(outer.read_stream("history.xml"), {logical: (stream, before, after)})[0]
    source = replace_stream_within_allocation(source, "_hdb", replace_stream_within_allocation(nested, stream, after, allow_shrink=True))
    return replace_stream_within_allocation(source, "history.xml", history)


@pytest.mark.parametrize("value,resize", [(11, False), (256, True), (-1, True)])
def test_constant_patch_preserves_every_other_token_and_stream(value, resize):
    source = token_container_fixture()
    logical, stream, original = pou(source)
    before = parse_token_pou(original)
    token = next(t for t in before.tokens if t.annotation().get("constant_type") == "K")
    result = patch_token_constant(source, expected_sha256=sha256(source), logical_name=logical,
        token_offset=token.offset, old_value=10, new_value=value, allow_resize=resize)
    _, _, updated = pou(result.data)
    after = parse_token_pou(updated)
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
