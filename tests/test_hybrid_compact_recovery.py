import json

import pytest

import application.model_api as api
from application.compact_alias import expand_hybrid_compact_ladder
from application.format_patch_repair import format_repair_response
from application.rejected_generation_preview import materialize_rejected_preview, recover_rejected_ladder


def _hybrid_compact():
    return {
        "device_comments": {},
        "rungs": [
            {
                "branches": [
                    {
                        "inputs": ["NO X3", {"or": [["NO X1"], ["NO M0"]]}],
                        "outputs": ["COIL M0"],
                    }
                ]
            },
            {
                "branches": [
                    {
                        "inputs": [">= D0 K1", "<= D0 K9"],
                        "outputs": ["COIL M10"],
                    }
                ]
            },
        ],
    }


def test_long_key_compact_alias_expands_without_changing_plc_tokens():
    ladder = expand_hybrid_compact_ladder(_hybrid_compact())
    assert ladder is not None
    assert len(ladder["rungs"]) == 2
    branch = ladder["rungs"][0]["branches"][0]
    assert branch["inputs"][0] == {"type": "NO", "address": "X3"}
    assert branch["inputs"][1] == {
        "type": "parallel_block",
        "branches": [
            [{"type": "NO", "address": "X1"}],
            [{"type": "NO", "address": "M0"}],
        ],
    }
    assert branch["outputs"] == [{"type": "COIL", "address": "M0"}]
    compare = ladder["rungs"][1]["branches"][0]["inputs"]
    assert compare == [
        {"type": "COMPARE", "expression": ">= D0 K1"},
        {"type": "COMPARE", "expression": "<= D0 K9"},
    ]


def test_format_repair_recovers_long_key_compact_alias_without_model(monkeypatch):
    raw = json.dumps(_hybrid_compact(), ensure_ascii=False, separators=(",", ":"))
    monkeypatch.setattr(
        api,
        "_request_model",
        lambda *args, **kwargs: pytest.fail("long-key compact alias must be deterministic"),
    )

    reasoning, content = format_repair_response(
        {"repair_mode": "format", "candidate_text": raw},
        "offline-model",
        "high",
    )

    assert reasoning == ""
    ladder = json.loads(content)
    assert ladder["rungs"][0]["branches"][0]["inputs"][0] == {
        "type": "NO",
        "address": "X3",
    }
    assert ladder["rungs"][0]["branches"][0]["outputs"] == [
        {"type": "COIL", "address": "M0"}
    ]


def test_rejected_preview_renders_long_key_compact_alias(tmp_path):
    raw = json.dumps(_hybrid_compact(), ensure_ascii=False, separators=(",", ":"))
    ladder, info = recover_rejected_ladder(raw)
    assert info["source_format"] == "compact_alias_ladder"
    assert len(ladder["rungs"]) == 2

    metadata = materialize_rejected_preview(raw, tmp_path, plc_model="FX3U")
    assert metadata["validation"]["status"] == "invalid_candidate"
    assert metadata["recovery"]["source_format"] == "compact_alias_ladder"
    for key in ("svg", "program_csv", "comment_csv"):
        path = tmp_path / metadata["artifacts"][key]
        assert path.is_file()
        assert path.stat().st_size > 0


@pytest.mark.parametrize("root_key", ["r", "root"])
def test_compact_root_alias_is_detached_idempotent_and_logic_preserving(root_key):
    import copy
    from application.compact_protocol import normalize_compact, expand_compact_ladder
    canonical = {"r": [{"h": None, "s": ["NC X1"], "b": [
        {"i": [{"or": [["NO X0"], ["NO Y0"]]}], "o": ["COIL Y0"]}]}]}
    value = {root_key: copy.deepcopy(canonical["r"])}
    before = copy.deepcopy(value)
    normalized, changes = normalize_compact(value)
    assert value == before and normalized == canonical
    assert normalize_compact(normalized) == (normalized, [])
    assert [row["rule"] for row in changes] == (["root_field_alias"] if root_key == "root" else [])
    assert expand_compact_ladder(value) == expand_compact_ladder(canonical)


@pytest.mark.parametrize("value", [
    {"root": [], "r": []}, {"root": [], "extra": "ignored?"},
    {"root": {"r": []}}, {"root": None}, {"root": []},
])
def test_compact_alias_never_discards_ambiguity_or_bypasses_schema(value):
    from application.compact_protocol import CompactProtocolError, expand_compact_ladder
    with pytest.raises(CompactProtocolError):
        expand_compact_ladder(value)


@pytest.mark.parametrize("field,value,keyword,path", [
    ("r", [], "minItems", "content.r"),
    ("s", False, "type", "content.r.0.s"),
    ("s", [7], "type", "content.r.0.s.0"),
    ("h", 1, "type", "content.r.0.h"),
    ("i", None, "type", "content.r.0.b.0.i"),
    ("i", [{"or": []}], "anyOf", "content.r.0.b.0.i.0"),
    ("i", [{"or": [[{"or": [["NO X0"]]}]]}], "anyOf", "content.r.0.b.0.i.0"),
    ("o", [], "minItems", "content.r.0.b.0.o"),
    ("o", [""], "minLength", "content.r.0.b.0.o.0"),
    ("o", ["A" * 161], "maxLength", "content.r.0.b.0.o.0"),
])
def test_compact_schema_reports_safe_paths_without_type_coercion(field, value, keyword, path):
    from application.compact_protocol import (
        CompactProtocolError, canonical_compact_example, expand_compact_ladder,
    )
    candidate = canonical_compact_example()
    target = candidate if field == "r" else candidate["r"][0] if field in {"h", "s"} else candidate["r"][0]["b"][0]
    target[field] = value
    with pytest.raises(CompactProtocolError) as caught:
        expand_compact_ladder(candidate)
    assert caught.value.path == path
    assert caught.value.schema_keyword == keyword


def test_schema_errors_do_not_echo_unknown_model_fields():
    from application.compact_protocol import (
        CompactProtocolError, canonical_compact_example, expand_compact_ladder,
    )
    candidate = canonical_compact_example()
    candidate["r"][0]["model_controlled_secret"] = "do not echo this"
    with pytest.raises(CompactProtocolError) as caught:
        expand_compact_ladder(candidate)
    assert caught.value.schema_keyword == "additionalProperties"
    assert caught.value.path == "content.r.0"
    assert "model_controlled_secret" not in str(caught.value)
    assert "do not echo this" not in str(caught.value)


def test_compact_missing_required_output_is_not_materialized():
    from application.compact_protocol import CompactProtocolError, expand_compact_ladder
    with pytest.raises(CompactProtocolError) as caught:
        expand_compact_ladder({"r": [{"b": [{}]}]})
    assert caught.value.path == "content.r.0.b.0.o"
    assert caught.value.schema_keyword == "required"


def test_compact_schema_keeps_documented_optional_defaults_and_semantic_parser():
    from application.compact_protocol import CompactProtocolError, expand_compact_ladder
    sparse = {"r": [{"b": [{"o": ["COIL Y0"]}]}]}
    explicit = {"r": [{"h": None, "s": [], "b": [{"i": [], "o": ["COIL Y0"]}]}]}
    assert expand_compact_ladder(sparse) == expand_compact_ladder(explicit)
    explicit["r"][0]["b"][0]["i"] = ["NOT_A_CONTACT X0"]
    with pytest.raises(CompactProtocolError):
        expand_compact_ladder(explicit)
