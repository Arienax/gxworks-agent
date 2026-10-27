"""Explicit JSON entry point for frozen native receipts; never used by transports."""
from __future__ import annotations

import json

from gxworks2.workspace_legacy_observations import decode_frozen_workspace_evidence


def load_frozen_native_json(payload, *args, **kwargs):
    """Decode immutable recorded evidence before a test mutates its current view."""
    return decode_frozen_workspace_evidence(json.loads(payload, *args, **kwargs))
