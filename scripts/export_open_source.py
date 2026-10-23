"""Export source while separating research materials and development output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from publication.source_policy import source_files


# Known format names remain importable by existing runtime-format tests.
# They do not control which files may be exported.
TEMPLATE_NAMES = {
    "blank-fbd-projects.zip.xz", "blank-ladder-projects.zip.xz",
    "fx3u.gxw", "nodes.json", "provenance.json",
    "q02-token-grammar.json", "q03udv-token-grammar.json",
    "fx-token-encoder-forms.json", "q-token-encoder-forms.json",
}
STRUCTURED_CATALOGS = (
    "common.json", "fx3u.json", "fx5u.json", "fx3u_verified_opcodes.json",
    "modifier_rules.json", "instruction_capabilities.json",
    "pwm_reviewed_facts.json", "pwm_contract.generated.json",
)


def candidates(root: Path):
    yield from source_files(root)


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


def export_source(root: Path, destination: Path) -> int:
    root, destination = root.resolve(), destination.resolve()
    if destination == root or root in destination.parents:
        raise ValueError("destination must be outside the development source tree")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in candidates(root):
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"missing or linked source file: {path}")
        relative = path.relative_to(root)
        name = relative.as_posix()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if name.startswith("src/gxw/templates/") and path.suffix == ".json":
            target.write_bytes(safe_template_json(path))
        elif name == "resources/instructions/mitsubishi/fx3u_step_widths.json":
            target.write_bytes(safe_native_encoding(path))
        elif name.startswith("resources/instructions/mitsubishi/") and path.suffix == ".json":
            target.write_bytes(safe_structured_catalog(path))
        else:
            shutil.copyfile(path, target)
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = export_source(ROOT, args.destination)
    except ValueError as error:
        parser.error(str(error))
    print(f"Exported {count} source paths to {args.destination}; research materials excluded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
