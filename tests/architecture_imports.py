"""Static import references shared by architecture policies and their probes."""
import ast
from importlib.util import resolve_name


def module_references(path, source_root, *, source_text=None):
    parts = list(path.relative_to(source_root).with_suffix("").parts)
    package = ".".join(parts[:-1])
    module_name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    tree = ast.parse(path.read_text(encoding="utf-8-sig") if source_text is None else source_text,
                     filename=str(path))
    importlib_names = {"importlib"}
    builtins_names = {"builtins"}
    import_functions = set()
    builtin_import_functions = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            importlib_names.update(alias.asname or alias.name for alias in node.names
                                   if alias.name == "importlib")
            builtins_names.update(alias.asname or alias.name for alias in node.names
                                  if alias.name == "builtins")
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib" and not node.level:
            import_functions.update(alias.asname or alias.name for alias in node.names
                                    if alias.name == "import_module")
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins" and not node.level:
            builtin_import_functions.update(alias.asname or alias.name for alias in node.names
                                            if alias.name == "__import__")

    def absolute(name, owner=package):
        if not name.startswith("."):
            return name.removeprefix("src.")
        try:
            return resolve_name(name, owner).removeprefix("src.")
        except (ImportError, ValueError):
            # Also cover repositories imported through an outer ``src``
            # namespace; invalid escaping imports must not evade a policy.
            return name.lstrip(".").removeprefix("src.")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, absolute(alias.name)
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            base = absolute(name) if name else ""
            if base:
                yield node.lineno, base
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, absolute(".".join(filter(None, (base, alias.name))))
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            builtin_import = (isinstance(func, ast.Name) and func.id in builtin_import_functions
                              or isinstance(func, ast.Attribute) and func.attr == "__import__"
                              and isinstance(func.value, ast.Name) and func.value.id in builtins_names)
            dynamic = (builtin_import or isinstance(func, ast.Name) and func.id in import_functions
                       or isinstance(func, ast.Attribute) and func.attr == "import_module"
                       and isinstance(func.value, ast.Name) and func.value.id in importlib_names)
            if not dynamic or not isinstance(node.args[0], ast.Constant):
                continue
            name = node.args[0].value
            if not isinstance(name, str):
                continue
            owner = package
            supplied = node.args[1] if len(node.args) > 1 else next(
                (kw.value for kw in node.keywords if kw.arg == "package"), None)
            if isinstance(supplied, ast.Constant) and isinstance(supplied.value, str):
                owner = supplied.value
            elif isinstance(supplied, ast.Name) and supplied.id == "__name__":
                owner = module_name
            yield node.lineno, absolute(name, owner)
            if builtin_import:
                fromlist = node.args[3] if len(node.args) > 3 else next(
                    (kw.value for kw in node.keywords if kw.arg == "fromlist"), None)
                if isinstance(fromlist, (ast.List, ast.Tuple)):
                    for member in fromlist.elts:
                        if isinstance(member, ast.Constant) and isinstance(member.value, str) and member.value != "*":
                            yield node.lineno, absolute(name + "." + member.value, owner)


def dependency_violations(paths, source_root, forbidden):
    return [(path, line, module) for path in paths
            for line, module in module_references(path, source_root)
            if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden)]


def core_paths(source_root):
    return [source_root / "rendering/ladder_svg.py",
            *(path for package in ("plc", "gxw", "gxworks2", "simulator")
              for path in sorted((source_root / package).rglob("*.py")))]
