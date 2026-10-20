"""Bounded SFC source records with raw-preserved graph and child programs.

The envelope and child ownership were checked against native frontend records.
Graph references describe source bindings, not SFC execution semantics. Unknown
storage modes, graph instructions and registration qualifiers remain explicit.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import struct
from typing import Mapping

from .models import GXWFormatError
from .source_header import source_payload_offset
from .token_pou import TokenProgram, parse_token_fragment


# Original graph readers, concrete CPU identities, native 1.635.0.1.
# The menu controls are frozen in gxw-sfc-cpu-menus-20261014. These lists
# select an observed buffer ABI; graph writing and instruction semantics have
# separate scopes. Stored names retain the native merged-menu identities.
NATIVE_SFC_GRAPH_FX_CPUS = frozenset((
    'FX0S/FX0', 'FX0N', 'FX1', 'FX1S', 'FX1N/FX1NC', 'FXU/FX2C',
    'FX2N/FX2NC', 'FX3S', 'FX3G', 'FX3U/FX3UC',
))
NATIVE_SFC_GRAPH_QL_CPUS = frozenset((
    'Q00J', 'Q00UJ', 'Q00', 'Q00U', 'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U',
    'Q03UD', 'Q03UDE', 'Q03UDV', 'Q04UDH', 'Q04UDEH', 'Q04UDV', 'Q04UDPV',
    'Q06H', 'Q06PH', 'Q06UDH', 'Q06UDEH', 'Q06UDV', 'Q06UDPV', 'Q10UDH',
    'Q10UDEH', 'Q12H', 'Q12PH', 'Q12PRH', 'Q13UDH', 'Q13UDEH', 'Q13UDV',
    'Q13UDPV', 'Q20UDH', 'Q20UDEH', 'Q25H', 'Q25PH', 'Q25PRH', 'Q26UDH',
    'Q26UDEH', 'Q26UDV', 'Q26UDPV', 'Q50UDEH', 'Q100UDEH',
    'L02S', 'L02', 'L06', 'L26', 'L26-BT',
))


def native_sfc_graph_profile(cpu):
    """Select a measured read ABI; never infer it from an untested series."""
    if cpu in NATIVE_SFC_GRAPH_FX_CPUS:
        return {'family': 'fx'}
    if cpu in NATIVE_SFC_GRAPH_QL_CPUS:
        return {'family': 'q-l'}
    return None


def sfc_step_action_reference(record, *, cpu=None):
    """Bind an explicit pair or the measured Q/L same-number source form.

    The single operand is a step number. It selects the action registration
    with that number, rather than ReadSymbol's independent substep output.
    Unknown CPU identities and other source forms remain unresolved.
    """
    if record.get('kind') != 'step-action-reference':
        return None
    references = record.get('references', [])
    if len(references) == 2:
        return tuple(references)
    if len(references) == 1 and cpu in NATIVE_SFC_GRAPH_QL_CPUS:
        return references[0], references[0]
    return None


@dataclass(frozen=True)
class SfcSource:
    raw: bytes
    layout: dict
    graph_tokens: TokenProgram | None
    graph_records: tuple[dict, ...]
    diagnostics: tuple[str, ...]

    def reconstruct(self) -> bytes:
        return self.raw


@dataclass(frozen=True)
class SfcProjectContext:
    """One imported project snapshot; never reuse it after a source edit."""

    raw: bytes
    program_name: str
    programs: tuple[str, ...]
    source: SfcSource
    project: dict
    declarations: dict
    issues: tuple[dict | str, ...]

    def object_model(self, *, token_profile=None, native_graph=None) -> dict:
        """Project stored ownership and optional lexical facts, not execution."""
        from .token_listing import decode_token_program, TokenInstruction, TokenText, TokenLabel, TokenGap

        children = []
        for child in self.source.layout['children']:
            program = parse_token_fragment(self.source.raw, child['token_offset'], child['token_size'])
            listing = (decode_token_program(program, profile=token_profile,
                       text_encoding=self.project['text_encoding']) if token_profile else None)
            records = []
            groups = listing.records if listing else program.tokens
            for item in groups:
                tokens = item.tokens if listing else (item,)
                record = {'source_offset': tokens[0].offset,
                          'raw_hex': b''.join(t.raw for t in tokens).hex(), 'kind': 'opaque'}
                if isinstance(item, TokenInstruction):
                    record.update(kind='instruction', op=item.mnemonic, args=list(item.args), step=item.step)
                elif isinstance(item, TokenText):
                    record.update(kind=item.role, text=item.text, step=item.step)
                elif isinstance(item, TokenLabel):
                    record.update(kind='label', text=item.text, step=item.step)
                elif isinstance(item, TokenGap):
                    record['reason'] = item.reason
                records.append(record)
            registrations = [{'name': action['name'], 'number': action['number'], **registration}
                             for action in self.source.layout['actions']
                             for registration in action['registrations'] if registration['zoom'] == child['name']]
            children.append({'kind': child['kind'], 'name': child['name'], 'number': child['number'],
                             'source_offset': child['token_offset'], 'records': records,
                             'registrations': registrations if child['kind'] == 'zoom' else []})
        graph = {'storage_words': self.source.layout['graph']['storage_words'],
                 'records': [dict(row) for row in self.source.graph_records],
                 'position_resolution': 'not-established'}
        graph['edit_profile'] = ('fx-references' if self.project['cpu'] in NATIVE_SFC_GRAPH_FX_CPUS else
                                'q-l-symbols' if self.project['cpu'] in NATIVE_SFC_GRAPH_QL_CPUS else None)
        cells = self.native_graph(native_graph)
        if cells is not None:
            graph.update(position_resolution='native-source-bound', **cells)
        return {'schema_version': 1, 'program': self.program_name, 'cpu': self.project['cpu'],
                'codepage': self.project['codepage'], 'lexical_profile': token_profile,
                'graph': graph,
                'actions': deepcopy(self.source.layout['actions']), 'children': children,
                'labels': {name: [{'name': row.name, 'data_type': row.data_type,
                                  'class_code': row.class_code, 'device': row.device, 'comment': row.comment}
                                 for row in document.rows] for name, document in self.declarations.items()},
                'issues': list(self.issues) + list(self.source.diagnostics)}

    def native_graph(self, evidence) -> dict | None:
        """Reuse original cell readings only for the same current source body.

        This is a geometry projection, not a save gate. A stale/missing reading
        leaves positions unresolved; declarations and children are rebuilt.
        """
        region = self.source.layout['graph']
        current = self.source.raw[region['offset']:region['offset'] + region['size']]
        if (not isinstance(evidence, dict) or evidence.get('status') != 'source-bound'
                or evidence.get('cpu') != self.project['cpu'] or evidence.get('native_version') != '1.635.0.1'
                or evidence.get('logical_name') != self.program_name or evidence.get('source_body_hex') != current.hex()):
            return None
        grid = evidence.get('after')
        if not isinstance(grid, dict):
            return None
        width, height, supplied = grid.get('width'), grid.get('height'), grid.get('cells')
        if (type(width) is not int or type(height) is not int or not 0 < width <= 256
                or not 0 < height <= 4096 or width * height > 65536 or not isinstance(supplied, list)
                or len(supplied) > width * height):
            return None
        fields = ('x', 'y', 'type', 'undefined', 'substep', 'number', 'branch', 'result')
        cells, seen = [], set()
        for row in supplied:
            if (not isinstance(row, dict) or any(type(row.get(key)) is not int for key in fields)
                    or not 0 <= row['x'] < width or not 0 <= row['y'] < height
                    or (row['x'], row['y']) in seen):
                return None
            seen.add((row['x'], row['y']))
            cell = {key: row[key] for key in fields}
            cell.update(id=f"cell:{row['x']}:{row['y']}", role='opaque', binding={'status': 'unresolved'})
            ql_step = self.project['cpu'] in NATIVE_SFC_GRAPH_QL_CPUS and row['type'] in (0, 6)
            if row['result'] == 1 and (row['type'] in (4, 10, 20, 26) or ql_step):
                action_number = row['number'] if ql_step else row['substep']
                references = [item for item in self.source.graph_records if item['kind'] == 'step-action-reference'
                              and sfc_step_action_reference(item, cpu=self.project['cpu']) == (row['number'], action_number)]
                actions = [item for item in self.source.layout['actions'] if item['number'] == action_number]
                cell['role'] = 'step'
                if len(references) == len(actions) == 1:
                    cell['binding'] = {'status': 'source-resolved', 'step_reference': row['number'],
                                       'action_number': action_number, 'registrations': deepcopy(actions[0]['registrations'])}
            elif row['result'] == 2:
                matches = [item for item in self.source.layout['children'] if item['kind'] == 'transition' and item['number'] == row['number']]
                cell['role'] = 'transition'
                if len(matches) == 1:
                    cell['binding'] = {'status': 'source-resolved', 'transition_number': row['number'], 'child': matches[0]['name']}
            elif row['result'] == 3:
                cell['role'] = 'connector'
            cells.append(cell)
        projected = {'width': width, 'height': height, 'cells': cells,
                'native_version': evidence['native_version'], 'connections': 'raw-native-symbols',
                'topology': _native_connections(cells, cpu=self.project['cpu'])}
        if self.project['cpu'] in NATIVE_SFC_GRAPH_QL_CPUS:
            projected['write_symbols'] = _native_write_symbols(cells, cpu=self.project['cpu'])
        return projected


def _native_write_symbols(cells, *, cpu):
    """Project measured Q/L symbols to original writer requests, without codegen."""
    if cpu not in NATIVE_SFC_GRAPH_QL_CPUS:
        return None
    topology = _native_connections(cells, cpu=cpu)
    if topology['issues'] or topology['jumps']:
        return None
    symbols, branches, lines = [], {}, {}
    for cell, symbol in zip(sorted(cells, key=lambda row: (row['y'], row['x'])), topology['symbols']):
        kind = symbol['kind']
        if kind in ('step', 'transition', 'end'):
            item = dict(kind='initial_step' if kind == 'step' and cell['type'] == 6 else kind,
                        x=cell['x'], y=cell['y'])
            if kind != 'end':
                item['number'] = cell['number']
            symbols.append(item)
        elif kind == 'line':
            lines.setdefault(cell['x'], []).append(cell['y'])
        elif kind in ('parallel-branch', 'parallel-coupling', 'selective-branch', 'selective-coupling'):
            branches.setdefault((cell['y'], kind.replace('-', '_')), []).append(cell['x'])
        else:
            return None
    for (y, kind), coordinates in branches.items():
        runs = [[coordinates[0]]]
        for coordinate in coordinates[1:]:
            if coordinate == runs[-1][-1] + 1:
                runs[-1].append(coordinate)
            else:
                runs.append([coordinate])
        for run in runs:
            if len(run) < 2:
                return None
            symbols.append(dict(kind=kind, start=run[0], end=run[-1], y=y))
    for x, coordinates in lines.items():
        runs = [[coordinates[0]]]
        for coordinate in coordinates[1:]:
            if coordinate == runs[-1][-1] + 1:
                runs[-1].append(coordinate)
            else:
                runs.append([coordinate])
        for run in runs:
            symbols.append(dict(kind='line', x=x, start=run[0], end=run[-1]))
    return sorted(symbols, key=lambda row: (row.get('y', row.get('start', 0)), row.get('x', row.get('start', 0))))


def resolve_sfc_graph_edits(context, native_graph, edits):
    """Resolve a selected step swap through this source's bound native grid."""
    if not isinstance(edits, list) or not any(isinstance(row, dict) and row.get('kind') == 'swap_steps' for row in edits):
        return edits
    from .models import GXWFormatError
    fields = {'kind', 'left_step_reference', 'right_step_reference'}
    if (len(edits) != 1 or set(edits[0]) != fields
            or any(type(edits[0][key]) is not int for key in fields - {'kind'})):
        raise GXWFormatError('SFC step swaps require two current references and cannot mix other graph edits')
    graph = context.native_graph(native_graph)
    if graph is None or graph.get('write_symbols') is None:
        raise GXWFormatError('SFC step swaps require this current source and a measured native symbol grid')
    left, right = (edits[0][key] for key in ('left_step_reference', 'right_step_reference'))
    symbols = deepcopy(graph['write_symbols'])
    selected = [[row for row in symbols if row['kind'] == 'step' and row['number'] == number]
                for number in (left, right)]
    if left == right or any(len(rows) != 1 for rows in selected):
        raise GXWFormatError('SFC step swaps require two distinct current ordinary steps')
    selected[0][0]['number'], selected[1][0]['number'] = right, left
    return [{'kind': 'rebuild', 'symbols': symbols}]


