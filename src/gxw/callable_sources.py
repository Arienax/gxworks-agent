"""CPU-selected callable interfaces bound to the existing graph and labels.

Source formals describe graphic connections; execution and IN_OUT copyback are
not inferred here. Failed or ambiguous bindings remain explicit read issues.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
import re

from .container_writer import validate_cfb_streams
from .declarations import parse_declarations
from .library_sources import decode_library_archive, parse_library_source, parse_library_declarations
from .models import GXWFormatError, NodeKind
from .project_metadata import logical_mapping, read_project_text_context
from .semantic import (FunctionBlockCategory, FunctionBlockPortSpec, FunctionBlockSpec,
                       SemanticPortRole)


CLASS = {'VAR': 1, 'VAR CONSTANT': 2, 'VAR_INPUT': 3, 'VAR_OUTPUT': 4,
         'VAR_IN_OUT': 5, 'VAR_INPUT CONSTANT': 11, 'VAR_IN_EXT': 12}

# SECTION selectors from the native Workspace CPU-name table. These select
# source declarations only; they do not share compiler/address behavior or
# certify one controller using the result of another controller's compilation.
LIBRARY_CPU_SECTIONS = {
    'FX0S/FX0': 'FX0', 'FX1': 'FX1N', 'FX1N/FX1NC': 'FX1N',
    'FX2/FX2C': 'FX2C', 'FX2N/FX2NC': 'FX2N', 'FX3U/FX3UC': 'FX3U',
    'FX3G/FX3GC': 'FX3G',
    'Q2A': 'QnA', 'Q2AS1': 'QnA', 'Q2AS(H)': 'QnA', 'Q2AS(H)S1': 'QnA',
    'Q3A': 'QnA', 'Q4A': 'QnA', 'Q00': 'Q00_Q01', 'Q01': 'Q00_Q01',
    'Q02/Q02H': 'Q', 'Q06H': 'Q', 'Q12H': 'Q', 'Q25H': 'Q',
    'Q02PH': 'QnPH', 'Q06PH': 'QnPH', 'Q12PH': 'QnPH', 'Q25PH': 'QnPH',
    'Q12PRH': 'QnPRH', 'Q25PRH': 'QnPRH', 'Q02(H)-A': 'Q02_H_A', 'Q06H-A': 'Q06H_A',
}


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


def interface(definition, input_count=None):
    if definition.get('source_kind') == 'project_labels':
        rows = [dict(name=r.name, class_code=r.class_code,
                     declared_type=r.data_type, source_offset=r.offset)
                for r in definition['declarations'].rows]
        if any(r['class_code'] not in CLASS.values() for r in rows):
            raise GXWFormatError('project declaration class outside observed interfaces')
    else:
        declaration = parse_library_declarations(definition)
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
        len(en) != 1 or len(eno) != 1 or inputs[:1] != en or outputs[:1] != eno
        or en[0]['class_code'] != 3 or eno[0]['class_code'] != 4
        or en[0]['declared_type'] != 'BOOL' or eno[0]['declared_type'] != 'BOOL'
    ):
        raise GXWFormatError('unobserved source EN/ENO pair')
    expanded = [r for r in inputs if r['class_code'] == 12]
    if expanded:
        if input_count is None:
            raise GXWFormatError('extensible source interface requires an explicit input count')
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
    if input_count is not None and len(inputs) != input_count:
        raise GXWFormatError('source formal interface differs from graph input count')
    for r in inputs + outputs:
        r['type_code'] = type_mask(r['declared_type'])
    return inputs, outputs


def sorted_ports(node):
    """Bind observed callable ports by side and ordinate; retain source indexes."""
    width, height = node.bbox.right-node.bbox.left, node.bbox.bottom-node.bbox.top
    input_flags = (3,) if node.kind_code == 1 else (1,)
    output_flags = (0, 2) if node.kind_code == 1 else (0,)
    inputs, outputs = [], []
    for i, p in enumerate(node.ports):
        if not 0 < p.local_y < height:
            raise GXWFormatError('callable port outside bbox interior')
        # Native mode 0x08 is negation, not an IN_OUT declaration class.
        # Preserve it independently of the source formal's direction/type.
        side_flags = p.port_kind_code & ~8
        if p.local_x == 0 and side_flags in input_flags:
            inputs.append(i)
        elif p.local_x == width and side_flags in output_flags:
            outputs.append(i)
        else:
            raise GXWFormatError('callable port flags or side outside native controls')
    for side in (inputs, outputs):
        if len({node.ports[i].local_y for i in side}) != len(side):
            raise GXWFormatError('ambiguous coincident callable ports')
        side.sort(key=lambda i: node.ports[i].local_y)
    return inputs, outputs


@lru_cache(maxsize=8)
def _library_text(value):
    return decode_library_archive(value[20:]).decoded


class ProjectCallableSources:
    """Read-only callable/declaration context for one selected native program."""

    def __init__(self, cpu, program_name, declarations, library_sources):
        self.cpu, self.program_name, self.declarations = cpu, program_name, declarations
        self.library_cpu = LIBRARY_CPU_SECTIONS.get(cpu, cpu)
        self.catalog = defaultdict(list)
        self.issues = []
        for name, text in library_sources.items():
            try:
                for definition in parse_library_source(text)['definitions']:
                    if definition['sections'] is None or self.library_cpu in definition['sections']:
                        self.catalog[definition['name'].casefold()].append((name, definition))
            except (ValueError, UnicodeError, AttributeError) as error:
                self.issues.append({'code': 'library_source_gap', 'stream': name, 'message': str(error)})
        self._project_definitions(declarations)

    def _project_definitions(self, declarations):
        for stream, document in declarations.items():
            kind = {0x1000001: 'FUNCTION', 0x1000002: 'FUNCTION_BLOCK'}.get(document.owner_pou_type)
            if kind is None or not stream.endswith('.Labels.lh'):
                continue
            owner = stream.removesuffix('.Labels.lh')
            if document.scope != 'local' or document.owner_name != owner:
                self.issues.append({'code': 'project_callable_owner_gap', 'stream': stream,
                                    'message': 'project callable owner and declaration stream differ'})
                continue
            definition = dict(name=owner, kind=kind, source_kind='project_labels',
                              offset=0, return_type_raw=document.owner_return_type,
                              declarations=document)
            self.catalog[owner.casefold()].append((stream, definition))

    @classmethod
    def from_project(cls, raw, program_name, declarations=None):
        outer = validate_cfb_streams(raw)
        mapping = logical_mapping(outer['projectdatalist.xml'])
        payloads = validate_cfb_streams(outer['_hdb'])
        streams = {name: payloads[key] for name, key in mapping.items() if key in payloads}
        contexts = [read_project_text_context(value) for name, value in streams.items() if name.endswith('.prj')]
        if len(contexts) != 1:
            raise GXWFormatError('one project CPU context is required for source library selection')
        if declarations is None:
            declarations = {name: parse_declarations(value, logical_name=name)
                            for name, value in streams.items() if name.endswith(('.Labels.lh', '.gh'))}
        libraries, issues = {}, []
        for name, value in streams.items():
            if name.endswith('.lif'):
                try:
                    libraries[name] = _library_text(value)
                except ValueError as error:
                    issues.append({'code': 'library_archive_gap', 'stream': name, 'message': str(error)})
        result = cls(contexts[0]['cpu'], program_name, declarations, libraries)
        result.issues[:0] = issues
        return result

    def with_declarations(self, declarations):
        result = object.__new__(type(self))
        result.__dict__ = {**self.__dict__, 'declarations': declarations}
        result.catalog = defaultdict(list, {name: [(stream, definition) for stream, definition in matches
            if definition.get('source_kind') != 'project_labels'] for name, matches in self.catalog.items()})
        result.issues = [issue for issue in self.issues if issue['code'] != 'project_callable_owner_gap']
        result._project_definitions(declarations)
        return result

    def label(self, symbol):
        local = self.program_name.removesuffix('.Program.pou') + '.Labels.lh'
        names = [local] if local in self.declarations else []
        names += [name for name, doc in self.declarations.items() if doc.scope == 'global']
        matched = [(name, row) for name in names for row in self.declarations[name].rows
                   if row.name.casefold() == symbol.casefold()]
        if len(matched) > 1:
            raise GXWFormatError('ambiguous local/global source declaration name: ' + symbol)
        return matched[0] if matched else None

    def definition(self, name, kind):
        matches = self.catalog.get(name.casefold(), [])
        if len(matches) != 1:
            raise GXWFormatError('missing or ambiguous CPU-selected source callable: ' + name)
        stream, definition = matches[0]
        if definition['kind'] != kind:
            raise GXWFormatError('graph kind differs from source definition')
        return stream, definition

    def fixed_interface(self, name, kind='FUNCTION_BLOCK'):
        stream, definition = self.definition(name, kind)
        inputs, outputs = interface(definition)
        if not 1 <= max(len(inputs), len(outputs)) <= 256:
            raise GXWFormatError('source interface has no bounded observed graphic ports')
        return {'source_stream': stream, 'definition_offset': definition['offset'],
                'inputs': inputs, 'outputs': outputs}

    def callable(self, node, *, bind_instance=True):
        ins, outs = sorted_ports(node)
        name = node.type_name if node.kind == NodeKind.FUNCTION_BLOCK else node.symbol
        if bind_instance and node.kind == NodeKind.FUNCTION_BLOCK:
            binding = self.label(node.symbol)
            row = binding[1] if binding else None
            if not row or row.type_code != 15 or row.type_reference != name or row.data_type != name:
                raise GXWFormatError('FB instance requires a matching source label type: ' + node.symbol)
        matches = self.catalog.get(name.casefold(), [])
        if not matches and node.kind == NodeKind.FUNCTION:
            base = re.sub(r'-[1-9][0-9]*$', '', name)
            candidates = self.catalog.get(base.casefold(), [])
            if len(candidates) == 1 and any(row['source_class'] == 'VAR_IN_EXT'
                    for row in parse_library_declarations(candidates[0][1])['rows']):
                matches = candidates
        if len(matches) != 1:
            raise GXWFormatError('missing or ambiguous CPU-selected source callable: ' + name)
        stream, definition = matches[0]
        expected_kind = 'FUNCTION_BLOCK' if node.kind == NodeKind.FUNCTION_BLOCK else 'FUNCTION'
        if definition['kind'] != expected_kind:
            raise GXWFormatError('graph kind differs from source definition')
        inputs, outputs = interface(definition, len(ins))
        if len(outputs) != len(outs):
            raise GXWFormatError('source formal interface differs from graph output count')
        counts = Counter(row['name'] for row in inputs + outputs)
        ports = [None] * len(node.ports)
        for side, indexes, formals in [('in', ins, inputs), ('out', outs, outputs)]:
            occurrences = Counter()
            repeats = Counter(row['name'] for row in formals)
            for index, formal in zip(indexes, formals):
                formal_name = formal['name']
                occurrences[formal_name] += 1
                endpoint = formal_name
                if counts[formal_name] > 1:
                    endpoint = side + '.' + endpoint
                if repeats[formal_name] > 1:
                    endpoint += '[' + str(occurrences[formal_name]) + ']'
                port = node.ports[index]
                ports[index] = {'name': endpoint, 'formal_name': formal_name, 'side': side,
                    'data_type': formal['declared_type'], 'class_code': formal['class_code'],
                    'occurrence': occurrences[formal_name], 'x': port.local_x, 'y': port.local_y,
                    'negated': bool(port.port_kind_code & 8)}
        return {'source_stream': stream, 'definition_offset': definition['offset'], 'ports': ports}

    def semantic_specs(self, program):
        """Per-instance geometry avoids assigning one fixed layout to a type."""
        specs = {}
        for node in program.nodes:
            if node.kind != NodeKind.FUNCTION_BLOCK:
                continue
            try:
                ports = self.callable(node)['ports']
            except GXWFormatError:
                specs[node.offset] = None
                continue
            formals = []
            for index, port in enumerate(ports):
                enable = port['formal_name'].upper() == ('EN' if port['side'] == 'in' else 'ENO')
                role = SemanticPortRole(('enable_' if enable else 'data_') + port['side'])
                formals.append(FunctionBlockPortSpec(port['formal_name'], role, port['y'],
                                                    node.ports[index].port_kind_code))
            specs[node.offset] = FunctionBlockSpec(node.type_name, FunctionBlockCategory.UNKNOWN, tuple(formals))
        return specs
