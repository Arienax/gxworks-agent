"""Frozen Web entry and a side-effect-free context-policy diagnostic."""
import json
import sys


def run():
    if sys.argv[1:] == ["--self-test-settings"]:
        from storage.user_data import runtime_self_test
        status = runtime_self_test()
        print(json.dumps(status, ensure_ascii=True))
        return 0 if status["status"] == "available" else 1
    if sys.argv[1:] == ["--settings-path-info"]:
        from pathlib import Path
        from storage.config import get_config_path
        config = Path(get_config_path())
        print(json.dumps({"config": str(config), "observations": str(config.parent / "model-observations.sqlite"),
                          "migration_performed": False, "model_called": False}, ensure_ascii=True))
        return 0
    if sys.argv[1:] == ["--self-test-knowledge"]:
        from knowledge.scope import runtime_status
        status = runtime_status()
        print(json.dumps(status, ensure_ascii=True))
        return 0 if status["status"] == "available" else 1
    if sys.argv[1:] == ["--self-test-openai-sdk"]:
        from model_runtime.provider import sdk_runtime_self_test
        return 0 if sdk_runtime_self_test() else 1
    if sys.argv[1:] == ["--context-policy-info"]:
        from shared.context_policy import resolve_context_policy
        policy = resolve_context_policy()
        print(json.dumps({"context_policy": policy.snapshot(),
                          "automatic_manuals": policy.manuals,
                          "control_examples": policy.examples,
                          "server_started": False, "model_called": False}, ensure_ascii=True))
        return 0
    from integrations.web.__main__ import main
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