def _native_connections(cells, *, cpu):
    """Project measured native symbol ports, never generate SFC instructions.

    Call only with validated current-source cells. FX2 15.31 ReadSymbol and
    measured Q/L branch/coupling writers and cold getters establish the low
    port bits and high kind nibble within their respective CPU scopes.
    Reciprocal adjacent ports form geometric segments; they do not
    establish evaluation order, parallel execution, or native acceptance.
    """
    scope = 'current native grid geometry and explicit jump references; no execution semantics'
    ql = cpu in NATIVE_SFC_GRAPH_QL_CPUS
    if cpu not in NATIVE_SFC_GRAPH_FX_CPUS and not ql:
        return dict(status='not-established', symbols=[], segments=[], jumps=[],
                    issues=[dict(reason='unverified-cpu-connection-profile')], scope=scope)
    kinds = {0: 'line', 0x50: 'selective-branch', 0x60: 'selective-coupling',
             0x90: 'parallel-branch', 0xa0: 'parallel-coupling'}
    directions = (('down', 1, 0, 1, 'up'), ('up', 2, 0, -1, 'down'),
                  ('right', 4, 1, 0, 'left'), ('left', 8, -1, 0, 'right'))
    ordered = sorted(cells, key=lambda row: (row['y'], row['x']))
    points, symbols, issues = {}, [], []
    for cell in ordered:
        item = dict(cell=cell.get('id', f"cell:{cell['x']}:{cell['y']}"), kind='opaque', ports=[])
        if cell['result'] == 3:
            branch = cell['branch']; item['branch'] = branch
            if 0 < branch <= 0xff and branch & 0x0f and branch & 0xf0 in kinds:
                item.update(kind=kinds[branch & 0xf0], ports=[name for name, mask, *_ in directions if branch & mask])
            else:
                issues.append(dict(cell=item['cell'], reason='unobserved-connector-flags', branch=branch))
        elif cell['result'] == 2:
            item.update(kind='transition', ports=['down', 'up'], reference=cell['number'])
        elif cell['result'] == 1 and (cell['type'] in (0, 6) if ql else cell['type'] in (4, 10, 20, 26)):
            item.update(kind='step', ports=['down', 'up'], reference=cell['number'],
                        initial=cell['type'] in (6, 10, 26))
        elif ql and cell['result'] == 1 and cell['type'] == 14 and cell['number'] == 999:
            item.update(kind='end', ports=['up'], reference=cell['number'])
        elif cell['result'] == 1 and cell['type'] == 15:
            item.update(kind='jump', ports=['up'], reference=cell['number'])
        else:
            issues.append(dict(cell=item['cell'], reason='unobserved-native-symbol'))
        points[cell['x'], cell['y']] = item
        symbols.append(item)
    segments = []
    for cell in ordered:
        symbol = points[cell['x'], cell['y']]
        for name, _, dx, dy, reciprocal in directions:
            if name not in symbol['ports']:
                continue
            target = points.get((cell['x'] + dx, cell['y'] + dy))
            if target is None or reciprocal not in target['ports']:
                if cell['result'] == 3:
                    issues.append(dict(cell=symbol['cell'], port=name, reason='missing-reciprocal-port'))
                continue
            if name in ('down', 'right'):
                style = ('parallel' if name == 'right' and symbol['kind'] == target['kind']
                         and symbol['kind'].startswith('parallel-') else 'single')
                segments.append(dict(source=symbol['cell'], target=target['cell'], direction=name, style=style))
    step_targets = {}
    for symbol in symbols:
        if symbol['kind'] == 'step':
            step_targets.setdefault(symbol['reference'], []).append(symbol['cell'])
    jumps = []
    for symbol in symbols:
        if symbol['kind'] == 'jump':
            targets = step_targets.get(symbol['reference'], [])
            jumps.append(dict(source=symbol['cell'], step_reference=symbol['reference'], targets=targets,
                              status='uniquely-correlated' if len(targets) == 1 else 'unresolved'))
    return dict(status='native-source-bound', symbols=symbols, segments=segments, jumps=jumps,
                issues=issues, scope=scope)


