"""Create a reviewable source-only public snapshot without private Git history.

This export is deliberately narrow. Research evidence, vendor manuals and their
derived knowledge indexes, internal development documents, and generated build
artifacts never enter the snapshot. Run it into a new empty directory, inspect the
result, and only then synchronize it with the public checkout.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


ROOT_FILES = (
    "LICENSE", "LICENSE-SCOPE.md", "THIRD_PARTY_NOTICES.md",
    "README.md", "README.zh-CN.md", "README.ja.md", "CHANGELOG.md",
    "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "ACCESSIBILITY.md",
    "build-web.bat", "start-web.cmd", "gxworks-agent-mcp.cmd", "gxworks-agent-mcp",
    "requirements/web.txt", "requirements/mcp.txt", "requirements/context.txt",
    "scripts/build_web_source.ps1", "scripts/start_web.ps1",
    "scripts/mcp_entry.py", "scripts/web_entry.py",
    "scripts/build_release.py", "scripts/build_web_package.ps1", "scripts/release_notices.py",
    "scripts/export_open_source.py",
    "scripts/release_smoke.py", "scripts/runtime_knowledge_resources.py",
    "scripts/runtime_st_smoke.py",
    "packaging/pyinstaller/release.spec", "requirements/release.txt", "requirements/release-mcp.txt",
    "tools/build_workspace_adapter.ps1", "tools/build_active_document.ps1", "tools/build_hardware_reader.ps1",
    "tools/build_simulator_gateway.ps1",
    "tools/gxw_visible_session.py",
    "resources/app.ico", "resources/config.default.json", "resources/plc_models.json",
    "resources/pattern_library.json", "resources/instructions/control_runtime.json",
    "resources/instructions/mitsubishi/fx3u_step_widths.json",
)

WEB_ROOT = (
    "index.html", "simulator.html", "package.json", "package-lock.json",
    "openapi.json", "tsconfig.json", "vite.config.ts", "LICENSE.md",
)

TEMPLATE_NAMES = {
    "blank-fbd-projects.zip.xz", "fx3u.gxw", "nodes.json", "provenance.json",
    "q02-token-grammar.json", "q03udv-token-grammar.json",
    "fx-token-encoder-forms.json",
    "q-token-encoder-forms.json",
}
STRUCTURED_CATALOGS = (
    "common.json", "fx3u.json", "fx5u.json", "fx3u_verified_opcodes.json",
    "modifier_rules.json", "instruction_capabilities.json",
)


def candidates(root: Path):
    for name in ROOT_FILES:
        yield root / name
    for name in WEB_ROOT:
        yield root / "web" / name
    for folder, suffixes in (
        ("src", {".py"}),
        ("web/src", {".ts", ".tsx", ".css"}),
        ("web/tests", {".mjs"}),
        ("native_adapters", {".cs", ".manifest"}),
        ("hardware_reader", {".cs", ".md"}),
        ("simulator_gateway", {".cs", ".md"}),
        ("resources/model_catalog", {".json"}),
        ("resources/locales", {".json"}),
    ):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and path.suffix in suffixes:
                yield path
    for name in sorted(TEMPLATE_NAMES):
        yield root / "src/gxw/templates" / name
    for name in STRUCTURED_CATALOGS:
        yield root / "resources/instructions/mitsubishi" / name


def safe_template_json(path: Path) -> bytes:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("source_evidence", "evidence", "float32_evidence",
                "dll_sha256", "instruction_lookup_rva", "instruction_text_rva",
                "operand_text_rva"):
        data.pop(key, None)
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def safe_native_encoding(path: Path) -> bytes:
    source = json.loads(path.read_text(encoding="utf-8"))
    data = {key: source[key] for key in ("schema_version", "opcodes",
            "supplemental_encodings", "lexical_overrides", "step_width_profile")}
    profile = data["step_width_profile"]
    profile.pop("description", None)
    for row in (*profile.get("rules", []), *profile.get("exact_forms", [])):
        if "evidence" in row:
            row["evidence"] = "native encoding observation"
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def safe_structured_catalog(path: Path) -> bytes:
    def project(value):
        if isinstance(value, list):
            return [project(item) for item in value]
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items()
                    if key not in {"description", "notes", "reference"}}
        return value
    data = project(json.loads(path.read_text(encoding="utf-8")))
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.destination.resolve()
    if destination == root or root in destination.parents:
        parser.error("destination must be outside the private source tree")
    if destination.exists() and any(destination.iterdir()):
        parser.error("destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    names = set()
    for path in candidates(root):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"missing or linked source file: {path}")
        relative = path.relative_to(root)
        name = relative.as_posix()
        if name in names:
            raise RuntimeError(f"duplicate export path: {name}")
        names.add(name)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith("src/gxw/templates/") and path.suffix == ".json":
            target.write_bytes(safe_template_json(path))
        elif name == "resources/instructions/mitsubishi/fx3u_step_widths.json":
            target.write_bytes(safe_native_encoding(path))
        elif name.startswith("resources/instructions/mitsubishi/"):
            target.write_bytes(safe_structured_catalog(path))
        else:
            shutil.copyfile(path, target)
    print(f"Exported {len(names)} reviewed source paths to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
