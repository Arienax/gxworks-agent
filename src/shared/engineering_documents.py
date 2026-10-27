# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Owner-selected engineering data locations within public documents.

These paths describe data contracts, never claims supplied by a payload. A
transport or application owner selects the document kind before traversal.
"""
from __future__ import annotations
import copy
from collections.abc import Mapping


_PROGRAM_MAPS = (("devices",), ("io_map",), ("device_comments",), ("variables",),
                 ("structs",), ("metadata",),
                 ("declaration_edits", "*", "renames"),
                 ("timing", "performance", "region_worst_ms"),
                 ("timing", "performance", "scan_budget", "allocation_ms"),
                 ("analysis", "config", "terminal_states"), ("analysis", "config", "timing", "allocation"),
                 ("analysis", "dependency_graph", "forward"), ("analysis", "dependency_graph", "reverse"))
_LABEL_TEXT_FIELDS = ("initial_value", "comment")
_PROGRAM_MAPS += tuple((*prefix, field)
    for prefix in (("labels", "*", "*"), ("declaration_edits", "*", "upserts", "*"))
    for field in _LABEL_TEXT_FIELDS)
_SPEC_MAPS = (("metadata",), ("extensions",), ("variables",), ("structs",),
              ("io_table", "*", "metadata"), ("parameters", "*", "value"),
              ("parameters", "*", "suggested_default"), ("parameters", "*", "required_when"),
              ("behavior_constraints", "*", "values"), ("operation_intents", "*", "parameters"),
              ("operation_intents", "*", "execution", "required_state"))
DATA_PATHS = {
    "program": _PROGRAM_MAPS,
    "spec": _SPEC_MAPS,
    "diagnostics": (("variables",), ("values",), ("findings", "*", "details")),
    "editor": tuple(("model", *path) for path in _PROGRAM_MAPS)
              + (("declaration_tables",),) + tuple(("presentation", "rows", "*", "*", field)
                  for field in _LABEL_TEXT_FIELDS),
    "job_output": tuple((field, *path) for field in ("analysis", "spec_draft") for path in _SPEC_MAPS),
    "simulation": (("initial",), ("set",), ("values",), ("metadata",),
                   ("suite", "tests", "*", "metadata"), ("suite", "tests", "*", "initial"),
                   ("suite", "tests", "*", "steps", "*", "set"), ("requirement_links",)),
}
DATA_PATHS["simulation"] += (("result", "results", "*", "trace", "*", "values"),
    ("result", "results", "*", "invariant_violations", "*", "values"),
    ("cases", "*", "invariant_violations", "*", "values"),
    ("cases", "*", "observations", "*", "values"),
    ("timeline", "samples", "*", "values"), ("timeline", "latest", "values"),
    ("timeline", "events", "*", "values"), ("timeline", "events", "*", "raw"))
DATA_PATHS["simulation_workbench"] = tuple(("plans", "*", *path) for path in DATA_PATHS["simulation"])
DATA_PATHS["delivery"] = (("device_io",),) + tuple(
    ("confirmed_spec", *path) for path in _SPEC_MAPS)
DATA_PATHS["source_snapshot"] = tuple(("model", *path) for path in _PROGRAM_MAPS)
DATA_PATHS["source_snapshot"] += tuple((*prefix, *path)
    for prefix in (("definitions", "*", "model"), ("definition", "model")) for path in _PROGRAM_MAPS)
DATA_PATHS["catalog"] = (("declaration_tables",),) + tuple(("empty_model", *path) for path in _PROGRAM_MAPS)
_PARAMETER_MAPS = (("declaration_edits", "*", "renames"), ("networks", "*", "calls", "*", "arguments"),
                   ("networks", "*", "nodes", "*", "arguments"))
_PARAMETER_MAPS += (("networks", "*", "source_block"),
                    ("preserved_blocks", "*", "source_block"),
                    ("preserved_blocks", "*", "source_offset"))
_PARAMETER_MAPS += tuple(("declaration_edits", "*", "upserts", "*", field) for field in _LABEL_TEXT_FIELDS)
DATA_PATHS["catalog"] += (("parameter_schema",),) + tuple(
    (model, *path) for model in ("parameter_model", "empty_parameter_model") for path in _PARAMETER_MAPS)
DATA_PATHS["catalog"] += (("project_configuration", "system_variable_ranges"),) + tuple(
    ("project_configuration", "gui_configuration", field)
    for field in ("workspace_ranges", "effective_ranges", "stored_ranges"))

# These maps have user-owned names but resource-owned values. Keep their names
# while still projecting private metadata and diagnostic text inside each value.
_PROGRAM_RECORDS = (("declaration_edits",), ("nodes", "*", "port_edits"), ("labels",),
                    ("labels", "*", "*", "member_devices"),
                    ("declaration_edits", "*", "upserts", "*", "member_devices"),
                    ("timing", "timers"), ("timing", "counters"),
                    ("analysis", "dependency_graph", "writers"), ("analysis", "dependency_graph", "readers"))
NAMED_RECORD_PATHS = {
    "program": _PROGRAM_RECORDS,
    "editor": tuple(("model", *path) for path in _PROGRAM_RECORDS)
              + (("presentation", "rows"), ("presentation", "rows", "*", "*", "member_devices")),
    "source_snapshot": tuple(("model", *path) for path in _PROGRAM_RECORDS),
    "catalog": tuple(("empty_model", *path) for path in _PROGRAM_RECORDS),
    "simulation": (("timeline", "samples", "*", "errors"), ("timeline", "latest", "errors")),
}
NAMED_RECORD_PATHS["catalog"] += tuple(
    (model, *path) for model in ("parameter_model", "empty_parameter_model")
    for path in (("declaration_edits",), ("networks", "*", "nodes", "*", "port_edits")))
NAMED_RECORD_PATHS["catalog"] += tuple(
    ("project_configuration", "gui_configuration", field) for field in ("differences", "retained_fields"))
NAMED_RECORD_PATHS["source_snapshot"] += tuple((*prefix, *path)
    for prefix in (("definitions", "*", "model"), ("definition", "model")) for path in _PROGRAM_RECORDS)
_DEFINITION_RECORDS = (("declarations", "*", "member_devices"), ("members", "*", "member_devices"))
_DEFINITION_DATA = tuple((collection, "*", field) for collection in ("declarations", "members")
                         for field in _LABEL_TEXT_FIELDS) + (("st_text",),)
NAMED_RECORD_PATHS["source_snapshot"] += tuple((*prefix, *path)
    for prefix in (("definitions", "*"), ("definition",)) for path in _DEFINITION_RECORDS)
DATA_PATHS["source_snapshot"] += tuple((*prefix, *path)
    for prefix in (("definitions", "*"), ("definition",)) for path in _DEFINITION_DATA)
DATA_PATHS["generation_context"] = tuple(
    (field, *path) for field in ("confirmed_spec", "user_facts") for path in _SPEC_MAPS)
DATA_PATHS["generation_context"] += tuple(("catalog", *path) for path in DATA_PATHS["catalog"])
DATA_PATHS["generation_context"] += tuple((*prefix, *path)
    for prefix in (("current_model",), ("generation_input", "previous"))
    for path in (*_PROGRAM_MAPS, *_PARAMETER_MAPS))
DATA_PATHS["generation_context"] += (("generation_input", "parameter_schema"),
    ("output_contract", "schema"), ("output_contract", "model_schema"),
    ("output_contract", "alternatives", "*", "model_schema"),
    ("generation_input", "declaration_tables"))
NAMED_RECORD_PATHS["generation_context"] = tuple(
    ("catalog", *path) for path in NAMED_RECORD_PATHS["catalog"])
NAMED_RECORD_PATHS["generation_context"] += tuple((*prefix, *path)
    for prefix in (("current_model",), ("generation_input", "previous"))
    for path in (*_PROGRAM_RECORDS, ("networks", "*", "nodes", "*", "port_edits")))

# FBD generation carries declarations and caller graphs in addition to the
# selected graph. Their user-owned names retain the same data/metadata boundary.
_FBD_CALLER = ("generation_input", "source_context", "fbd_callers", "*", "parameter_model")
DATA_PATHS["generation_context"] += tuple((*_FBD_CALLER, *path)
    for path in (*_PROGRAM_MAPS, *_PARAMETER_MAPS))
NAMED_RECORD_PATHS["generation_context"] += tuple((*_FBD_CALLER, *path)
    for path in (*_PROGRAM_RECORDS, ("networks", "*", "nodes", "*", "port_edits")))
_FBD_DECLARATION_MAPS = (("generation_input", "existing_declarations"),
    ("generation_input", "call_interface"), ("generation_input", "structures"),
    ("generation_input", "source_context", "declarations"),
    ("generation_input", "source_context", "structures"))
NAMED_RECORD_PATHS["generation_context"] += _FBD_DECLARATION_MAPS
_FBD_DECLARATION_ROWS = tuple((*path, "*", "*") for path in _FBD_DECLARATION_MAPS) + (
    ("generation_input", "source_context", "st_sources", "*", "declarations", "*"),)
DATA_PATHS["generation_context"] += tuple((*path, field)
    for path in _FBD_DECLARATION_ROWS for field in _LABEL_TEXT_FIELDS)
NAMED_RECORD_PATHS["generation_context"] += tuple((*path, "member_devices")
    for path in _FBD_DECLARATION_ROWS)

# A diff carries the same field owners as its corresponding source document.
DATA_PATHS["preview_diff"] = tuple(("property_changes", path[0], side, *path[1:])
    for side in ("before", "after") for path in _PROGRAM_MAPS)
NAMED_RECORD_PATHS["preview_diff"] = tuple(("property_changes", path[0], side, *path[1:])
    for side in ("before", "after") for path in _PROGRAM_RECORDS)
for side in ("before", "after"):
    prefix = ("definition_changes", side)
    DATA_PATHS["preview_diff"] += tuple((*prefix, "model", *path) for path in _PROGRAM_MAPS)
    DATA_PATHS["preview_diff"] += tuple((*prefix, *path) for path in _DEFINITION_DATA)
    NAMED_RECORD_PATHS["preview_diff"] += tuple((*prefix, "model", *path) for path in _PROGRAM_RECORDS)
    NAMED_RECORD_PATHS["preview_diff"] += tuple((*prefix, *path) for path in _DEFINITION_RECORDS)
DATA_PATHS["delivery"] += tuple(("changes", *path) for path in DATA_PATHS["preview_diff"])
NAMED_RECORD_PATHS["delivery"] = (("fbd_declarations",), ("fbd_declarations", "*", "*", "member_devices"))
DATA_PATHS["delivery"] += tuple(("fbd_declarations", "*", "*", field) for field in _LABEL_TEXT_FIELDS)
NAMED_RECORD_PATHS["delivery"] += tuple(("changes", *path) for path in NAMED_RECORD_PATHS["preview_diff"])


def _matches(path, patterns):
    return any(len(pattern) == len(path) and all(a == "*" or a == b for a, b in zip(pattern, path))
               for pattern in patterns)


def project_document(value, *, kind, field_allowed, project_text=lambda text: text):
    paths = DATA_PATHS[kind]
    named_records = NAMED_RECORD_PATHS.get(kind, ())

    def walk(item, path):
        if _matches(path, paths):
            return copy.deepcopy(item)
        if isinstance(item, Mapping):
            return {str(key): walk(nested, (*path, str(key))) for key, nested in item.items()
                    if _matches(path, named_records) or field_allowed(key)}
        if isinstance(item, (list, tuple)):
            return [walk(nested, (*path, "*")) for nested in item]
        if isinstance(item, str):
            return project_text(item)
        return copy.deepcopy(item)
    return walk(value, ())


def restore_document_data(projected, original, *, kind, project_metadata):
    """Retain only declared data leaves after a second metadata projection."""
    paths = DATA_PATHS[kind]
    named_records = NAMED_RECORD_PATHS.get(kind, ())
    def walk(current, source, path):
        if _matches(path, paths):
            return copy.deepcopy(source)
        if isinstance(current, Mapping) and isinstance(source, Mapping):
            if _matches(path, named_records):
                return {**current, **{key: walk(
                    current[key] if key in current else project_metadata(value), value, (*path, str(key)))
                    for key, value in source.items()}}
            result = {key: walk(value, source[key], (*path, str(key))) if key in source else value
                      for key, value in current.items()}
            for key, value in source.items():
                if key not in result and _matches((*path, str(key)), paths):
                    result[key] = copy.deepcopy(value)
            return result
        if isinstance(current, list) and isinstance(source, (list, tuple)):
            return [walk(value, source[index], (*path, "*")) for index, value in enumerate(current)]
        return current
    return walk(projected, original, ())
