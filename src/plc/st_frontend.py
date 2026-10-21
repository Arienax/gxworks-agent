"""IEC ST syntax trees with a source-preserving GX Works2 lexical adapter.

The pinned tree-sitter grammar owns recognition. This adapter owns GX device
views and GX expression binding, never CPU instruction or device permissions.
Tuple-compatible nodes keep existing Core consumers on the same syntax tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re


class STParseError(ValueError):
    def __init__(self, message, offset=0):
        super().__init__(message)
        self.offset = offset


class STUncovered(STParseError):
    pass


@dataclass(frozen=True)
class SourceRange:
    start: int
    end: int
    line: int
    column: int


class Node(tuple):
    def __new__(cls, values, span):
        node = super().__new__(cls, values)
        node.span = span
        return node

    @property
    def kind(self):
        return self[0]


@dataclass
class Declaration:
    name: str
    data_type: str
    class_name: str = 'VAR'
    initial: object = None
    initial_text: str = ''
    address: str = ''
    span: SourceRange | None = None

    def native_row(self):
        return dict(name=self.name, data_type=self.data_type, class_name=self.class_name,
                    initial_value=self.initial_text, device=self.address)


@dataclass
class Unit:
    name: str
    kind: str
    body: list = field(default_factory=list)
    declarations: list[Declaration] = field(default_factory=list)
    return_type: str = ''
    types: dict = field(default_factory=dict)
    source: str = ''
    body_text: str = ''


@dataclass
class SourceUnit:
    units: list[Unit]
    declarations: list[Declaration]
    types: dict


# SH080782ENG-O, ST operator table: equality differs from ordering; operators
# of the same priority associate from the left, including exponentiation.
def operator_binding():
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    if not DEFAULT_INSTRUCTION_REGISTRY.fact_bundle_current():
        raise STUncovered('Fact resource changed; reload the checking context')
    feature = DEFAULT_INSTRUCTION_REGISTRY.language_feature('ST.OPERATORS') or {}
    groups = [f for f in feature.get('facts', []) if f.get('status') == 'source_verified'
              and f.get('dimension') == 'parameters.operator_binding']
    if len(groups) != 1:
        raise STUncovered('GX operator facts are missing or ambiguous')
    return groups[0]['value']


BACKEND = 'tree-sitter-iec61131-3-st@00e24f50f8ded0d71c146659030fb66d7c4d1529'


class _Adapter:
    def __init__(self, source, *, body=False):
        if not isinstance(source, str) or len(source) > 512000:
            raise STUncovered('ST source is absent or exceeds the inspection size limit')
        self.source = source
        self.prefix = 'PROGRAM __BODY__\n' if body else ''
        self.raw = (self.prefix + source + ('\nEND_PROGRAM' if body else '')).encode('utf-8')
        # A bit-qualified GX device is an identifier, rather than a floating
        # member name. Equal-length projection keeps every byte position intact.
        from plc.validation import DEVICE_ADDRESS_RE
        def view(match):
            token = match.group()
            return token.replace('.', '_') if DEVICE_ADDRESS_RE.fullmatch(token) else token
        projected = re.sub(rb'\b[A-Za-z]+[0-9A-Fa-f]+\.[0-9]+\b',
            lambda m: view(re.match(r'.*', m.group().decode())).encode(), self.raw)
        projected = re.sub(rb'\bW?STRING\s*\[[0-9]+\]',
            lambda m: m.group().replace(b'[', b'(').replace(b']', b')'), projected, flags=re.I)
        # The native declaration formatter spells stored class 10 VAR_RETAIN.
        # The pinned grammar recognizes IEC's equal-length VAR RETAIN spelling;
        # the original bytes still own declaration text and source positions.
        projected = re.sub(rb'\bVAR_RETAIN\b', b'VAR RETAIN', projected, flags=re.I)
        try:
            import tree_sitter
            import tree_sitter_iec61131_3_st
        except ImportError as error:
            raise STUncovered('ST frontend dependency is unavailable') from error
        parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_iec61131_3_st.language()))
        self.root = parser.parse(projected).root_node
        if self.root.has_error:
            stack = [self.root]
            while stack:
                node = stack.pop()
                if node.type == 'ERROR' or node.is_missing:
                    offset = self.span(node).start
                    raise STParseError(f'Invalid or missing ST syntax at offset {offset}: {node.type}', offset)
                stack.extend(reversed(node.children))
            raise STParseError('Invalid ST syntax')

    def text(self, node):
        return self.raw[node.start_byte:node.end_byte].decode('utf-8') if node is not None else ''

    def span(self, node):
        start = len(self.raw[:node.start_byte].decode('utf-8')) - len(self.prefix)
        end = len(self.raw[:node.end_byte].decode('utf-8')) - len(self.prefix)
        return self.range(start, end)

    def range(self, start, end):
        start = min(len(self.source), max(0, start))
        end = min(len(self.source), max(start, end))
        before = self.source[:start]
        return SourceRange(start, end, before.count('\n') + 1, len(before.rsplit('\n', 1)[-1]) + 1)

    def expression(self, start, end, *values):
        span = self.range(start, end)
        return Node((*values, span.start), span)

    def node(self, node, *values, expression=False):
        span = self.span(node)
        return Node((*values, span.start) if expression else values, span)

    def expr(self, node, depth=0):
        if depth > 100:
            raise STUncovered('Expression nesting exceeds the inspection limit')
        kind, text = node.type, self.text(node)
        if kind in {'binary_expression', 'unary_expression'}:
            # Rebind the recognized operator stream to the GX table. Parentheses
            # and postfix nodes remain indivisible operands, preserving grouping.
            stream = []
            def flatten(n, level=0):
                if level > 100:
                    raise STUncovered('Expression nesting exceeds the inspection limit')
                if n.type == 'binary_expression':
                    flatten(n.child_by_field_name('left'), level + 1)
                    stream.append(self.text(n.child_by_field_name('operator')).upper())
                    flatten(n.child_by_field_name('right'), level + 1)
                elif n.type == 'unary_expression':
                    operator = n.child_by_field_name('operator')
                    stream.append(('prefix', self.text(operator).upper(), self.span(operator).start))
                    flatten(n.child_by_field_name('operand'), level + 1)
                elif n.type in {'integer_literal', 'real_literal'} and self.text(n).startswith(('-', '+')):
                    span = self.span(n)
                    stream.extend([('prefix', self.text(n)[0], span.start),
                        self.expression(span.start + 1, span.end, 'atom', self.text(n)[1:])])
                else:
                    stream.append(self.expr(n, depth + 1))
            flatten(node)
            precedence = {key: value for key, value in operator_binding()['precedence'].items() if key != 'NOT'}
            cursor = 0
            def bind(priority=0, level=0):
                nonlocal cursor
                if level > 100:
                    raise STUncovered('Expression nesting exceeds the inspection limit')
                item = stream[cursor]
                cursor += 1
                if isinstance(item, tuple) and item[0] == 'prefix':
                    operand = bind(8, level + 1)
                    left = self.expression(item[2], operand.span.end, 'unary', item[1], operand)
                else:
                    left = item
                while cursor < len(stream) and isinstance(stream[cursor], str) and precedence.get(stream[cursor], 0) > priority:
                    op = stream[cursor]
                    cursor += 1
                    right = bind(precedence[op], level + 1)
                    left = self.expression(left.span.start, right.span.end, 'binary', op, left, right)
                return left
            return bind()
        if kind == 'parenthesized_expression':
            value = self.expr(next(n for n in node.named_children if n.type != 'comment'), depth + 1)
            span = self.span(node)
            return Node((*value[:-1], span.start), span)
        if kind == 'identifier' or kind.endswith('_literal') or kind == 'direct_address':
            return self.node(node, 'atom', text.upper() if kind in {'identifier', 'direct_address'} else text, expression=True)
        if kind == 'member_access_expression':
            base = self.expr(node.child_by_field_name('object'), depth + 1)
            member = self.text(node.child_by_field_name('member')).upper()
            return (self.node(node, 'atom', base[1] + '.' + member, expression=True) if base[0] == 'atom'
                    else self.node(node, 'member', base, member, expression=True))
        if kind == 'index_expression':
            return self.node(node, 'index', self.expr(node.child_by_field_name('object'), depth + 1),
                [self.expr(n, depth + 1) for n in node.children_by_field_name('index') if n.is_named], expression=True)
        if kind == 'call_expression':
            function = self.expr(node.child_by_field_name('function'), depth + 1)
            if function[0] != 'atom':
                raise STUncovered('Indirect ST callable is not bound', self.span(node).start)
            args = []
            for argument in node.child_by_field_name('arguments').named_children:
                if argument.type == 'named_argument':
                    name = self.text(argument.child_by_field_name('name')).upper()
                    value = argument.child_by_field_name('value')
                    direction = '=>' if any(c.type == '=>' for c in argument.children) else ':='
                    args.append(self.node(argument, 'named', name, direction, self.expr(value, depth + 1), expression=True))
                elif argument.type != 'comment':
                    args.append(self.expr(argument, depth + 1))
            return self.node(node, 'call', function[1], args, expression=True)
        if kind == 'structure_initializer':
            return self.node(node, 'structure', [(self.text(n.child_by_field_name('name')).upper(),
                self.expr(n.child_by_field_name('value'), depth + 1)) for n in node.named_children
                if n.type == 'structure_initializer_field'], expression=True)
        if kind == 'array_repetition':
            count = node.child_by_field_name('count')
            return self.node(node, 'array_repeat', self.expr(count, depth + 1),
                [self.expr(n, depth + 1) for n in node.named_children if n != count and n.type != 'comment'], expression=True)
        raise STUncovered('Recognized ST expression is not mapped: ' + kind, self.span(node).start)

    def statements(self, nodes, depth=0):
        if depth > 80:
            raise STUncovered('Statement nesting exceeds the inspection limit')
        result = []
        for node in nodes:
            kind = node.type
            def expr(field):
                return self.expr(node.child_by_field_name(field))
            def body(field='body'):
                return self.statements(node.children_by_field_name(field), depth + 1)
            if kind in {'empty_statement', 'comment', 'pragma'}:
                if kind == 'pragma':
                    raise STUncovered('ST pragma semantics are not mapped', self.span(node).start)
                continue
            if kind == 'assignment_statement':
                result.append(self.node(node, 'assign', expr('left'), expr('right')))
            elif kind == 'invocation_statement':
                result.append(self.node(node, 'invoke', self.expr(node.named_children[0])))
            elif kind == 'if_statement':
                arms, otherwise = [(expr('condition'), body('consequence'))], []
                for child in node.named_children:
                    if child.type == 'elsif_clause':
                        arms.append((self.expr(child.child_by_field_name('condition')),
                            self.statements(child.children_by_field_name('body'), depth + 1)))
                    elif child.type == 'else_clause':
                        otherwise = self.statements(child.children_by_field_name('body'), depth + 1)
                result.append(self.node(node, 'if', arms, otherwise))
            elif kind == 'case_statement':
                arms, otherwise = [], []
                for child in node.named_children:
                    if child.type == 'case_clause':
                        selections = []
                        for value in child.named_children:
                            if value.type != 'case_value':
                                continue
                            value = value.named_children[0]
                            selections.append((self.expr(value.child_by_field_name('lower')), self.expr(value.child_by_field_name('upper')))
                                if value.type == 'subrange' else (self.expr(value), None))
                        arms.append((selections, self.statements(child.children_by_field_name('body'), depth + 1)))
                    elif child.type == 'else_clause':
                        otherwise = self.statements(child.children_by_field_name('body'), depth + 1)
                result.append(self.node(node, 'case', expr('subject'), arms, otherwise))
            elif kind == 'for_statement':
                step = node.child_by_field_name('step')
                result.append(self.node(node, 'for', expr('control'), expr('start'), expr('end'),
                    self.expr(step) if step else self.node(node, 'atom', '1', expression=True), body()))
            elif kind in {'while_statement', 'repeat_statement'}:
                result.append(self.node(node, kind.removesuffix('_statement'), expr('condition'), body()))
            elif kind == 'exit_statement':
                result.append(self.node(node, 'exit'))
            elif kind == 'return_statement':
                value = node.child_by_field_name('value')
                result.append(self.node(node, 'return', self.expr(value) if value else None))
            else:
                raise STUncovered('Recognized ST statement is not mapped: ' + kind, self.span(node).start)
        return result

    def declarations(self, block):
        result = []
        class_name = block.type.upper().replace('_BLOCK', '')
        if class_name not in {'VAR', 'VAR_INPUT', 'VAR_OUTPUT', 'VAR_IN_OUT', 'VAR_GLOBAL', 'VAR_EXTERNAL', 'VAR_TEMP'}:
            raise STUncovered('Declaration class is not mapped: ' + class_name, self.span(block).start)
        qualifiers = [self.text(n).upper() for n in block.named_children if n.type == 'var_qualifier_list']
        if any('CONSTANT' in q for q in qualifiers):
            class_name += '_CONSTANT'
        if any('RETAIN' in q for q in qualifiers):
            if class_name != 'VAR' or any('NON_RETAIN' in q for q in qualifiers):
                raise STUncovered('RETAIN declaration combination is not mapped', self.span(block).start)
            class_name = 'VAR_RETAIN'
        for node in block.named_children:
            if node.type == 'pragma':
                raise STUncovered('Declaration pragma semantics are not mapped', self.span(node).start)
            if node.type != 'variable_declaration':
                continue
            initial_fields = node.children_by_field_name('initial_value')
            initial_nodes = [n for n in initial_fields if n.is_named and n.type != 'comment']
            initial_text = self.raw[initial_fields[0].start_byte:initial_fields[-1].end_byte].decode('utf-8') if initial_fields else ''
            initial = [self.expr(n) for n in initial_nodes]
            if initial_text.startswith('['):
                initial = self.expression(self.span(initial_fields[0]).start, self.span(initial_fields[-1]).end, 'array', initial)
            else:
                initial = initial[0] if initial else None
            for name in node.children_by_field_name('names'):
                if name.is_named:
                    result.append(Declaration(self.text(name), self.text(node.child_by_field_name('type')), class_name,
                        initial, initial_text, self.text(node.child_by_field_name('address')), self.span(name)))
        return result

    def convert(self):
        units, globals_, types = [], [], {}
        for node in self.root.named_children:
            kind = node.type
            if kind == 'comment':
                continue
            if kind == 'type_declaration':
                for definition in node.named_children:
                    if definition.type != 'type_definition':
                        continue
                    name = self.text(definition.child_by_field_name('name')).upper()
                    value = definition.child_by_field_name('definition')
                    if value.type == 'structure_type_inline':
                        types[name] = [Declaration(self.text(n.child_by_field_name('name')),
                            self.text(n.child_by_field_name('type')), initial=self.expr(n.child_by_field_name('default'))
                            if n.child_by_field_name('default') else None,
                            initial_text=self.text(n.child_by_field_name('default')), span=self.span(n))
                            for n in value.named_children if n.type == 'structure_field']
                    else:
                        types[name] = self.text(value)
            elif kind.startswith('var_'):
                globals_.extend(self.declarations(node))
            elif kind in {'program_declaration', 'function_declaration', 'function_block_declaration'}:
                declarations = [d for block in node.named_children if block.type.startswith('var_') for d in self.declarations(block)]
                known = {'identifier', 'elementary_type', 'comment'}
                for child in node.named_children:
                    if (child.type not in known and child != node.child_by_field_name('return_type')
                            and not child.type.startswith('var_') and child not in node.children_by_field_name('body')):
                        raise STUncovered('Recognized POU feature is not mapped: ' + child.type, self.span(child).start)
                unit = Unit(self.text(node.child_by_field_name('name')), kind.removesuffix('_declaration'),
                    self.statements(node.children_by_field_name('body')), declarations,
                    self.text(node.child_by_field_name('return_type')), source=self.source)
                headers = [child for child in node.named_children if child.type.startswith('var_')]
                end_header = headers[-1].end_byte if headers else (node.child_by_field_name('return_type') or node.child_by_field_name('name')).end_byte
                closing = [child for child in node.children if child.type.upper() in {'END_PROGRAM', 'END_FUNCTION', 'END_FUNCTION_BLOCK'}]
                unit.body_text = self.raw[end_header:(closing[-1].start_byte if closing else node.end_byte)].decode('utf-8').strip()
                units.append(unit)
            else:
                raise STUncovered('Recognized ST unit is not mapped: ' + kind, self.span(node).start)
        for unit in units:
            unit.types = types
        return SourceUnit(units, globals_, types)


def parse_unit(source):
    return _Adapter(source).convert()


def parse_body(source):
    return _Adapter(source, body=True).convert().units[0]


def parse_initializer(source, data_type):
    # Initializers are a declaration production, not a general expression.
    # This also covers the native array repetition form, e.g. [3(0)].
    return parse_body(f'VAR __st_initial_value : {data_type} := {source}; END_VAR').declarations[0].initial


def parse_source(source):
    # Only choose the input form, never a second parsing backend.
    head = re.sub(r'(?s)\(\*.*?\*\)|//[^\n]*', '', source).lstrip() if isinstance(source, str) else ''
    return parse_unit(source) if re.match(r'(PROGRAM|FUNCTION_BLOCK|FUNCTION|TYPE)\b', head, re.I) else SourceUnit([parse_body(source)], [], {})


class Parser:
    """Compatibility for consumers of the previous body parser."""
    def __init__(self, source):
        self.source, self.statements, self.unit = source, 0, None

    def body(self):
        parsed = parse_source(self.source)
        self.parsed = parsed
        programs = [unit for unit in parsed.units if unit.kind == 'program']
        if len(parsed.units) == 1:
            self.unit = parsed.units[0]
        elif len(programs) == 1:
            self.unit = programs[0]
        else:
            raise STUncovered('Inspection requires one selected POU')
        def count(body, *, containers):
            total = len(body) if containers else sum(node[0] not in {'if', 'case', 'for', 'while', 'repeat'} for node in body)
            for node in body:
                if node[0] == 'if':
                    total += sum(count(b, containers=containers) for _, b in node[1]) + count(node[2], containers=containers)
                elif node[0] == 'case':
                    total += sum(count(b, containers=containers) for _, b in node[2]) + count(node[3], containers=containers)
                elif node[0] in {'for', 'while', 'repeat'}:
                    total += count(node[-1], containers=containers)
            return total
        self.statements = count(self.unit.body, containers=False)
        self.ast_statement_count = count(self.unit.body, containers=True)
        return self.unit.body
