import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def _transport_field_accesses(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fields = {"choices", "delta", "reasoning_content"}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in fields:
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in fields:
                found.append((node.lineno, key.value))
    return found


def test_plc_and_gx_core_do_not_import_model_or_vendor_clients():
    core_files = [
        SOURCE_ROOT / "plc/core.py",
        SOURCE_ROOT / "plc/generation_contract.py",
        SOURCE_ROOT / "plc/ir.py",
        SOURCE_ROOT / "plc/validation.py",
        SOURCE_ROOT / "plc/semantics.py",
        SOURCE_ROOT / "plc/static_analysis.py",
        SOURCE_ROOT / "plc/timing.py",
        SOURCE_ROOT / "plc/st_renderer.py",
        SOURCE_ROOT / "rendering/ladder.py",
        *sorted((SOURCE_ROOT / "gxworks2").rglob("*.py")),
        *sorted((SOURCE_ROOT / "simulator").rglob("*.py")),
    ]
    forbidden_roots = {
        "openai",
        "anthropic",
        "zhipuai",
        "model_runtime.provider",
        "agent_runtime.agent",
        "mcp",
        "mcp_types",
        "integrations",
    }

    violations = []
    for path in core_files:
        for imported in _imports(path):
            if _blocked(imported, forbidden_roots):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert violations == []


def test_provider_does_not_import_plc_gx_or_automation_implementation():
    forbidden_roots = {"plc.ir", "plc.core", "gxworks2", "simulator", "pywinauto", "rendering.ladder"}
    violations = [
        imported
        for imported in _imports(SOURCE_ROOT / "model_runtime/provider.py")
        if _blocked(imported, forbidden_roots)
    ]
    assert violations == []


def test_agent_depends_on_runtime_not_plc_implementation():
    imports = _imports(SOURCE_ROOT / "agent_runtime/agent.py")
    forbidden_roots = {"plc.core", "plc.ir", "agent_runtime.plc_tools", "gxworks2", "pywinauto"}
    assert [
        imported
        for imported in imports
        if _blocked(imported, forbidden_roots)
    ] == []
    assert "agent_runtime.runtime" in imports
    assert "model_runtime.provider" in imports


def test_only_model_provider_imports_openai_sdk():
    violations = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if path == SOURCE_ROOT / "model_runtime/provider.py":
            continue
        if any(imported.split(".", 1)[0] == "openai" for imported in _imports(path)):
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_agent_and_api_do_not_parse_vendor_response_fields():
    assert _transport_field_accesses(SOURCE_ROOT / "application/model_workflows.py") == []
    assert _transport_field_accesses(SOURCE_ROOT / "agent_runtime/agent.py") == []


def test_mcp_adapters_do_not_import_plc_or_desktop_implementations():
    adapter_root = SOURCE_ROOT / "integrations" / "mcp"
    forbidden = {
        "plc.core", "plc.ir", "plc.validation", "plc.semantics",
        "plc.static_analysis", "plc.timing", "rendering.ladder", "inspection.engine",
        "knowledge.retriever", "gxworks2", "simulator", "pywinauto",
        "ui.desktop.qt", "PyQt5", "PyQt6", "ui.desktop.main_window", "agent_runtime.agent", "openai",
        "application.model_workflows", "model_runtime.provider", "config", "storage.config", "storage.credentials",
    }
    violations = [
        f"{path.relative_to(ROOT)} -> {imported}"
        for path in adapter_root.rglob("*.py")
        for imported in _imports(path)
        if _blocked(imported, forbidden)
    ]
    assert violations == []
    assert "agent_runtime.runtime" in _imports(adapter_root / "tool_adapter.py")
    assert "agent_runtime.runtime" in _imports(adapter_root / "server.py")
    assert "storage.session" in _imports(adapter_root / "context_provider.py")


def test_runtime_and_builtin_agent_do_not_depend_on_optional_mcp_sdk():
    for name in ("agent_runtime/runtime.py", "agent_runtime/messages.py", "plc/generation_contract.py", "agent_runtime/agent.py", "agent_runtime/plc_tools.py", "model_runtime/provider.py", "storage/session.py"):
        assert not {"mcp", "mcp_types", "integrations"}.intersection(
            imported.split(".", 1)[0] for imported in _imports(SOURCE_ROOT / name)
        )


def test_generation_contract_and_tool_messages_have_only_declared_support_dependencies():
    # The generation schema now derives opcodes from the instruction catalog.
    # Validate that exact support chain, not a blanket exemption for PLC modules.
    expected = {
        "plc/generation_contract.py": {"__future__", "copy", "re", "typing", "plc.instructions"},
        "agent_runtime/messages.py": {"__future__", "dataclasses", "typing"},
        "plc/instructions.py": {"__future__", "shared.paths", "json", "os", "sys", "dataclasses", "enum", "pathlib", "typing"},
        "shared/paths.py": {"pathlib", "sys"},
    }
    for name, allowed in expected.items():
        assert set(_imports(SOURCE_ROOT / name)) <= allowed


def test_external_tool_runtime_has_no_model_or_credential_dependency():
    forbidden = {"application.model_workflows", "model_runtime.provider", "config", "storage.config", "storage.credentials", "openai", "ui.desktop.qt", "PyQt5", "PyQt6"}
    for name in ("agent_runtime/runtime.py", "agent_runtime/messages.py", "agent_runtime/plc_tools.py", "plc/generation_contract.py"):
        assert not [item for item in _imports(SOURCE_ROOT / name) if _blocked(item, forbidden)]


def test_model_provider_reexports_the_same_neutral_tool_types():
    import model_runtime.provider as model_provider
    import agent_runtime.messages as tool_messages

    assert model_provider.ToolCall is tool_messages.ToolCall
    assert model_provider.ToolResult is tool_messages.ToolResult


def _blocked(module, prefixes):
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)
