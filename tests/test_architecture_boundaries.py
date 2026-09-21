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
        SOURCE_ROOT / 'plc/core.py',
        SOURCE_ROOT / 'plc/generation_contract.py',
        SOURCE_ROOT / 'plc/ir.py',
        SOURCE_ROOT / 'plc/validation.py',
        SOURCE_ROOT / 'plc/semantics.py',
        SOURCE_ROOT / 'plc/static_analysis.py',
        SOURCE_ROOT / 'plc/timing.py',
        SOURCE_ROOT / 'plc/st_renderer.py',
        SOURCE_ROOT / "rendering/ladder_svg.py",
        *sorted((SOURCE_ROOT / "gxworks2").rglob("*.py")),
        *sorted((SOURCE_ROOT / "simulator").rglob("*.py")),
    ]
    forbidden_roots = {
        "openai",
        "anthropic",
        "zhipuai",
        "model_runtime.provider", "model_runtime",
        "agent_runtime.agent",
        "mcp",
        "mcp_types",
        "integrations",
    }

    violations = []
    for path in core_files:
        for imported in _imports(path):
            if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden_roots):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    assert violations == []


def test_provider_does_not_import_plc_gx_or_automation_implementation():
    forbidden_roots = {"plc.ir", "plc.core", "gxworks2", "simulator", "pywinauto", "rendering"}
    violations = [
        imported
        for imported in _imports(SOURCE_ROOT / 'model_runtime/provider.py')
        if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden_roots)
    ]
    assert violations == []


def test_agent_depends_on_runtime_not_plc_implementation():
    imports = _imports(SOURCE_ROOT / 'agent_runtime/agent.py')
    forbidden_roots = {"plc.core", "plc.ir", "agent_runtime.plc_tools", "gxworks2", "pywinauto"}
    assert [
        imported
        for imported in imports
        if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden_roots)
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
    assert _transport_field_accesses(SOURCE_ROOT / 'application/model_api.py') == []
    assert _transport_field_accesses(SOURCE_ROOT / 'agent_runtime/agent.py') == []


def test_mcp_adapters_do_not_import_plc_or_desktop_implementations():
    adapter_root = SOURCE_ROOT / "integrations" / "mcp"
    forbidden = {
        "plc.core", "plc.ir", "plc.validation", "plc.semantics",
        "plc.static_analysis", "plc.timing", "rendering", "inspection.engine",
        "knowledge.retriever", "gxworks2", "simulator", "pywinauto",
        "ui.desktop.qt", "PyQt5", "PyQt6", "main", "agent_runtime.agent", "openai",
        "api", "model_runtime.provider", "model_runtime", "config", "storage.config", "storage.credentials",
    }
    violations = [
        f"{path.relative_to(ROOT)} -> {imported}"
        for path in adapter_root.rglob("*.py")
        for imported in _imports(path)
        if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden)
    ]
    assert violations == []
    assert "agent_runtime.runtime" in _imports(adapter_root / "tool_adapter.py")
    assert "agent_runtime.runtime" in _imports(adapter_root / "server.py")
    assert "storage.session" in _imports(adapter_root / "context_provider.py")


def test_runtime_and_builtin_agent_do_not_depend_on_optional_mcp_sdk():
    for name in ('agent_runtime/runtime.py', 'agent_runtime/messages.py', 'plc/generation_contract.py', 'agent_runtime/agent.py', 'agent_runtime/plc_tools.py', 'model_runtime/provider.py', 'storage/session.py'):
        assert not {"mcp", "mcp_types", "integrations"}.intersection(
            imported.split(".", 1)[0] for imported in _imports(SOURCE_ROOT / name)
        )


def test_generation_contract_uses_only_stdlib_and_authoritative_instruction_registry():
    # The selected base already derives opcode schemas from the CPU instruction
    # registry. Keep that single source of truth instead of copying an allowlist.
    allowed = {"__future__", "copy", "re", "typing", "dataclasses", "json", "hashlib"}
    assert set(_imports(SOURCE_ROOT / "agent_runtime/messages.py")) <= allowed
    assert set(_imports(SOURCE_ROOT / "plc/generation_contract.py")) <= allowed | {"plc.instructions"}