def read_sfc_project_context(raw: bytes, program_name: str | None = None) -> SfcProjectContext:
    """Bind selected SFC source, labels and CPU to the same current GXW bytes."""
    from .container_writer import validate_cfb_streams
    from .declarations import parse_declarations
    from .project_metadata import logical_mapping, project_source_groups, read_project_text_context

    raw = bytes(raw)
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    nested = validate_cfb_streams(outer['_hdb'])
    projects = [read_project_text_context(nested[key]) for name, key in mapping.items()
                if name.endswith('.prj') and key in nested]
    if len(projects) != 1:
        raise GXWFormatError('SFC project requires one current CPU configuration')
    groups, issues = project_source_groups(outer['projectdatalist.xml'], folder_type=8)
    names = []
    for name in groups:
        if name in mapping and mapping[name] in nested:
            body = nested[mapping[name]]
            try:
                start = source_payload_offset(body)
            except GXWFormatError as error:
                issues.append({'code': 'sfc_source_gap', 'object': name, 'message': str(error)})
                continue
            if body[start:start + 4] == b'\xf0\0\0\0':
                names.append(name)
    names = tuple(sorted(names))
    if program_name is None and len(names) == 1:
        program_name = names[0]
    if program_name not in names:
        raise GXWFormatError('select an existing registered SFC POU from this GXW project')
    declarations = {}
    local = groups[program_name]['labels']
    for name, key in mapping.items():
        if (name == local or name.endswith('.gh')) and key in nested:
            try:
                declarations[name] = parse_declarations(nested[key], logical_name=name,
                    source_kind=240 if name == local else None)
            except GXWFormatError as error:
                issues.append(name + ': ' + str(error))
    return SfcProjectContext(raw, program_name, names, parse_sfc_pou(nested[mapping[program_name]]),
                             projects[0], declarations, tuple(issues))


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
    transitions = {"056c020405", "056c021d05", "056c020505", "056c021f05", "056c020605", "056c022005",
                   "056c020705", "056c020b05"}
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
    cursor = source_payload_offset(raw)

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


