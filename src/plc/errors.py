"""Errors shared by deterministic patch validation and artifact delivery."""

class DebugLoopError(RuntimeError):
    """A deterministic Debug/Patch boundary rejected an operation."""

