# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Shared program storage rules: allocation context and operand representation.

What lives here is the part Core, CSV planning and the GXW writers must agree on:
the project-bound allocation context, the encoded-operand view of a device, and the
rules that decide whether a stored width can follow the saved D/W allocation. Parsing
GX Works2 project bytes and writing token headers stay in the GXW layer.
"""