def serialize_sfc_pou(source: SfcSource,
                      child_tokens: Mapping[tuple[str, str], bytes] | None = None) -> bytes:
    """Replace existing child tokens while retaining graph, bindings and caches.

    Keys are the child's stored (kind, name), not a step number or display
    position. Tokens are framed raw source records; accepting them here does
    not establish that the native converter or PLC accepts their semantics.
    Only each edited child's two size words and token region are rewritten.
    """
    raw = source.raw
    layout = frame_sfc_pou(raw)
    edits = []
    for identity, supplied in (child_tokens or {}).items():
        selected = [child for child in layout['children']
                    if (child['kind'], child['name']) == identity]
        if len(selected) != 1:
            raise GXWFormatError('missing or ambiguous SFC child identity: ' + repr(identity))
        if not isinstance(supplied, (bytes, bytearray, memoryview)):
            raise GXWFormatError('SFC child tokens must be bytes')
        tokens = bytes(supplied)
        if len(tokens) > 8 * 1024 * 1024 - 20:
            raise GXWFormatError('SFC child exceeds the native workspace body bound')
        parse_token_fragment(tokens, 0, len(tokens))
        child = selected[0]
        if tokens != raw[child['token_offset']:child['tail_offset']]:
            edits.append((child, tokens))
    parts, cursor = [], 0
    for child, tokens in sorted(edits, key=lambda edit: edit[0]['size_offset']):
        size = 20 + len(tokens)
        parts.extend((raw[cursor:child['size_offset']], struct.pack('<II', size, size),
                      raw[child['payload_offset'] + 4:child['token_offset']], tokens))
        cursor = child['tail_offset']
    parts.append(raw[cursor:])
    return b''.join(parts)
