"""Frozen Web entry and side-effect-free runtime diagnostics."""
import json
import sys


def run():
    if sys.argv[1:] == ["--self-test-core"]:
        import importlib
        from gxw.object_model import default_baseline, native_catalog
        from gxw.token_q import _catalog
        packages = ("agent_runtime", "application", "gxw", "gxworks2", "inspection", "integrations",
                    "knowledge", "model_runtime", "plc", "rendering", "shared", "simulator", "storage")
        compiled = all(hasattr(importlib.import_module(name), "__compiled__") for name in packages)
        templates = len(default_baseline()) > 0 and len(native_catalog()) > 0
        catalogs = bool(_catalog("q02")["headers"] and _catalog("q03udv")["headers"])
        from application.compact_protocol import expand_compact_ladder
        from application.generation_agent import direct_response_schema
        legacy = {"r": [{"h": ["NO X0"], "s": [], "b": [{"i": [], "o": ["COIL Y0"]}]}]}
        current = {"result": {"r": [{"c": ["NO X0"], "b": [{"i": [], "o": ["COIL Y0"]}]}]}}
        from plc.ir import lower_rung_instructions
        def operations(value):
            return [(item["op"], item["args"]) for item in
                    lower_rung_instructions(expand_compact_ladder(value)["rungs"][0])]
        protocol = operations(legacy) == operations(current) == [("LD", ["X0"]), ("OUT", ["Y0"])]
        schema = direct_response_schema()
        protocol = protocol and schema.get("type") == "object" and "anyOf" not in schema
        print(json.dumps({"native_compiled": compiled, "embedded_templates": templates,
                          "cpu_catalogs": catalogs, "generation_protocol": protocol, "native_operations": False}))
        return 0 if compiled and templates and catalogs and protocol else 1
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
    from integrations.web.__main__ import main
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
