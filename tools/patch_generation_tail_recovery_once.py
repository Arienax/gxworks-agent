#!/usr/bin/env python3
"""One-shot patcher for deterministic trailing-noise JSON recovery."""
from pathlib import Path


def patch_generation() -> None:
    path = Path("src/application/generation.py")
    text = path.read_text(encoding="utf-8")
    old_doc = '''            def trim_redundant_json_tail(candidate):
                """Remove only a tiny redundant closing-delimiter tail.

                This is intentionally narrower than a generic JSON fixer. It
                accepts the exact safe case seen in production: a complete JSON
                object followed by one or a few stray '}' / ']' characters. It
                never drops prose, a second object, or arbitrary trailing data.
                """
'''
    new_doc = '''            def trim_redundant_json_tail(candidate):
                """Remove only a tiny non-semantic tail after a complete object.

                This is intentionally narrower than a generic JSON fixer. A
                complete top-level object may be followed by a few punctuation
                characters emitted by the model (for example `}`, `]`, `,`, `;`,
                `.`, a backtick, or CJK punctuation). We never discard letters,
                digits, quotes, or a second object/array because those may carry
                semantic content that must remain an explicit repair candidate.
                """
'''
    if old_doc not in text:
        raise SystemExit("generation docstring anchor not found")
    text = text.replace(old_doc, new_doc, 1)

    old_guard = '''                if (
                    not isinstance(value, dict)
                    or not suffix
                    or len(suffix) > 8
                    or any(char not in "}]" for char in suffix)
                ):
                    return candidate, False
'''
    new_guard = '''                if (
                    not isinstance(value, dict)
                    or not suffix
                    or len(suffix) > 8
                    or any(char.isalnum() or char in "{[\\\"'" for char in suffix)
                ):
                    return candidate, False
'''
    if old_guard not in text:
        raise SystemExit("generation tail guard anchor not found")
    text = text.replace(old_guard, new_guard, 1)
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_tests() -> None:
    path = Path("tests/test_generation_rejected_json_repair.py")
    text = path.read_text(encoding="utf-8")
    anchor = '''def test_generation_recovers_rejected_redundant_json_tail_without_model_retry(tmp_path):
    raw = json.dumps(_ladder(), ensure_ascii=False) + "}"

    def stream(*_args, **_kwargs):
        raise _json_rejection(raw)

    result = GenerationWorkflow(
        GenerationRequest("X0 controls Y0", model_name="offline"),
        tmp_path,
        dependencies=GenerationDependencies(
            stream_response=stream,
            generate_json=lambda *_a, **_k: pytest.fail(
                "redundant closing delimiter must not trigger another model call"
            ),
            preserve_rejected_candidate=True,
        ),
    ).run()

    assert result["validation"]["status"] == "candidate_ready"
    assert any("多余" in message for message in result["validation"]["messages"])
    assert json.loads((tmp_path / "ladder.json").read_text(encoding="utf-8")) == _ladder()
    assert not (tmp_path / "repair_candidate.json").exists()


'''
    replacement = '''@pytest.mark.parametrize("suffix", ["}", "]", ";", ",", ".", "。", "`"])
def test_generation_recovers_rejected_tiny_punctuation_tail_without_model_retry(tmp_path, suffix):
    raw = json.dumps(_ladder(), ensure_ascii=False) + suffix

    def stream(*_args, **_kwargs):
        raise _json_rejection(raw)

    result = GenerationWorkflow(
        GenerationRequest("X0 controls Y0", model_name="offline"),
        tmp_path,
        dependencies=GenerationDependencies(
            stream_response=stream,
            generate_json=lambda *_a, **_k: pytest.fail(
                "tiny punctuation tail must not trigger another model call"
            ),
            preserve_rejected_candidate=True,
        ),
    ).run()

    assert result["validation"]["status"] == "candidate_ready"
    assert any("多余" in message for message in result["validation"]["messages"])
    assert json.loads((tmp_path / "ladder.json").read_text(encoding="utf-8")) == _ladder()
    assert not (tmp_path / "repair_candidate.json").exists()


def test_generation_does_not_drop_semantic_extra_data(tmp_path):
    raw = json.dumps(_ladder(), ensure_ascii=False) + "x"

    def stream(*_args, **_kwargs):
        raise _json_rejection(raw)

    workflow = GenerationWorkflow(
        GenerationRequest("X0 controls Y0", model_name="offline"),
        tmp_path,
        dependencies=GenerationDependencies(
            stream_response=stream,
            generate_json=lambda *_a, **_k: pytest.fail(
                "semantic extra data must remain an explicit repair candidate"
            ),
            preserve_rejected_candidate=True,
        ),
    )

    with pytest.raises(GenerationValidationError):
        workflow.run()
    assert (tmp_path / "repair_candidate.json").read_text(encoding="utf-8") == raw


'''
    if anchor not in text:
        raise SystemExit("test anchor not found")
    text = text.replace(anchor, replacement, 1)
    path.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    patch_generation()
    patch_tests()
