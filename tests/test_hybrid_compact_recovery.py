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
