"""Frozen Web entry and a side-effect-free context-policy diagnostic."""
import json
import sys


def run():
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