def test_external_tool_runtime_has_no_model_or_credential_dependency():
    forbidden = {"api", "model_runtime.provider", "model_runtime", "config", "storage.config", "storage.credentials", "openai", "ui.desktop.qt", "PyQt5", "PyQt6"}
    for name in ('agent_runtime/runtime.py', 'agent_runtime/messages.py', 'agent_runtime/plc_tools.py', 'plc/generation_contract.py'):
        assert not [item for item in _imports(SOURCE_ROOT / name)
                    if any(item == prefix or item.startswith(prefix + ".") for prefix in forbidden)]


def test_model_provider_reexports_the_same_neutral_tool_types():
    import model_runtime.provider as model_provider
    import agent_runtime.messages as tool_messages

    assert model_provider.ToolCall is tool_messages.ToolCall
    assert model_provider.ToolResult is tool_messages.ToolResult


def _backend_bypasses(source):
    """Catch static imports/aliases and literal dynamic imports, not hostile reflection."""
    tree = ast.parse(source)
    found, aliases, modules = [], {"__import__", "import_module"}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            aliases.update(a.asname or a.name for a in node.names if a.name == "import_module")
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                modules[alias.asname or alias.name] = (node.module or "") + "." + alias.name
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lstrip(".")
            names = [module] + [module + "." + a.name for a in node.names]
        elif isinstance(node, ast.Call) and node.args:
            fn = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if fn in aliases and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                names = [node.args[0].value]
        elif isinstance(node, ast.Attribute):
            parts, current = [], node
            while isinstance(current, ast.Attribute):
                parts.insert(0, current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                names = [".".join([modules.get(current.id, current.id), *parts])]
        for name in names:
            if name == "knowledge.core" or name.startswith(("knowledge.core.", "knowledge.retriever._")):
                found.append((node.lineno, name))
    return found


def test_application_agents_and_integrations_use_the_scoped_retrieval_facade():
    violations = []
    for package in ("application", "agent_runtime", "integrations"):
        for path in (SOURCE_ROOT / package).rglob("*.py"):
            for line, name in _backend_bypasses(path.read_text(encoding="utf-8")):
                violations.append(f"{path.relative_to(ROOT)}:{line} -> {name}")
    assert not violations, "\n".join(violations)


def test_scoped_retrieval_guard_catches_aliases_and_literal_dynamic_imports():
    for source in (
        "import knowledge.core as backend", "from knowledge import core as backend",
        "from knowledge.core import retrieve_knowledge as lookup",
        "from knowledge.retriever import _core as backend",
        "from knowledge.retriever import _retrieve_uncached",
        "from knowledge import retriever as r; r._core._retrieve_knowledge('x')",
        "import knowledge.retriever as r; r._retrieve_uncached('x')",
        "from importlib import import_module as load; load('knowledge.core')",
        "import importlib as il; il.import_module('knowledge.core')",
        "__import__('knowledge.core')", "import knowledge; knowledge.core.retrieve_knowledge('x')",
    ):
        assert _backend_bypasses(source), source
    assert not _backend_bypasses("from knowledge.retriever import retrieve_knowledge")


def test_production_workflows_cannot_reintroduce_effort_hints():
    violations = []
    for package in ("application", "agent_runtime"):
        for path in (SOURCE_ROOT / package).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.keyword) and node.arg == "effort":
                    if not isinstance(node.value, ast.Constant) or node.value.value is not None:
                        violations.append((path.name, node.lineno, "non-neutral effort"))
                if isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "reasoning_effort" for k in node.keys):
                    violations.append((path.name, node.lineno, "workflow tuning"))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    positional = node.args.posonlyargs + node.args.args
                    defaults = list(zip(positional[-len(node.args.defaults):], node.args.defaults)) if node.args.defaults else []
                    defaults += list(zip(node.args.kwonlyargs, node.args.kw_defaults))
                    for arg, value in defaults:
                        if arg.arg == "effort" and value is not None and not (isinstance(value, ast.Constant) and value.value is None):
                            violations.append((path.name, node.lineno, "workflow effort default"))
    assert not violations, violations
