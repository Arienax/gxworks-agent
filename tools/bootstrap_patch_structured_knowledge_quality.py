#!/usr/bin/env python3
"""One-shot patcher for structured knowledge extraction quality."""
from __future__ import annotations
from pathlib import Path
import re

path = Path('tools/build_fx3u_knowledge_v3.py')
text = path.read_text(encoding='utf-8')
original = text

text, count = re.subn(r'BUILDER_VERSION = "3\.0\.2"', 'BUILDER_VERSION = "3.0.3"', text, count=1)
assert count == 1, f'builder version replacement count={count}'

old = '''    for match in DEVICE_RANGE_RE.finditer(text):
        first_prefix = match.group(1).upper()
        second_prefix = (match.group(3) or first_prefix).upper()
        first_token = f"{first_prefix}{match.group(2)}"
        second_token = f"{second_prefix}{match.group(4)}"
        if (
            first_prefix == second_prefix == "S"
            and chunk_type == "instruction"
            and not _explicit_state_relay_context(text, first_token)
            and not _explicit_state_relay_context(text, second_token)
        ):
            entities[(first_token, "operand_placeholder")] += 1
            entities[(second_token, "operand_placeholder")] += 1
            continue
        entity = f"{first_prefix}{match.group(2)}-{second_prefix}{match.group(4)}"
        entities[(entity, "device_range")] += 1
'''
new = '''    for match in DEVICE_RANGE_RE.finditer(text):
        first_prefix = match.group(1).upper()
        second_prefix = (match.group(3) or first_prefix).upper()
        first_token = f"{first_prefix}{match.group(2)}"
        second_token = f"{second_prefix}{match.group(4)}"
        # A real PLC device range cannot change device family.  Text such as
        # ``D8360-Y003`` is normally two table/prose cells flattened around a
        # dash and must not become one device_range entity.
        if first_prefix != second_prefix:
            continue
        if (
            first_prefix in {"S", "D", "N", "M", "P"}
            and chunk_type == "instruction"
            and _operand_placeholder_context(text, match.start(), match.end())
            and (
                first_prefix != "S"
                or (
                    not _explicit_state_relay_context(text, first_token)
                    and not _explicit_state_relay_context(text, second_token)
                )
            )
        ):
            entities[(first_token, "operand_placeholder")] += 1
            entities[(second_token, "operand_placeholder")] += 1
            continue
        entity = f"{first_prefix}{match.group(2)}-{second_prefix}{match.group(4)}"
        entities[(entity, "device_range")] += 1
'''
assert text.count(old) == 1, 'range extraction block not found exactly once'
text = text.replace(old, new, 1)

old = '''        if prefix == "S" and int(match.group(2)) > 0:
            is_operand_context = chunk_type == "instruction" or _operand_placeholder_context(
                text, match.start(), match.end()
            )
            if is_operand_context and not _explicit_state_relay_context(text, entity):
                entities[(entity, "operand_placeholder")] += 1
                continue
        entities[(entity, "device")] += 1
'''
new = '''        if prefix in {"S", "D", "N", "M", "P"} and int(match.group(2)) > 0:
            is_operand_context = (
                chunk_type == "instruction"
                and _operand_placeholder_context(text, match.start(), match.end())
            )
            if (
                is_operand_context
                and (prefix != "S" or not _explicit_state_relay_context(text, entity))
            ):
                entities[(entity, "operand_placeholder")] += 1
                continue
        entities[(entity, "device")] += 1
'''
assert text.count(old) == 1, 'device extraction block not found exactly once'
text = text.replace(old, new, 1)

old = '''        elif re.fullmatch(r"S[1-9]\\d*", entity) and (
            chunk_type == "instruction"
            or _operand_placeholder_context(text, 0, len(text))
        ) and not _explicit_state_relay_context(text, entity):
            kind = "operand_placeholder"
'''
new = '''        elif re.fullmatch(r"[SDNMP][1-9]\\d*", entity) and (
            chunk_type == "instruction"
            and _operand_placeholder_context(text, 0, len(text))
        ) and (
            not entity.startswith("S")
            or not _explicit_state_relay_context(text, entity)
        ):
            kind = "operand_placeholder"
'''
assert text.count(old) == 1, 'explicit entity operand block not found exactly once'
text = text.replace(old, new, 1)

old = '''def instruction_completion_flags(pages: list[PageArtifact]) -> list[str]:
    flags: list[str] = []
    for page in pages:
        text = page.clean_text
        for match in re.finditer(r"\\bM8\\d{3}\\b", text, flags=re.I):
            window = text[max(0, match.start() - 100) : match.end() + 140]
            if re.search(r"complete|completion|flag|finished", window, flags=re.I):
                value = match.group(0).upper()
                if value not in flags:
                    flags.append(value)
    return flags
'''
new = '''def instruction_completion_flags(pages: list[PageArtifact]) -> list[str]:
    """Return relays explicitly described as instruction completion flags.

    A generic occurrence of the word ``flag`` is intentionally insufficient:
    Mitsubishi instruction pages also contain zero, carry, borrow, error,
    busy/ready, limit and control flags.  Relay-local line segments keep an
    adjacent status row from inheriting the semantics of a completion row.
    """
    completion_semantics = re.compile(
        r"\\b(?:instruction\\s+)?execution\\s+complete(?:d)?\\b|"
        r"\\bexecution\\s+completion\\s+(?:flag|relay)\\b|"
        r"\\b(?:instruction|operation)\\s+completion\\s+(?:flag|relay)\\b|"
        r"\\bcompletion\\s+(?:flag|relay)\\b|"
        r"\\b(?:instruction|operation)\\s+(?:is\\s+)?finished\\b|"
        r"\\bfinished\\s+(?:flag|relay)\\b",
        flags=re.I,
    )
    relay_re = re.compile(r"\\bM8\\d{3}\\b", flags=re.I)
    flags: list[str] = []
    for page in pages:
        lines = page.clean_text.splitlines()
        for line_index, raw_line in enumerate(lines):
            line = normalize_line(raw_line)
            matches = list(relay_re.finditer(line))
            for index, match in enumerate(matches):
                left = 0 if index == 0 else (matches[index - 1].end() + match.start()) // 2
                right = len(line) if index + 1 == len(matches) else (match.end() + matches[index + 1].start()) // 2
                context = line[left:right]
                if not completion_semantics.search(context):
                    neighbors: list[str] = [context]
                    if line_index > 0:
                        previous = normalize_line(lines[line_index - 1])
                        if not relay_re.search(previous):
                            neighbors.insert(0, previous)
                    if line_index + 1 < len(lines):
                        following = normalize_line(lines[line_index + 1])
                        if not relay_re.search(following):
                            neighbors.append(following)
                    context = " ".join(value for value in neighbors if value)
                if completion_semantics.search(context):
                    value = match.group(0).upper()
                    if value not in flags:
                        flags.append(value)
    return flags
'''
assert text.count(old) == 1, 'completion flag parser block not found exactly once'
text = text.replace(old, new, 1)

assert text != original
path.write_text(text, encoding='utf-8')
print('patched', path)
print('builder_version=3.0.3')
