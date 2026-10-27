# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""GX Works2 parsing and template-backed offline project writing."""

from .connectivity import (
    ConnectivityGraph,
    ConnectivityNet,
    PortRef,
    build_connectivity_graph,
)
from .models import (
    GXWFormatError,
    NodeKind,
    Point,
    PortDescriptor,
    Rect,
    StructuredBlock,
    StructuredNode,
    StructuredProgram,
    StructuredWire,
)
from .project_resolver import GXWProjectResolver
from .semantic import (
    CoilRole,
    ContactPolarity,
    LadderEdge,
    DEFAULT_FUNCTION_BLOCK_REGISTRY,
    DEFAULT_FUNCTION_FAMILY_REGISTRY,
    FunctionFamilySpec,
    FunctionBlockCategory,
    FunctionBlockPortSpec,
    FunctionBlockSpec,
    SemanticCoil,
    SemanticContact,
    SemanticFunction,
    SemanticFunctionBlock,
    SemanticFunctionBlockPort,
    SemanticFunctionPort,
    SemanticIssue,
    SemanticIssueSeverity,
    SemanticLadderPort,
    SemanticPortRole,
    SemanticTerminal,
    StructuredSemanticModel,
    TerminalRole,
    UnmodeledNodeRef,
    build_semantic_model,
)
from .structured_pou import parse_structured_pou
from .structured_writer import structured_from_ladder


def __getattr__(name):
    # Adapter observation modules import the model types before project writing
    # is available. Resolve writer exports on demand to keep that import acyclic.
    if name in {"ProjectWriteResult", "build_gxw_project", "write_gxw_project"}:
        from importlib import import_module

        value = getattr(import_module(".project_writer", __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def read_structured_program(*args, **kwargs):
    from .decoder import read_structured_program as _read_structured_program

    return _read_structured_program(*args, **kwargs)


def describe_program(*args, **kwargs):
    from .decoder import describe_program as _describe_program

    return _describe_program(*args, **kwargs)


__all__ = [
    "ProjectWriteResult",
    "build_gxw_project",
    "write_gxw_project",
    "structured_from_ladder",
    "CoilRole",
    "ConnectivityGraph",
    "ConnectivityNet",
    "ContactPolarity",
    "LadderEdge",
    "DEFAULT_FUNCTION_BLOCK_REGISTRY",
    "DEFAULT_FUNCTION_FAMILY_REGISTRY",
    "FunctionFamilySpec",
    "FunctionBlockCategory",
    "FunctionBlockPortSpec",
    "FunctionBlockSpec",
    "GXWFormatError",
    "GXWProjectResolver",
    "NodeKind",
    "Point",
    "PortDescriptor",
    "PortRef",
    "Rect",
    "SemanticCoil",
    "SemanticContact",
    "SemanticFunction",
    "SemanticFunctionBlock",
    "SemanticFunctionBlockPort",
    "SemanticFunctionPort",
    "SemanticIssue",
    "SemanticIssueSeverity",
    "SemanticLadderPort",
    "SemanticPortRole",
    "SemanticTerminal",
    "StructuredNode",
    "StructuredBlock",
    "StructuredProgram",
    "StructuredSemanticModel",
    "StructuredWire",
    "TerminalRole",
    "UnmodeledNodeRef",
    "build_connectivity_graph",
    "build_semantic_model",
    "describe_program",
    "parse_structured_pou",
    "read_structured_program",
]
