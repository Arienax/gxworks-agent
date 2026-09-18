import ast
from pathlib import Path

from ui.desktop.icons import ICON_CODEPOINTS, codicon


def _literal_icon_references(project_root):
    references = set()
    for path in project_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (
                function.id
                if isinstance(function, ast.Name)
                else function.attr
                if isinstance(function, ast.Attribute)
                else ""
            )
            argument_index = 1 if name == "set_codicon" else 0
            if name not in {"set_codicon", "codicon", "codicon_icon"}:
                continue
            if len(node.args) <= argument_index:
                continue
            argument = node.args[argument_index]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                references.add(argument.value)
    return references


def test_all_literal_codicon_references_have_mappings():
    project_root = Path(__file__).resolve().parents[1] / "src"
    assert not (_literal_icon_references(project_root) - set(ICON_CODEPOINTS))


def test_unknown_codicon_degrades_without_raising():
    assert codicon("not-a-real-icon") == codicon("warning")
