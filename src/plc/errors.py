# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Errors shared by deterministic patch validation and artifact delivery."""

class DebugLoopError(RuntimeError):
    """A deterministic Debug/Patch boundary rejected an operation."""
