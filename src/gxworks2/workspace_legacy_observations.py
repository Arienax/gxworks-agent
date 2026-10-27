# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Explicit offline decoding of immutable workspace receipts from before v3.

Live transports must never import this module. Conversion copies documents and
recognizes the recorded operation together with its exact historical origin.
Already structured evidence is retained, including invalid evidence that Core
must reject. Original archive bytes remain unchanged.
"""
from __future__ import annotations

from .workspace_observations import EvidenceOrigin


def decode_frozen_workspace_evidence(document):
    """Return a current evidence view of a frozen legacy JSON document."""
    descriptions = {
        'NativeCodeLexicalRead': (EvidenceOrigin.CODE_LEXICAL_READ,
            'original ChangePToILcode and GetStepSize; retained generated primary bytes after check; not execution'),
        'OwnedCheckInput': (EvidenceOrigin.OWNED_CHECK_INPUT,
            'original ProcessManager.ProgramCheck owned code copy; read-only; no interception'),
        'NativeBodyRead': (EvidenceOrigin.SOURCE_BODY,
            'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'),
        'NativeSfcChildRead': (EvidenceOrigin.SOURCE_BODY,
            'original Workspace.GetPOUBodyData(1692); source bytes, not compiled PCode'),
        'NativeSourceReferences': (EvidenceOrigin.SOURCE_REFERENCES,
            'original CreateProgramAnalysis3 and GetProgramAnalysis3; current compilation; 88-byte public records'),
        'NativeTaskSelection': (EvidenceOrigin.TASK_SELECTION,
            'original Workspace collection, child, type, name and parent reads before compilation'),
    }
    framings = {
        'observed SFC length prefix; remaining header bytes uninterpreted': EvidenceOrigin.SFC_LENGTH_PREFIX,
        'original checker envelope helper; optional tables omitted; read-only': EvidenceOrigin.STRUCTURED_CHECK_ENVELOPE,
    }

    def decode(value, *, framing=False):
        if isinstance(value, list):
            return [decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: decode(item, framing=key == 'framing') for key, item in value.items()}
        if 'evidence' not in result:
            operation, note = result.get('operation'), result.get('provenance')
            entry = descriptions.get(operation) if isinstance(operation, str) else None
            origin = (entry[0] if entry and note == entry[1] else
                      framings.get(note) if framing and isinstance(note, str) else None)
            if origin is not None:
                result['evidence'] = {'schema_version': 1, 'origin': origin.value}
        # The outer wire version is independent of the retained internal receipt
        # schema, and only historical wire v2 is normalized by this offline API.
        workspace_receipt = (isinstance(result.get('validation'), dict)
            or {'compile', 'check', 'creation', 'calls', 'cpu', 'native_version'}.issubset(result))
        if (type(result.get('protocol_version')) is int and result['protocol_version'] == 2
                and workspace_receipt):
            result['protocol_version'] = 1
        return result

    return decode(document)
