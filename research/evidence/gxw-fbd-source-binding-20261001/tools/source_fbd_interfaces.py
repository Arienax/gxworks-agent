"""Project-bound graph interfaces from GXW sources, never a native snapshot.

The catalog is parsed from the project's original CPU-selected .lif text.
Native observations validate interface binding, not execution/writeback effects.
The existing graph/declaration models retain every source and opaque byte.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
import re

from gxw.connectivity import build_connectivity_graph
from gxw.container import CompoundFile
from gxw.declarations import parse_declarations
from gxw.models import GXWFormatError
from gxw.project_metadata import read_project_text_context
from gxw.project_resolver import GXWProjectResolver
from gxw.structured_pou import parse_structured_pou
from probe_gxw_library_archive import (
    decode_library_archive, frame_library_text, observed_source_declarations,
)


CLASS = {'VAR': 1, 'VAR CONSTANT': 2, 'VAR_INPUT': 3, 'VAR_OUTPUT': 4,
         'VAR_IN_OUT': 5, 'VAR_INPUT CONSTANT': 11, 'VAR_IN_EXT': 12}
TYPE = {'BOOL': 1, 'INT': 4, 'DINT': 8, 'WORD': 0x20000, 'DWORD': 0x40000,
        'REAL': 0x200, 'LREAL': 0x400, 'TIME': 0x800, 'STRING': 0x8000,
        'ANY_INT': 0x1fe, 'ANY_NUM': 0x7fe, 'ANY_BIT': 0xf0001,
        'ANY': 0xffffff, 'ANY_SIMPLE': 0xfffff, 'ANY16': 0x20044,
        'ANY32': 0x40088, 'ANY_REAL': 0x600}


def type_mask(text):
    if text in TYPE:
        return TYPE[text]
    if re.fullmatch(r'STRING(?:\([1-9][0-9]*\)|\[[1-9][0-9]*\])', text):
        return 0x8000
    array = re.fullmatch(r'ARRAY ?\[([^]]+)\] OF (\w+)', text)
    if array and array[2] in TYPE:
        bounds = [re.fullmatch(r'(-?\d+)\.\.(-?\d+)', part) for part in array[1].split(',')]
        if bounds and all(b and -2**31 <= int(b[1]) <= int(b[2]) < 2**31 for b in bounds):
            return 0x100000
    raise GXWFormatError('formal type outside observed coarse native masks: ' + text)


def interface(definition, input_count):
    declaration = observed_source_declarations(definition)
    if declaration['gaps']:
        raise GXWFormatError('unframed source library declarations')
    rows = []
    for r in declaration['rows']:
        if r['source_class'] not in CLASS:
            raise GXWFormatError('source declaration class outside observed interfaces')
        rows.append(dict(name=r['name'], class_code=CLASS[r['source_class']],
                         declared_type=r['data_type_raw'], source_offset=r['offset']))
    inputs = [r for r in rows if r['class_code'] in (3, 11, 12)]
    outputs = [r for r in rows if r['class_code'] == 4]
    inouts = [r for r in rows if r['class_code'] == 5]
    en = [r for r in rows if r['name'].upper() == 'EN']
    eno = [r for r in rows if r['name'].upper() == 'ENO']
    if bool(en) != bool(eno) or en and (
        len(en) != 1 or len(eno) != 1 or en[0] != inputs[0] or eno[0] != outputs[0]
        or en[0]['class_code'] != 3 or eno[0]['class_code'] != 4
        or en[0]['declared_type'] != 'BOOL' or eno[0]['declared_type'] != 'BOOL'
    ):
        raise GXWFormatError('unobserved source EN/ENO pair')
    expanded = [r for r in inputs if r['class_code'] == 12]
    if expanded:
        fixed = [r for r in inputs if r['class_code'] != 12]
        if len(expanded) != 1 or fixed != en:
            raise GXWFormatError('unobserved mixed extensible inputs')
        repeat = input_count - len(inouts) - len(en)
        if not 1 <= repeat <= 256:
            raise GXWFormatError('extensible input arity outside observed bound')
        inputs = en + expanded * repeat
    inputs += inouts
    return_type = definition['return_type_raw']
    if return_type is not None and return_type != "@'VOID'":
        outputs = eno + [dict(name=definition['name'], class_code=4,
                              declared_type=return_type, source_offset=None)] + [r for r in outputs if r not in eno]
    outputs += inouts
    if len(inputs) != input_count:
        raise GXWFormatError('source formal interface differs from graph input count')
    for r in inputs + outputs:
        r['type_code'] = type_mask(r['declared_type'])
    return inputs, outputs


def sorted_ports(node):
    """Bind observed callable ports by side and ordinate; retain source indexes."""
    width, height = node.bbox.right-node.bbox.left, node.bbox.bottom-node.bbox.top
    input_flags = (3,) if node.kind_code == 1 else (1, 9)
    output_flags = (0, 2) if node.kind_code == 1 else (0, 8)
    inputs, outputs = [], []
    for i, p in enumerate(node.ports):
        if not 0 < p.local_y < height:
            raise GXWFormatError('callable port outside bbox interior')
        if p.local_x == 0 and p.port_kind_code in input_flags:
            inputs.append(i)
        elif p.local_x == width and p.port_kind_code in output_flags:
            outputs.append(i)
        else:
            raise GXWFormatError('callable port flags or side outside native controls')
    for side in (inputs, outputs):
        if len({node.ports[i].local_y for i in side}) != len(side):
            raise GXWFormatError('ambiguous coincident callable ports')
        side.sort(key=lambda i: node.ports[i].local_y)
    return inputs, outputs


class SourceGraph:
    def __init__(self, raw, logical):
        self.raw = raw
        self.resolver = GXWProjectResolver(CompoundFile(raw))
        present = {e.name for e in self.resolver.hdb.iter_streams()}
        self.streams = {f.logical_name:self.resolver.read_logical_file(f.logical_name)
                        for f in self.resolver.logical_files() if f.stream_name in present}
        self.missing_logical_objects = [f.logical_name for f in self.resolver.logical_files()
                                       if f.stream_name not in present]
        contexts = [read_project_text_context(v) for k,v in self.streams.items() if k.endswith('.prj')]
        if len(contexts) != 1:
            raise GXWFormatError('one project CPU context is required for source library selection')
        self.cpu = contexts[0]['cpu']
        self.library_cpu = {'FX3U/FX3UC': 'FX3U', 'FX3G/FX3GC': 'FX3G'}.get(self.cpu, self.cpu)
        self.program = parse_structured_pou(self.streams[logical], logical_name=logical,
                                             preserve_unsupported_records=True)
        self.declarations = {k:parse_declarations(v, logical_name=k) for k,v in self.streams.items()
                             if k.endswith(('.Labels.lh', '.gh'))}
        self.catalog = defaultdict(list)
        self.library_gaps = []
        for name, value in self.streams.items():
            if not name.endswith('.lif'):
                continue
            try:
                text = decode_library_archive(value[20:]).decoded
                for d in frame_library_text(text)['definitions']:
                    if d['sections'] is None or self.library_cpu in d['sections']:
                        self.catalog[d['name'].casefold()].append((name, d))
            except ValueError as e:
                self.library_gaps.append(dict(stream=name, reason=str(e)))

    def label(self, symbol):
        local = self.program.logical_name.removesuffix('.Program.pou') + '.Labels.lh'
        def hits(names):
            return [(k,r) for k in names for r in self.declarations[k].rows if r.name.casefold() == symbol.casefold()]
        matched = hits([local]) if local in self.declarations else []
        global_hits = hits([k for k,d in self.declarations.items() if d.scope == 'global'])
        if matched and global_hits:
            raise GXWFormatError('ambiguous local/global source declaration name: ' + symbol)
        if not matched:
            matched = global_hits
        if len(matched) > 1:
            raise GXWFormatError('ambiguous source declaration binding: ' + symbol)
        if not matched:
            return None
        name, r = matched[0]
        return dict(table=name, record_id=r.record_id, source_offset=r.offset,
                    name=r.name, data_type=r.data_type, type_reference=r.type_reference,
                    type_code=r.type_code, class_code=r.class_code, device=r.device,
                    iec_address=r.iec_address, initial_value=r.initial_value)

    def callable(self, node):
        ins, outs = sorted_ports(node)
        name = node.type_name if node.kind_code == 2 else node.symbol
        if node.kind_code == 2:
            label = self.label(node.symbol)
            if (not label or label['type_code'] != 15 or label['type_reference'] != name
                    or label['data_type'] != name):
                raise GXWFormatError('FB instance requires a matching source label type')
        matches = self.catalog.get(name.casefold(), [])
        if not matches and node.kind_code == 1:
            base = re.sub(r'-[1-9][0-9]*$', '', name)
            candidates = self.catalog.get(base.casefold(), [])
            if len(candidates) == 1 and any(r['source_class'] == 'VAR_IN_EXT'
                for r in observed_source_declarations(candidates[0][1])['rows']):
                matches = candidates
        if len(matches) != 1:
            raise GXWFormatError('missing or ambiguous CPU-selected source callable: ' + name)
        stream, definition = matches[0]
        if definition['kind'] != ('FUNCTION_BLOCK' if node.kind_code == 2 else 'FUNCTION'):
            raise GXWFormatError('graph kind differs from source definition')
        inputs, outputs = interface(definition, len(ins))
        if len(outputs) != len(outs):
            raise GXWFormatError('source formal interface differs from graph output count')
        ports = []
        for side, indexes, formals in [('in', ins, inputs), ('out', outs, outputs)]:
            repeats = defaultdict(int)
            for index, formal in zip(indexes, formals):
                repeats[formal['name']] += 1
                ports.append(dict(index=index, side=side, **formal, occurrence=repeats[formal['name']],
                                  point=asdict(node.port_point(index)), flags=node.ports[index].port_kind_code))
        return dict(source_stream=stream, definition_offset=definition['offset'], ports=ports)

    def view(self):
        graph = build_connectivity_graph(self.program)
        membership = {r.offset:i for i,(_, records) in enumerate(self.program.block_records()) for r in records}
        nodes, gaps = [], list(self.library_gaps)
        for n in self.program.nodes:
            item = dict(offset=n.offset, block=membership[n.offset], kind=n.kind_code,
                        symbol=n.symbol, type_name=n.type_name, bbox=asdict(n.bbox))
            if n.kind_code in (1, 2):
                try:
                    item['interface'] = self.callable(n)
                    for p in item['interface']['ports']:
                        p['net_index'] = graph.net_for_port(n.offset, p['index']).index
                except GXWFormatError as e:
                    item['interface_gap'] = str(e)
                    gaps.append(dict(node_offset=n.offset, reason=str(e)))
            if n.kind_code != 1:
                try:
                    item['label'] = self.label(n.symbol)
                    if n.kind_code == 2 and (not item['label'] or item['label']['type_code'] != 15
                                             or item['label']['type_reference'] != n.type_name):
                        gaps.append(dict(node_offset=n.offset, reason='FB graph type and declaration differ'))
                except GXWFormatError as e:
                    gaps.append(dict(node_offset=n.offset, reason=str(e)))
            nodes.append(item)
        return dict(cpu=self.cpu, library_cpu=self.library_cpu, logical=self.program.logical_name, nodes=nodes,
                    nets=[dict(index=n.index, ports=[dict(node_offset=p.node_offset, port_index=p.port_index) for p in n.ports],
                               wire_offsets=list(n.wire_offsets)) for n in graph.nets], gaps=gaps,
                    unknown_record_count=len(self.program.unknown_records),
                    missing_logical_objects=self.missing_logical_objects, execution_effects='not inferred')
