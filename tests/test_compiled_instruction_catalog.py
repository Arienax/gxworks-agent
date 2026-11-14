import json
import os
import shutil

from plc import instructions
from plc.instruction_catalog_store import CATALOG_FILENAME, build_compiled_catalog, read_compiled_catalog


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sources(directory):
    for name in instructions.CATALOG_REQUIRED_FILES:
        _write(directory / name, {"schema_version": 1})
    models = ["Q02", "Q03UDV"]
    _write(directory / "instruction_families.json", {
        "schema_version": 1,
        "cpu_contracts": [{
            "method": "reviewed-manual-contract-v1", "cpus": models,
            "instruction": {"mnemonic": "CHECK", "cpu_support": models, "arity": {"min": 1, "max": 1},
                            "operands": [{"name": "S", "data_type": "word", "address_forms": ["link_direct"],
                                          "address_form_limits": {"link_direct": {"allowed_device_prefixes": ["W"]}}}]},
            "native_order": ["S"], "verified_fields": ["arity", "operand_order"], "execution_form": "continuous",
            "sources": [{"manual_id": "fixture", "manual": "Fixture", "revision": "A",
                         "url": "https://example.invalid/manual", "pdf_page": 2, "models": models}],
        }],
        "instruction_families": [{"base_opcode": "CHECK", "facts": [], "shared_values": {
            "body": {"parameters": ["S"], "value": {"operands": ["S"]}},
            "alias": {"shared_ref": {"family": "CHECK", "name": "body"}},
        }}],
    })


def _select_sources(monkeypatch, directory):
    from shared import paths

    monkeypatch.delenv("GXW2_INSTRUCTION_OWNER_MANIFEST", raising=False)
    monkeypatch.setattr(instructions, "_candidate_catalog_directories", lambda: (directory,))
    monkeypatch.setattr(paths, "resource_path", lambda name: directory / "unavailable" / name)


def test_compiled_catalog_reuses_checked_specs_and_loads_contracts_by_cpu(tmp_path, monkeypatch):
    _sources(tmp_path)
    _select_sources(monkeypatch, tmp_path)
    raw = instructions.load_catalog_sources(tmp_path)
    assert build_compiled_catalog(tmp_path) == tmp_path / CATALOG_FILENAME

    def fail(_directory):
        raise AssertionError("valid catalogue should not rebuild from JSON")

    monkeypatch.setattr(instructions, "load_catalog_sources", fail)
    compiled = instructions.load_default_instruction_registry()
    assert compiled._specs == raw._specs
    assert list(compiled._cpu_contracts) == list(raw._cpu_contracts)
    assert not compiled._cpu_contracts._values
    assert "_family_knowledge" not in vars(compiled)
    assert compiled.resolve_catalog_form("CHECK", cpu="Q02") == raw.resolve_catalog_form("CHECK", cpu="Q02")
    assert set(compiled._cpu_contracts._values) == {("mitsubishi", "CHECK", "Q02")}
    assert compiled._family_knowledge == raw._family_knowledge


def test_copied_catalog_falls_back_when_an_authored_input_changes(tmp_path, monkeypatch):
    original = tmp_path / "original"
    copied = tmp_path / "copy"
    original.mkdir()
    copied.mkdir()
    _sources(original)
    build_compiled_catalog(original)
    for path in original.iterdir():
        target = copied / path.name
        shutil.copyfile(path, target)
        timestamp = path.stat().st_mtime_ns + 1_000_000_000
        os.utime(target, ns=(timestamp, timestamp))
    assert read_compiled_catalog(copied) is not None

    source = copied / "instruction_families.json"
    timestamp = source.stat().st_mtime_ns + 1_000_000_000
    source.write_text(source.read_text(encoding="utf-8").replace('"CHECK"', '"MATCH"'), encoding="utf-8")
    os.utime(source, ns=(timestamp, timestamp))
    assert read_compiled_catalog(copied) is None
    _select_sources(monkeypatch, copied)
    assert instructions.load_default_instruction_registry().resolve_catalog_form("MATCH", cpu="Q03UDV") is not None
    assert build_compiled_catalog(copied) == copied / CATALOG_FILENAME
    assert read_compiled_catalog(copied).resolve_catalog_form("MATCH", cpu="Q03UDV") is not None
