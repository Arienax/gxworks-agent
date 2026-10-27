# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Compare the local and the published step-width resource by semantics.

The development checkout keeps fuller provenance prose than the published copy of
``resources/instructions/mitsubishi/fx3u_step_widths.json``. Every field that can
change an answer must therefore be compared, not just the resource as a file:
native header associations, scopes and their normalised meaning, instruction
forms, operand positions, address ranges, fixed and variable width formulas,
aliases, rule precedence, gap handling and the stable evidence identifiers.

Prose that the publication rules allow to differ is normalised to one marker, and
keys that exist only in the development copy are reported instead of compared.
The tool reads the published copy from a path or from a Git revision, so it runs
before as well as after a commit, and it exits non-zero on any semantic drift.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

RESOURCE = "resources/instructions/mitsubishi/fx3u_step_widths.json"

#: Fields the publication rules remove from the public copy (provenance detail).
PRIVATE_ONLY = (
    "evidence",
    "dll_sha256",
    "cpu_code",
    "reproducer",
    "evidence_files",
    "supplemental_evidence",
    "original_native_catalog_git_blob",
)

EVIDENCE_MARKER = "<evidence>"


def _load_source(source: str, repo: str | None = None) -> Dict[str, Any]:
    """One resource document from a filesystem path or ``git:<revision>:<path>``."""
    if source.startswith("git:"):
        _, revision, path = source.split(":", 2)
        raw = subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=repo)
        return json.loads(raw.decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _normalise(node: Any) -> Any:
    """Drop prose, keep every field that can change a width answer."""
    if isinstance(node, Mapping):
        return {key: (EVIDENCE_MARKER if key == "evidence" else _normalise(value))
                for key, value in sorted(node.items()) if key not in PRIVATE_ONLY}
    if isinstance(node, list):
        return [_normalise(item) for item in node]
    return node


def _scopes(document: Mapping[str, Any]) -> Dict[str, Tuple[Any, ...]]:
    """Normalised scope of every entry that declares one, keyed by its identity."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from plc.instruction_steps import _scope_spec

    profile = document.get("step_width_profile") or {}
    result: Dict[str, Tuple[Any, ...]] = {}
    for kind, entries in (("rule", profile.get("rules") or ()),
                          ("variable", profile.get("variable_forms") or ()),
                          ("exact", profile.get("exact_forms") or ()),
                          ("range", profile.get("device_ranges") or ())):
        for entry in entries:
            label = kind + ":" + "/".join(str(item) for item in
                                          entry.get("opcodes") or [entry.get("opcode") or ""])
            label += ":" + str(entry.get("operands") or entry.get("devices") or "")
            models, series, family = _scope_spec(dict(entry), label)
            result[label] = (models, series, family)
    declared = profile.get("observation_scopes") or {}
    if declared:
        for name in ("default", *(declared.get("overrides") or {})):
            spec = declared["default"] if name == "default" else declared["overrides"][name]
            result["observation:" + name] = _scope_spec(dict(spec), "observation " + name)
    return result


def _differences(local: Any, public: Any, path: str = "") -> List[str]:
    """Every place where the published copy is not the development copy."""
    problems: List[str] = []
    if isinstance(local, Mapping) and isinstance(public, Mapping):
        for key in sorted(set(public) - set(local)):
            problems.append(f"{path}/{key}: only in the published copy")
        for key in sorted(set(local) - set(public)):
            if key in PRIVATE_ONLY:
                continue
            problems.append(f"{path}/{key}: missing from the published copy")
        for key in sorted(set(local) & set(public)):
            problems.extend(_differences(local[key], public[key], f"{path}/{key}"))
        return problems
    if isinstance(local, list) and isinstance(public, list):
        if len(local) != len(public):
            problems.append(f"{path}: {len(local)} entries locally, {len(public)} published")
            return problems
        for index, (left, right) in enumerate(zip(local, public)):
            problems.extend(_differences(left, right, f"{path}[{index}]"))
        return problems
    if local != public:
        problems.append(f"{path}: {local!r} locally, {public!r} published")
    return problems


#: Answers compared between the two copies: an in-scope form, the device-table
#: boundaries, an out-of-scope series and a form whose scope the declaration
#: decides.
PROBE_MODELS = ("FX3U", "FX3G", "FX2N", "FX1S")
PROBES = (
    ("LD", ("M0",)), ("LD", ("M1536",)), ("LD", ("M3584",)), ("LD", ("M8256",)),
    ("LD", ("S1024",)), ("LDP", ("M3584",)), ("OUT", ("Y0",)), ("SFTL", ("D0",)),
    ("MC", ("N0", "M0")), ("MC", ("N0", "M3584")), ("MOV", ("D0", "D1")),
    ("DADD", ("D0", "D2", "D4")), ("NEXT", ()), ("END", ()), ("MCR", ("N0",)),
)


def _answers(document: Mapping[str, Any],
             encodings: Mapping[str, Tuple[str, int]]) -> Dict[str, Tuple[Any, ...]]:
    """Instance and mnemonic answers for the probe set, keyed by target and form."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from plc.instruction_steps import StepWidthCatalog

    catalogue = StepWidthCatalog(encodings, dict(document["step_width_profile"]))
    answers: Dict[str, Tuple[Any, ...]] = {}
    for model in PROBE_MODELS:
        for opcode, operands in PROBES:
            instance = catalogue.resolve(opcode, list(operands), plc_model=model)
            mnemonic = catalogue.mnemonic_width(opcode, plc_model=model)
            answers[f"{model} {opcode} {' '.join(operands)}".strip()] = (
                instance.status, instance.steps, mnemonic.status, mnemonic.steps)
    return answers


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", default=RESOURCE,
                        help="development copy of the resource (default: the working file)")
    parser.add_argument("--public", default="git:HEAD:" + RESOURCE,
                        help="published copy, as a path or git:<revision>:<path>")
    parser.add_argument("--repo", default=".",
                        help="repository the git revision is read from")
    arguments = parser.parse_args(argv)

    local_document = _load_source(arguments.local)
    public_document = _load_source(arguments.public, arguments.repo)
    problems = _differences(_normalise(local_document), _normalise(public_document))

    local_scopes = _scopes(local_document)
    public_scopes = {key: value for key, value in _scopes(public_document).items()}
    for key in sorted(public_scopes):
        if key not in local_scopes:
            problems.append(f"scope {key}: only in the published copy")
        elif local_scopes[key] != public_scopes[key]:
            problems.append(f"scope {key}: {local_scopes[key]} locally, "
                            f"{public_scopes[key]} published")

    opcodes_local = local_document.get("opcodes") or {}
    opcodes_public = public_document.get("opcodes") or {}
    if opcodes_local != opcodes_public:
        problems.append("opcodes: the native encoding map differs")

    encodings = {header: (entry[0], entry[1]) for header, entry in opcodes_local.items()}
    local_answers = _answers(local_document, encodings)
    public_answers = _answers(public_document, encodings)
    for key in sorted(local_answers):
        if local_answers[key] != public_answers.get(key):
            problems.append(f"answer {key}: {local_answers[key]} locally, "
                            f"{public_answers.get(key)} published")

    if problems:
        print("Step-width resource semantics differ:")
        for problem in problems:
            print("  " + problem)
        return 1
    print(f"Step-width resource matches: {len(opcodes_local)} encodings, "
          f"{len(local_scopes)} scoped entries, {len(local_answers)} probe answers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
