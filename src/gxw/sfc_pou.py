"""Bounded SFC source records with raw-preserved graph and child programs.

The envelope and child ownership were checked against native frontend records.
Graph references describe source bindings, not SFC execution semantics. Unknown
storage modes, graph instructions and registration qualifiers remain explicit.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .models import GXWFormatError
from .token_pou import TokenProgram, parse_token_fragment


@dataclass(frozen=True)
class SfcSource:
    raw: bytes
    layout: dict
    graph_tokens: TokenProgram | None
    graph_records: tuple[dict, ...]
    diagnostics: tuple[str, ...]

    def reconstruct(self) -> bytes:
        return self.raw


def _graph_references(program: TokenProgram) -> tuple[tuple[dict, ...], tuple[str, ...]]:
    """Read observed reference records without guessing graph execution order."""
    groups = []
    for token in program.tokens:
        if token.raw[1] < 0x90 or not groups:
            groups.append([])
        groups[-1].append(token)
    records, gaps = [], []
    # These are SFC's intermediate reference records, shared by independently
    # compiled FX3U/Q00J controls. They are not Q/FX ordinary ladder opcodes.
    steps = {"056c021705": 1, "056c020f05": 1, "056c041c05": 2, "056c041405": 2}
    transitions = {"056c020405", "056c021d05", "056c020505", "056c021f05", "056c020605", "056c022005"}
    jumps = {"0562020005", "0542020005", "0542010105"}
    for group in groups:
        row = dict(offset=group[0].offset, raw_hex="".join(t.raw.hex() for t in group), kind="opaque")
        header = group[0].raw.hex()
        types, kind = ((), "end") if header == "056c010a05" else (None, None)
        if header in steps:
            types, kind = (0x98,) * steps[header], "step-action-reference"
        elif header in transitions:
            types, kind = (0x99,), "transition-reference"
        elif header in jumps:
            types, kind = (0x98,), "step-reference"
        elif header == "056c041505":
            types, kind = (0x98, 0xdc), "block-call-reference"
        values = []
        if types is not None and len(group) == len(types) + 1:
            for token, code in zip(group[1:], types):
                if len(token.raw) not in (4, 5) or token.raw[1] != code:
                    break
                values.append(int.from_bytes(token.raw[2:-1], "little"))
            else:
                row.update(kind=kind, references=values)
        if row["kind"] == "opaque":
            gaps.append("unobserved SFC graph record at " + str(row["offset"]))
        records.append(row)
    return tuple(records), tuple(gaps)


def parse_sfc_pou(raw: bytes) -> SfcSource:
    raw = bytes(raw)
    layout = frame_sfc_pou(raw)
    start, size = layout["graph"]["offset"], layout["graph"]["size"]
    tokens, records, gaps = None, (), []
    mode = struct.unpack_from("<II", raw, start + 4)
    layout["graph"]["storage_words"] = list(mode)
    if mode == (0, 0):
        try:
            if size < 20:
                raise GXWFormatError("truncated converted SFC graph")
            block_size = struct.unpack_from("<I", raw, start + 12)[0]
            if block_size < 4 or 12 + block_size + 4 > size:
                raise GXWFormatError("invalid converted SFC graph code extent")
            end = start + 12 + block_size
            cache_size = struct.unpack_from("<I", raw, end)[0]
            if cache_size < 4 or end + cache_size != start + size:
                raise GXWFormatError("unsupported converted SFC cache extent")
            tokens = parse_token_fragment(raw, start + 16, block_size - 4)
            records, graph_gaps = _graph_references(tokens)
            gaps.extend(graph_gaps)
            layout["graph"].update(code_offset=start + 16, code_size=block_size - 4,
                                   cache_offset=end, cache_size=cache_size)
        except GXWFormatError as exc:
            gaps.append(str(exc))
    else:
        gaps.append("SFC graph storage is retained without a reference projection: " + str(mode))
    zoom_names = [c["name"] for c in layout["children"] if c["kind"] == "zoom"]
    transition_numbers = [c["number"] for c in layout["children"] if c["kind"] == "transition"]
    action_numbers = [a["number"] for a in layout["actions"]]
    for identities, role in ((zoom_names, "zoom names"), (transition_numbers, "transition numbers"), (action_numbers, "action numbers")):
        if len(set(identities)) != len(identities):
            gaps.append("ambiguous SFC " + role)
    for action in layout["actions"]:
        for registration in action["registrations"]:
            if registration["qualifier"] != 0:
                gaps.append("uninterpreted SFC action qualifier " + str(registration["qualifier"]))
            if zoom_names.count(registration["zoom"]) != 1:
                gaps.append("unresolved SFC action program " + registration["zoom"])
    for record in records:
        kind = record["kind"]
        if kind == "step-action-reference" and record["references"][-1] not in action_numbers:
            gaps.append("unresolved SFC action reference " + str(record["references"][-1]))
        elif kind == "transition-reference" and record["references"][0] not in transition_numbers:
            gaps.append("unresolved SFC transition reference " + str(record["references"][0]))
    return SfcSource(raw, layout, tokens, records, tuple(gaps))


def frame_sfc_pou(raw: bytes) -> dict:
    """Return bounded source regions; do not interpret or reconstruct the graph."""
    cursor = 54

    def word():
        nonlocal cursor
        if cursor + 4 > len(raw):
            raise GXWFormatError("truncated SFC framing")
        value = struct.unpack_from("<I", raw, cursor)[0]
        cursor += 4
        return value

    def count():
        value = word()
        if value > 8192:
            raise GXWFormatError("SFC child count exceeds experiment bound")
        return value

    def name():
        nonlocal cursor
        n = count()
        end = cursor + 2 * n
        if not n or end > len(raw) or raw[end - 2:end] != b"\0\0":
            raise GXWFormatError("invalid SFC child name extent")
        try:
            result = raw[cursor:end - 2].decode("utf-16le")
        except UnicodeError as exc:
            raise GXWFormatError("undecodable SFC child name") from exc
        if "\0" in result:
            raise GXWFormatError("embedded NUL in SFC child name")
        cursor = end
        return result

    def program(kind, identity, number=None):
        nonlocal cursor
        flag = word()
        if flag != 1:
            raise GXWFormatError("unsupported child program storage flag")
        size_offset = cursor
        size = word()
        start, end = cursor, cursor + size
        if size < 20 or end > len(raw) or struct.unpack_from("<I", raw, start)[0] != size:
            raise GXWFormatError("inconsistent child program extent")
        tokens = parse_token_fragment(raw, start + 20, size - 20)
        cursor = end
        tail_offset = cursor
        tail = word()
        if tail != 1:
            raise GXWFormatError("unsupported child program trailer")
        return dict(kind=kind, name=identity, number=number, size_offset=size_offset,
                    payload_offset=start, payload_size=size, token_offset=start + 20,
                    token_size=size - 20, token_hex=tokens.body.hex(),
                    tail_offset=tail_offset)

    if word() != 0xF0:
        raise GXWFormatError("not the observed SFC source type")
    graph_size = word()
    graph_start = cursor
    if graph_size < 12 or graph_start + graph_size > len(raw) or word() != graph_size:
        raise GXWFormatError("unsupported SFC graph extent")
    cursor = graph_start + graph_size
    children = []
    zoom_count_offset = cursor
    for _ in range(count()):
        entry_start = cursor
        child = program("zoom", name())
        child.update(record_offset=entry_start, record_size=cursor-entry_start)
        children.append(child)
    actions = []
    action_count_offset = cursor
    for _ in range(count()):
        entry_start = cursor
        identity = name()
        number_offset = cursor
        number = word()
        registrations = []
        for _ in range(count()):
            qualifier = word()
            registrations.append(dict(qualifier=qualifier, zoom=name()))
        if word() != 1:
            raise GXWFormatError("unsupported SFC action trailer")
        actions.append(dict(name=identity, number=number, number_offset=number_offset, registrations=registrations,
                            record_offset=entry_start, record_size=cursor-entry_start))
    transition_count_offset = cursor
    for _ in range(count()):
        entry_start = cursor
        identity, number = name(), word()
        child = program("transition", identity, number)
        child.update(record_offset=entry_start, record_size=cursor-entry_start)
        children.append(child)
    tail_offset = cursor
    # Real older FX3U projects end after the last child trailer. Native save
    # appends a zero DWORD; retain either observed suffix exactly as supplied.
    if raw[cursor:] not in (b"", b"\0" * 4):
        raise GXWFormatError("unsupported SFC trailing region")
    return dict(graph=dict(offset=graph_start, size=graph_size, handling="opaque-preserved"),
                children=children, actions=actions, tail_offset=tail_offset,
                tail_hex=raw[tail_offset:].hex(),
                zoom_count_offset=zoom_count_offset, action_count_offset=action_count_offset,
                transition_count_offset=transition_count_offset)

