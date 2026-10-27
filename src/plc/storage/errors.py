# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""The computation error every storage rule raises.

Project-file writers convert it to their own format error at their boundary, so the
shared rules never import a project byte reader or writer.
"""
from __future__ import annotations


class StorageRuleError(ValueError):
    """A storage form or encoding this rule set cannot describe."""

class StorageFramingError(StorageRuleError):
    """An encoded operand whose token framing could not be read."""

