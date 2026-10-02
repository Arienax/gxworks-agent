"""Recheck archived native observations without launching GX Works2."""
import hashlib
import json
from pathlib import Path
import runpy
import zipfile

import pytest

from gxw.container import CompoundFile
from gxw.experiment import compare_programs
from gxw.object_model import export_object_model, read_project


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "research/results/generation-evidence-manifest.json").read_text())


def evidence(name):
    with zipfile.ZipFile(ROOT / MANIFEST["archive"]) as archive:
        raw = archive.read(name)
    assert hashlib.sha256(raw).hexdigest() == MANIFEST["files"][name]["sha256"]
    return raw


@pytest.mark.parametrize("name,report", [
    ("auto_decl", "auto-declarations"), ("fresh_fbd", "fresh-fbd"), ("relay_parallel", "relay-parallel")])
def test_native_compile_save_reload_preserves_objects_wires_and_declarations(name, report):
    a, b = evidence(name + ".gxw"), evidence(name + "_compile.gxw")
    p, d, _ = read_project(a)
    q, e, _ = read_project(b)
    result = compare_programs(p, q)
    assert all(result["checks"].values())
    assert export_object_model(p, d)["labels"] == export_object_model(q, e)["labels"]
    recorded = json.loads((ROOT / ("research/results/" + report + "-roundtrip.json")).read_text())
    # Historical reports predate explicit blocks; retain their full comparison
    # while checking the new field separately, without rewriting old evidence.
    assert result['checks']['blocks']
    assert len(result['before']['blocks']) == len(result['after']['blocks']) == 1
    legacy = {key: {k: v for k, v in value.items() if k != 'blocks'}
              for key, value in result.items()}
    assert legacy == recorded["program"]
    assert hashlib.sha256(a).hexdigest() == recorded["source_sha256"]
    assert hashlib.sha256(b).hexdigest() == recorded["saved_sha256"]
    assert recorded["native_compile"]["errors"] == 0
    if name == "auto_decl":
        assert len(d["1.Labels.lh"].rows) == 1008
        assert CompoundFile(a).num_fat_sectors > 1
    elif name == "relay_parallel":
        assert recorded["native_compile"]["warnings"] == 1
        assert recorded["native_compile"]["warning_codes"] == ["C2034"]
        assert {n.symbol for n in q.nodes} == {"X0", "X1", "X2", "X3", "Y0", "Y1", "Y2"}


def test_cli_generate_inspect_noop_and_declaration_write(tmp_path):
    main = runpy.run_path(str(ROOT / "tools/gxw_project.py"))["main"]
    def call(*args):
        main([str(a) for a in args])
    baseline = tmp_path / "generated.gxw"
    call("fbd", ROOT / "research/models/two-ton.json", "-o", baseline, "--report", tmp_path / "generated.json")
    original = baseline.read_bytes()
    model_path = tmp_path / "objects.json"
    call("inspect", baseline, "-o", model_path)
    copy = tmp_path / "copy.gxw"
    call("fbd", model_path, "--baseline", baseline, "-o", copy, "--report", tmp_path / "copy.json")
    assert copy.read_bytes() == original
    declared = tmp_path / "declared.gxw"
    call("declarations", baseline, ROOT / "research/models/declarations.json", "-o", declared,
         "--report", tmp_path / "declarations.json")
    p, _, _ = read_project(original)
    q, docs, _ = read_project(declared.read_bytes())
    assert all(compare_programs(p, q)["checks"].values())
    row = next(r for r in docs["1.Labels.lh"].rows if r.name == "enable")
    assert row.device == "M100" and row.type_code == 1
    assert baseline.read_bytes() == original
    with pytest.raises(SystemExit):
        call("fbd", model_path, "-o", model_path, "--report", tmp_path / "not-written.json")
    assert not (tmp_path / "not-written.json").exists()


def test_archive_declaration_fixtures_are_native_streams():
    fixture = json.loads((ROOT / "tests/fixtures/gxw_declarations_20260910.json").read_text())
    for name, tables in fixture.items():
        _, documents, _ = read_project(evidence(name + ".gxw"))
        assert {key: d.raw.hex() for key, d in documents.items()} == tables


def test_source_bound_application_candidates_keep_graph_and_labels_after_native_save_and_cold_reopen():
    import base64
    from gxw.declarations import parse_declarations
    from gxw.structured_pou import parse_structured_pou
    with zipfile.ZipFile(ROOT/'research/evidence/gxw-fbd-application-v2-20261002.zip') as archive:
        cases = json.loads(archive.read('application-source-witnesses.json'))
    native_count = 0
    for case in cases:
        candidate = case['candidate']
        program = parse_structured_pou(base64.b64decode(candidate['program_base64']),
            logical_name=candidate['program'],preserve_unsupported_records=True)
        assert candidate['projection']['schema_version']==2
        assert candidate['cpu']==case['cpu']
        assert any(node.symbol=='APP_MODULE_STAGE_A' for node in program.nodes)
        declarations = {name:parse_declarations(base64.b64decode(raw),logical_name=name)
                        for name,raw in candidate['declarations'].items()}
        assert any(row.name=='APP_MODULE_STAGE_A' for row in declarations['FBD_MATRIX.Labels.lh'].rows)
        if 'native_cold' not in case:
            continue
        native_count += 1
        for name in ('native_saved','native_cold'):
            observed = case[name]
            actual = parse_structured_pou(base64.b64decode(observed['program_base64']),
                logical_name=observed['program'],preserve_unsupported_records=True)
            assert [record.raw for record in actual.iter_records()]==[record.raw for record in program.iter_records()]
            actual_labels = {key:parse_declarations(base64.b64decode(raw),logical_name=key)
                             for key,raw in observed['declarations'].items()}
            assert {key:[row.raw for row in doc.rows] for key,doc in actual_labels.items()} == {
                key:[row.raw for row in doc.rows] for key,doc in declarations.items()}
    assert len(cases)==67 and native_count==55
