"""CPU-selected callable interfaces bound to the existing graph and labels.

Source formals describe graphic connections; execution and IN_OUT copyback are
not inferred here. Failed or ambiguous bindings remain explicit read issues.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
import re

from .container_writer import validate_cfb_streams
from .integrity import read_project_hdb
from .declarations import (BASIC_TYPES, CLASS_CODES, _type_parts, local_table_for,
                           parse_declarations, parse_structure_declarations, resolve_label)
from .library_sources import decode_library_archive, parse_library_source, parse_library_declarations
from .models import GXWFormatError, NodeKind
from .project_metadata import (current_rows, project_stream_mapping, read_project_text_context,
                               read_project_compile_options, project_source_groups, existing_source_groups)
from .semantic import (FunctionBlockCategory, FunctionBlockPortSpec, FunctionBlockSpec,
                       SemanticPortRole)


CLASS = {'VAR': 1, 'VAR CONSTANT': 2, 'VAR_INPUT': 3, 'VAR_OUTPUT': 4,
         'VAR_IN_OUT': 5, 'VAR_INPUT CONSTANT': 11, 'VAR_IN_EXT': 12}


def read_project_declarations(streams, *, source_groups=None, overrides=None):
    """Read independent declaration tables without hiding usable source graphs.

    A foreign/unsupported table remains a stream in the saved project. It does
    not become an empty declaration table, and its missing names cannot supply
    an interface or authorize edits that depend on that scope.
    """
    documents, issues = {}, []
    owners = {group['labels']: name for name, group in (source_groups or {}).items()}
    for name, value in streams.items():
        if not name.endswith(('.lh', '.gh', '.lnl')):
            continue
        if name in (overrides or {}):
            documents[name] = overrides[name]
            continue
        try:
            from .source_header import source_payload_offset
            source = streams.get(owners.get(name))
            kind = source[source_payload_offset(source)] if source is not None else None
            documents[name] = parse_declarations(value, logical_name=name,
                source_kind=240 if kind == 240 else None)
        except GXWFormatError as error:
            issues.append({'code': 'declaration_source_gap', 'stream': name, 'message': str(error)})
    documents.update(overrides or {})
    return documents, issues

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


def interface(definition, input_count=None, *, resolve_type=None, graphical=False):
    if definition.get('source_kind') == 'project_labels':
        # The original compiler frontend orders project declarations by their
        # saved number, independently of displayed row order and live IDs.
        # Reordered author rows and the captured compiler buffer establish
        # this binding; the number is not an identity for editable local rows.
        # Ordinary inputs, ordinary outputs and IN_OUT have separate ordered
        # sequences. Native FX3G controls retain shared numbers across groups.
        declared = definition['declarations'].rows
        for classes in ((3, 11, 12), (4,), (5,)):
            group = [r for r in declared if r.class_code in classes]
            if len({r.record_id for r in group}) != len(group):
                raise GXWFormatError('ambiguous saved declaration order in project callable')
        rows = [dict(name=r.name, class_code=r.class_code,
                     declared_type=r.data_type, source_offset=r.offset)
                for r in sorted(declared, key=lambda r: r.record_id)]
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
    document = definition.get('declarations')
    if (graphical and document is not None and document.owner_pou_status is not None
            and document.owner_pou_status & 4):
        # Native ReadCallableFormals synthesizes this pair from the POU option
        # bit, before enumerating declarations. It is absent from the saved
        # label rows; counting rows alone drops the first input/output pair.
        if not any(row['name'].upper() in ('EN', 'ENO') for row in rows):
            rows = [dict(name='EN', class_code=3, declared_type='BOOL', source_offset=None),
                    dict(name='ENO', class_code=4, declared_type='BOOL', source_offset=None), *rows]
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
        if len(expanded) != 1:
            raise GXWFormatError('unobserved mixed extensible inputs')
        repeat = input_count - len(inouts) - len(fixed)
        if not 1 <= repeat <= 256:
            raise GXWFormatError('extensible input arity outside observed bound')
        # MUX has a fixed selector before VAR_IN_EXT; ADD has only the repeated
        # input (or an EN prefix). Expand the declaration at its actual slot.
        inputs = [formal for row in inputs for formal in
                  ([row] * repeat if row['class_code'] == 12 else [row])]
    inputs += inouts
    return_type = definition['return_type_raw']
    if return_type is not None and return_type != "@'VOID'":
        outputs = eno + [dict(name=definition['name'], class_code=4,
                              declared_type=return_type, source_offset=None)] + [r for r in outputs if r not in eno]
    outputs += inouts
    if input_count is not None and len(inputs) != input_count:
        raise GXWFormatError('source formal interface differs from graph input count')
    for r in inputs + outputs:
        r['type_code'] = (resolve_type or type_mask)(r['declared_type'])
    return inputs, outputs


def sorted_ports(node):
    """Bind observed callable ports by side and ordinate; retain source indexes."""
    width, height = node.bbox.right-node.bbox.left, node.bbox.bottom-node.bbox.top
    # Bit 0x04 marks a function's constant-only input. Original FMOV nodes
    # retain code 7 for the CPU-selected VAR_INPUT CONSTANT formal ``n``.
    input_flags = (3, 7) if node.kind_code == 1 else (1,)
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


def _user_library_owners(metadata, streams):
    """Pair native library labels/source by current folder and namespace.

    Folder numbers restart in each library. The declaration metadata row owns
    the POU name; the source filename and embedded label owner are not keys.
    Unknown groups remain read issues, without choosing an arbitrary source.
    """
    rows, _ = current_rows(metadata, 'DSPROJECTDATA', 'D_Projectdata')
    groups = defaultdict(list)
    descriptors = set()
    for row in rows:
        fields = {key: value.text.strip() for key, value in row.fields().items()}
        if fields.get('bScrapFlag', 'false').lower() in ('true', '1'):
            continue
        name = fields.get('szName', '')
        if (fields.get('ucProductType') == '1' and fields.get('ucFolderType') == '80'
                and fields.get('ucReserve') == '0' and fields.get('ucFileType') == '0' and name.endswith('.lif')):
            # Native user-library properties are counted UTF-16 metadata,
            # not the compressed source archives sharing the .lif extension.
            descriptors.add(name)
        if fields.get('ucFolderType') != '92':
            continue
        namespace = name.rsplit('\\', 1)[-1].rsplit('.', 1)[0] if '\\' in name else None
        key = tuple(fields.get(field) for field in ('ucProductType', 'uiFolderNo', 'ucReserve')) + (namespace,)
        groups[key].append(fields)
    owners, issues = {}, []
    for key, members in groups.items():
        labels = [m['szName'] for m in members if m.get('ucFileType') == '1' and m['szName'].endswith('.lnl')]
        sources = [m['szName'] for m in members if m.get('ucFileType') == '2' and m['szName'].endswith('.lnb')]
        if (key[0] != '1' or not key[1] or key[2] != '0' or not key[3] or len(members) != 2
                or len(labels) != 1 or len(sources) != 1
                or labels[0] not in streams or sources[0] not in streams):
            issues.append({'code': 'user_library_owner_gap', 'streams': [m.get('szName', '') for m in members],
                           'message': 'missing or ambiguous native library declaration/source group'})
            continue
        stem = labels[0].rsplit('\\', 1)[0]
        name, separator, _ = stem.rpartition('.')
        if not separator or not name:
            issues.append({'code': 'user_library_owner_gap', 'stream': labels[0],
                           'message': 'native library declaration identity is outside observed names'})
            continue
        owners[labels[0]] = {'name': name, 'library': key[3], 'source_program': sources[0]}
    for name in streams:
        if name.endswith('.lnl') and name not in owners and not any(
                name == issue.get('stream') or name in issue.get('streams', []) for issue in issues):
            issues.append({'code': 'user_library_owner_gap', 'stream': name,
                           'message': 'native library declaration has no current source group'})
    return owners, issues, descriptors


def _user_library_structures(metadata, streams, descriptors):
    """Read current native type owners without choosing an import precedence."""
    rows, _ = current_rows(metadata, 'DSPROJECTDATA', 'D_Projectdata')
    namespaces = {name.removesuffix('.lif').casefold() for name in descriptors if name in streams}
    structures = defaultdict(list)
    project_names = set()
    issues = []
    for row in rows:
        fields = {key: value.text.strip() for key, value in row.fields().items()}
        if fields.get('bScrapFlag', 'false').lower() in ('true', '1'):
            continue
        logical = fields.get('szName', '')
        if logical.endswith('.str'):
            project_names.add(logical.removesuffix('.str').casefold())
        if not logical.endswith('.lns'):
            continue
        name, separator, namespace = logical.rpartition('\\')
        namespace = namespace.removesuffix('.lns').casefold()
        key = (namespace, name.casefold())
        valid = (tuple(fields.get(field) for field in
            ('ucProductType', 'ucFolderType', 'ucReserve', 'ucFileType')) == ('1', '90', '0', '0')
            and separator and name and '\\' not in name and '/' not in logical
            and namespace in namespaces and logical in streams)
        document = None
        try:
            if not valid:
                raise GXWFormatError('native library structure has no current namespace/source owner')
            document = parse_structure_declarations(streams[logical], logical_name=logical)
        except GXWFormatError as error:
            issues.append({'code': 'user_library_structure_gap', 'stream': logical, 'message': str(error)})
        # Failed or duplicate owners remain candidates so an unqualified caller
        # cannot silently borrow a same-named type from another import.
        structures[key].append(document)
    for (namespace, name), documents in structures.items():
        if len(documents) != 1:
            issues.append({'code': 'user_library_structure_gap', 'library': namespace, 'type_name': name,
                           'message': 'ambiguous current native library structure owner'})
    return dict(structures), frozenset(project_names), issues


def _project_structures(metadata, streams):
    """Bind .str bytes to their current project type role, including gaps."""
    rows, _ = current_rows(metadata, 'DSPROJECTDATA', 'D_Projectdata')
    structures, issues = defaultdict(list), []
    expected = {'ucProductType': '1', 'ucFolderType': '4', 'uiFolderNo': '0',
                'ucReserve': '0', 'ucFileType': '1', 'bStructuredStorage': 'true'}
    for row in rows:
        fields = {key: value.text.strip() for key, value in row.fields().items()}
        logical = fields.get('szName', '')
        if not logical.endswith('.str') or fields.get('bScrapFlag', 'false').lower() in ('true', '1'):
            continue
        name = logical.removesuffix('.str')
        document = None
        try:
            if (any(fields.get(key) != value for key, value in expected.items())
                    or re.fullmatch(r'[^\W\d]\w*', name) is None or logical not in streams):
                raise GXWFormatError('project structure has no current native type/source owner')
            document = parse_structure_declarations(streams[logical], logical_name=logical)
        except GXWFormatError as error:
            issues.append({'code': 'project_structure_gap', 'stream': logical, 'message': str(error)})
        # A damaged project claim must not fall back to a same-named import.
        structures[name.casefold()].append(document)
    for name, documents in structures.items():
        if len(documents) != 1:
            issues.append({'code': 'project_structure_gap', 'type_name': name,
                           'message': 'ambiguous current project structure owner'})
    return dict(structures), issues


class ProjectCallableSources:
    """Read-only callable/declaration context for one selected native program."""

    def __init__(self, cpu, program_name, declarations, library_sources, *, global_variable_hiding=False,
                 user_library_owners=None, source_groups=None, library_structures=None,
                 project_structure_names=(), project_structures=None):
        self.cpu, self.program_name, self.declarations = cpu, program_name, declarations
        self.user_library_owners = dict(user_library_owners or {})
        self.source_groups = dict(source_groups) if source_groups is not None else None
        self.library_structures = dict(library_structures or {})
        self.project_structures = dict(project_structures or {})
        self.project_structure_names = frozenset(project_structure_names) | frozenset(self.project_structures)
        self.global_variable_hiding = global_variable_hiding
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
            # Native user FUNCTION labels carry 0x1000003. The distinct
            # 0x1000001 control does not acquire a FUNCTION return interface.
            kind = {0x1000003: 'FUNCTION', 0x1000002: 'FUNCTION_BLOCK'}.get(document.owner_pou_type)
            if kind is None:
                continue
            library_owner = self.user_library_owners.get(stream)
            if library_owner is not None:
                owner = library_owner['name']
            elif self.source_groups is not None:
                sources = [name for name, group in self.source_groups.items() if group['labels'] == stream]
                if len(sources) != 1:
                    continue
                try:
                    local_table_for(declarations, sources[0], self.source_groups)
                except GXWFormatError:
                    self.issues.append({'code': 'project_callable_owner_gap', 'stream': stream,
                                        'message': 'project callable owner and declaration stream differ'})
                    continue
                owner = document.owner_name
            elif stream.endswith('.Labels.lh'):
                owner = stream.removesuffix('.Labels.lh')
            else:
                continue
            if document.scope != 'local' or document.owner_name != owner:
                self.issues.append({'code': 'project_callable_owner_gap', 'stream': stream,
                                    'message': 'project callable owner and declaration stream differ'})
                continue
            definition = dict(name=owner, kind=kind, source_kind='project_labels',
                              offset=0, return_type_raw=document.owner_return_type,
                              declarations=document)
            if library_owner is not None:
                definition.update(library_owner)
            self.catalog[owner.casefold()].append((stream, definition))

    @classmethod
    def from_project(cls, raw, program_name, declarations=None):
        outer = validate_cfb_streams(raw)
        mapping = project_stream_mapping(outer)
        payloads = read_project_hdb(outer).payloads
        streams = {name: payloads[key] for name, key in mapping.items() if key in payloads}
        contexts = [read_project_text_context(value) for name, value in streams.items() if name.endswith('.prj')]
        if len(contexts) != 1:
            raise GXWFormatError('one project CPU context is required for source library selection')
        libraries, issues = {}, []
        source_groups, source_issues = existing_source_groups(outer['projectdatalist.xml'], streams)
        issues.extend(source_issues)
        declarations, declaration_issues = read_project_declarations(streams,
            source_groups=source_groups, overrides=declarations)
        issues.extend(declaration_issues)
        for program in list(source_groups):
            try:
                if program not in streams or local_table_for(declarations, program, source_groups) is None:
                    raise GXWFormatError('native project source or declaration payload is missing')
            except GXWFormatError as error:
                issues.append({'code': 'project_source_owner_gap', 'stream': program, 'message': str(error)})
                del source_groups[program]
        if program_name not in source_groups and not any(
                issue.get('stream') == program_name or program_name in issue.get('streams', []) for issue in issues):
            issues.append({'code': 'project_source_owner_gap', 'stream': program_name,
                           'message': 'selected native source has no unambiguous project declaration group'})
        user_library_owners, owner_issues, descriptors = _user_library_owners(outer['projectdatalist.xml'], streams)
        issues.extend(owner_issues)
        structures, project_types, structure_issues = _user_library_structures(
            outer['projectdatalist.xml'], streams, descriptors)
        issues.extend(structure_issues)
        project_structures, project_issues = _project_structures(outer['projectdatalist.xml'], streams)
        issues.extend(project_issues)
        for name, value in streams.items():
            if name.endswith('.lif') and name not in descriptors:
                try:
                    libraries[name] = _library_text(value)
                except ValueError as error:
                    issues.append({'code': 'library_archive_gap', 'stream': name, 'message': str(error)})
        project_name, project_raw = next((name, value) for name, value in streams.items() if name.endswith('.prj'))
        try:
            hiding = read_project_compile_options(project_raw)['global_variable_hiding']
        except GXWFormatError as error:
            hiding = None
            issues.append({'code': 'project_compile_option_gap', 'stream': project_name, 'message': str(error)})
        result = cls(contexts[0]['cpu'], program_name, declarations, libraries, global_variable_hiding=hiding,
                     user_library_owners=user_library_owners, source_groups=source_groups,
                     library_structures=structures, project_structure_names=project_types,
                     project_structures=project_structures)
        result.issues[:0] = issues
        result.declaration_gaps = {issue['stream'] for issue in declaration_issues}
        result.declaration_owners = {group['labels']: name for name, group in
                                    source_groups.items()}
        result.source_streams = streams
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
        for stream in getattr(self, 'declaration_gaps', ()):
            owner = getattr(self, 'declaration_owners', {}).get(stream)
            if stream.endswith('.gh') or owner == self.program_name:
                raise GXWFormatError('current label scope contains an unreadable declaration table: ' + stream)
        local = self.local_table
        if self.source_groups is not None and local is None:
            return None
        return resolve_label(self.declarations, self.program_name, symbol,
                             global_variable_hiding=self.global_variable_hiding, local_table=local)

    def value_type_identity(self, data_type):
        """Keep composite owner and array bounds in the same type identity."""
        base, bounds = _type_parts(data_type)
        if base.upper() in BASIC_TYPES or base.upper() == 'LREAL' or base.upper().startswith('ANY'):
            identity = base.upper()
        else:
            try:
                identity = 'STRUCT:' + self.structure(base).logical_name + ':' + base
            except GXWFormatError:
                stream, _ = self.definition(base, 'FUNCTION_BLOCK')
                identity = 'FB:' + stream + ':' + base
        return ('ARRAY' + repr([list(value) for value in bounds]) + ':' if bounds else '') + identity.upper()

    def actual_value(self, symbol, *, literal_rules=None):
        """Bind an FBD actual to its source, value domain and storage separately.

        Reading nested declarations does not authorize creating or rewriting
        their native layout. Existing writer capability checks still apply.
        """
        from plc.fbd_semantics import parse_literal, FBDSemanticError
        from plc.validation import (parse_device_address, is_bit_device, _validate_device_address,
                                    _validate_writable_device, device_address_radix, DEVICE_ADDRESS_RE,
                                    device_address_status)
        def checked_address(address):
            if device_address_status(address, self.cpu) == 'unverified':
                raise FBDSemanticError('fbd_device_coverage', 'Device namespace is absent from the current CPU rule subset',
                                       severity='unverified', expression=text, device=address, plc_model=self.cpu)
            _validate_device_address(address, '$.fbd.actual', self.cpu)
        text = str(symbol).strip()
        root = re.match(r'[^\W\d]\w*', text)
        binding = self.label(root[0]) if root else None
        if binding is not None:
            table, declaration = binding
            base, bounds = _type_parts(declaration.data_type)
            constant = declaration.class_code in (2, 9, 11)
            current_type = declaration.data_type
            result = dict(text=text, kind='label', source_table=table, source_offset=declaration.offset,
                          declaration=declaration.name, declared_type=declaration.data_type,
                          class_code=declaration.class_code, constant=constant, readable=True,
                          writable=declaration.class_code not in (2, 3, 9, 11, 12),
                          storage=dict(device=declaration.device or None, iec_address=declaration.iec_address or None),
                          member_path=[], issues=[])
            remaining = text[root.end():]
            while remaining:
                if remaining.startswith('['):
                    match = re.match(r'\[([^\]]+)\]', remaining)
                    if match is None or not bounds:
                        raise FBDSemanticError('fbd_actual_subscript', 'Array index has no matching declaration', expression=text)
                    indices = []
                    for token in match[1].split(','):
                        token = token.strip()
                        if re.fullmatch(r'[+-]?\d+', token):
                            indices.append(int(token))
                        else:
                            index_binding = self.label(token)
                            if index_binding is None or index_binding[1].class_code not in (2, 9):
                                raise FBDSemanticError('fbd_actual_index_coverage', 'Index requires a known integer constant',
                                                       severity='unverified', expression=text, index=token)
                            value = parse_literal(index_binding[1].initial_value, self.cpu, rules=literal_rules)
                            if not value or type(value.get('value')) is not int:
                                raise FBDSemanticError('fbd_actual_subscript', 'Array index constant is not an integer', expression=text)
                            indices.append(value['value'])
                    if len(indices) != len(bounds) or any(not lo <= index <= hi for index, (lo, hi) in zip(indices, bounds)):
                        raise FBDSemanticError('fbd_actual_subscript', 'Array index is outside the declared shape',
                                               expression=text, shape=[list(v) for v in bounds], indices=indices)
                    result['member_path'].append(dict(indices=indices, shape=[list(v) for v in bounds]))
                    bounds, remaining, current_type = [], remaining[match.end():], base
                    continue
                match = re.match(r'\.([^\W\d]\w*)', remaining)
                if not match or bounds:
                    raise FBDSemanticError('fbd_actual_syntax', 'Member access needs a scalar declared owner', expression=text)
                member_name = match[1]
                try:
                    structure = self.structure(base)
                except GXWFormatError:
                    try:
                        stream, definition = self.definition(base, 'FUNCTION_BLOCK')
                    except GXWFormatError as error:
                        raise FBDSemanticError('fbd_actual_member', 'Member owner has no unique definition', expression=text) from error
                    document = definition.get('declarations')
                    if document is None:
                        raise FBDSemanticError('fbd_actual_member_coverage', 'FB member declaration is unavailable',
                                               severity='unverified', expression=text)
                    matches = [row for row in document.rows if row.name.casefold() == member_name.casefold()]
                    if len(matches) != 1 or matches[0].class_code != CLASS_CODES['VAR_OUTPUT']:
                        raise FBDSemanticError('fbd_actual_member_access', 'Only a declared FB output can be read as a member', expression=text)
                    member = matches[0]
                    result['writable'] = False
                else:
                    stream = structure.logical_name
                    matches = [row for row in structure.members if row.name.casefold() == member_name.casefold()]
                    if len(matches) != 1:
                        raise FBDSemanticError('fbd_actual_member', 'Missing or ambiguous declared structure member', expression=text)
                    member = matches[0]
                result['member_path'].append(dict(source_stream=stream, source_offset=member.offset, name=member.name))
                base, bounds = _type_parts(member.data_type)
                current_type = member.data_type
                remaining = remaining[match.end():]
            result.update(data_type=current_type,
                          shape=[list(value) for value in bounds])
            capacity = re.fullmatch(r'STRING\s*\[\s*(\d+)\s*\]', current_type, re.I)
            if capacity:
                result['string_capacity'] = int(capacity[1])
            result['types'] = [self.value_type_identity(current_type)]
            if constant and not result['member_path']:
                value = parse_literal(declaration.initial_value, self.cpu, rules=literal_rules)
                if value is not None:
                    if base.upper() not in value['types']:
                        raise FBDSemanticError('fbd_constant_type', 'Declared constant initializer does not fit its type', expression=text)
                    if capacity and len(value['value']) > int(capacity[1]):
                        raise FBDSemanticError('fbd_constant_type', 'Declared string constant exceeds its capacity', expression=text)
                    result['value'] = value['value']
                    result['syntax'] = value['syntax']
            return result
        grouped = re.fullmatch(r'K([1-8])([A-Z]+[0-9A-F]+)', text, re.I)
        if grouped:
            address, digits = grouped[2].upper(), int(grouped[1])
            checked_address(address)
            if not is_bit_device(address, self.cpu):
                raise FBDSemanticError('fbd_actual_device_group', 'Digit specification needs a bit device', expression=text)
            prefix, index = parse_device_address(address, self.cpu)
            last = index + digits * 4 - 1
            radix = device_address_radix(prefix, self.cpu)
            suffix = format(last, 'X') if radix == 16 else format(last, 'o') if radix == 8 else str(last)
            checked_address(prefix + suffix)
            types = ['INT', 'WORD'] if digits <= 4 else ['DINT', 'DWORD']
            writable = True
            try:
                _validate_writable_device(address, '$.fbd.actual', self.cpu)
            except ValueError:
                writable = False
            return dict(text=text, kind='device_group', types=types, constant=False, readable=True, writable=writable,
                        storage=dict(first=address, last=prefix + suffix, bits=digits * 4), shape=[], issues=[])
        literal = parse_literal(text, self.cpu, rules=literal_rules)
        if literal is not None:
            return literal
        if DEVICE_ADDRESS_RE.fullmatch(text):
            checked_address(text)
        if parse_device_address(text, self.cpu) is not None:
            _validate_device_address(text, '$.fbd.actual', self.cpu)
            bit = is_bit_device(text, self.cpu)
            writable = True
            try:
                _validate_writable_device(text, '$.fbd.actual', self.cpu)
            except ValueError:
                writable = False
            return dict(text=text, kind='device', types=['BOOL'] if bit else ['INT', 'WORD'],
                        constant=False, readable=True, writable=writable, shape=[], issues=[],
                        storage=dict(device=text.upper(), bits=1 if bit else 16), data_type='BOOL' if bit else None)
        raise FBDSemanticError('fbd_actual_unresolved', 'Actual is not bound to the current declarations or target grammar',
                               severity='unverified', expression=text)

    def call_storage_bits(self, node, formal=None):
        """Current MELSEC instruction device view, never a cast of a label."""
        if node.kind != NodeKind.FUNCTION:
            return None
        try:
            stream, definition = self.definition(node.symbol, 'FUNCTION')
        except GXWFormatError:
            return None  # Serialized operators have value ports, no MELSEC device span.
        if stream.casefold() != 'melsec.lif' or definition.get('source_kind') == 'project_labels':
            return None
        if formal is not None:
            from plc.fbd_semantics import language_rules
            return language_rules('FBD.TYPES', self.cpu).get('generic_storage_bits', {}).get(formal.get('data_type'))
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        from plc.instruction_constraints import _cpu_members
        forms = [DEFAULT_INSTRUCTION_REGISTRY.resolve_form(node.symbol, cpu=cpu) for cpu in _cpu_members(self.cpu)]
        if any(not form or 'instruction_width' not in form.spec.verified_fields for form in forms):
            return None
        widths = {form.spec.instruction_width for form in forms}
        return next(iter(widths)) if len(widths) == 1 and widths <= {16, 32} else None

    def callee_context(self, node):
        """Expose saved call conditions; unknown compiler option bits stay unknown."""
        kind = 'FUNCTION_BLOCK' if node.kind == NodeKind.FUNCTION_BLOCK else 'FUNCTION'
        name = node.type_name if node.kind == NodeKind.FUNCTION_BLOCK else node.symbol
        stream, definition = self.definition(name, kind)
        document = definition.get('declarations')
        logical = next((key for key, group in (self.source_groups or {}).items() if group['labels'] == stream), None)
        raw = getattr(self, 'source_streams', {}).get(logical)
        language = None
        if raw:
            from .source_header import source_payload_offset
            language = {193: 'st', 208: 'fbd', 1: 'ladder'}.get(raw[source_payload_offset(raw)])
        return dict(source_stream=stream, source_name=logical, language=language, definition=definition,
                    options=dict(use_en_eno=bool(document.owner_pou_status & 4)
                                 if document is not None and document.owner_pou_status is not None else None,
                                 use_macrocode=None, use_mc_mcr=None), raw=raw)

    def inout_output_usage(self, node, formal):
        """Return True/False only when the complete current callee can decide it."""
        context = self.callee_context(node)
        raw = context['raw']
        if not raw:
            return None, context['language']
        if context['language'] == 'st':
            from .text_pou import parse_st_pou
            from plc.st_frontend import parse_body, STUncovered
            try:
                unit = parse_body(parse_st_pou(raw).text)
            except (ValueError, STUncovered):
                return None, 'st'
            result = False
            def visit(value):
                nonlocal result
                if not isinstance(value, (tuple, list)):
                    return
                if value and value[0] == 'assign' and isinstance(value[1], (tuple, list)):
                    target = value[1]
                    while target and target[0] in ('index', 'member', 'field') and isinstance(target[1], (tuple, list)):
                        target = target[1]
                    if target[0] == 'atom' and target[1].casefold() == formal.casefold():
                        result = True
                if value and isinstance(value[0], str) and value[0] in {'call', 'call_stmt'} and result is not True:
                    result = None  # A nested callee may write an actual parameter.
                for child in value:
                    visit(child)
            visit(unit.body)
            return result, 'st'
        if context['language'] == 'fbd':
            from .structured_pou import parse_structured_pou
            program = parse_structured_pou(raw, logical_name=context['source_name'], preserve_unsupported_records=True)
            if program.unknown_records:
                return None, 'fbd'
            written = any(re.match(re.escape(formal) + r'(?:$|\.|\[)', n.symbol, re.I) and n.kind in
                       (NodeKind.OUTPUT, NodeKind.INOUT, NodeKind.COIL, NodeKind.COIL_SET, NodeKind.COIL_RESET)
                       for n in program.nodes)
            if written:
                return True, 'fbd'
            return (None if any(n.kind in (NodeKind.FUNCTION, NodeKind.FUNCTION_BLOCK) for n in program.nodes) else False), 'fbd'
        return None, context['language']

    @property
    def local_table(self):
        return local_table_for(self.declarations, self.program_name, self.source_groups)

    def declaration_kind(self, row):
        if row.type_code == 15:
            return 'function_block'
        if row.type_code == 0:
            try:
                self.definition(row.data_type,'FUNCTION_BLOCK')
            except GXWFormatError:
                pass
            else:
                return 'function_block'
        try:
            base, _ = _type_parts(row.data_type)
            self.structure(base)
        except (GXWFormatError, ValueError):
            pass
        else:
            return 'structure'
        return 'variable'

    def definition(self, name, kind):
        namespace = (self.source_groups or {}).get(self.program_name, {}).get('library')
        def local_matches(values):
            local=[entry for entry in values if namespace and entry[1].get('library')==namespace]
            return local or values
        matches = self.catalog.get(name.casefold(), [])
        if not matches and kind == 'FUNCTION':
            base = re.sub(r'-[1-9][0-9]*$', '', name)
            candidates = local_matches(self.catalog.get(base.casefold(), []))
            if len(candidates) == 1:
                definition = candidates[0][1]
                rows = (definition['declarations'].rows if definition.get('source_kind') == 'project_labels'
                        else parse_library_declarations(definition)['rows'])
                if any((row.class_code == 12 if hasattr(row, 'class_code') else
                        row['source_class'] == 'VAR_IN_EXT') for row in rows):
                    matches = candidates
        matches=local_matches(matches)
        if len(matches) != 1:
            raise GXWFormatError('missing or ambiguous CPU-selected source callable: ' + name)
        stream, definition = matches[0]
        if definition['kind'] != kind:
            raise GXWFormatError('graph kind differs from source definition')
        return stream, definition

    def structure(self, name, *, library=None):
        """Resolve one current type owner; a name alone is not a source."""
        key = name.casefold()
        namespace=(self.source_groups or {}).get(self.program_name,{}).get('library')
        if library is not None:
            candidates = self.library_structures.get((library.casefold(), key), ())
        elif namespace and (namespace.casefold(),key) in self.library_structures:
            candidates=self.library_structures[(namespace.casefold(),key)]
        elif key in self.project_structure_names:
            candidates = self.project_structures.get(key, ())
        else:
            candidates = [document for (_, type_name), documents in self.library_structures.items()
                          if type_name == key for document in documents]
        if len(candidates) != 1 or candidates[0] is None:
            raise GXWFormatError('missing or ambiguous current structure source: ' + name)
        return candidates[0]

    def structure_value(self, symbol):
        """Resolve global values, local VAR values and source-owned IN_OUT members.

        Source offsets identify saved records, not live native objects. This
        establishes type/bounds only; it neither allocates member devices nor
        infers current compilation, public check completion or PLC behavior.
        """
        known_types = self.project_structure_names | {name for _, name in self.library_structures}
        parsed = re.fullmatch(r'([^\W\d]\w*)(?:\[(-?[0-9]+)\])?(?:\.([^\W\d]\w*)(?:\[(-?[0-9]+)\])?)?', symbol)
        if parsed is None:
            root = re.match(r'([^\W\d]\w*)(?=\.|\[)', symbol)
            binding = self.label(root[1]) if root else None
            if binding is not None:
                try:
                    base_type, _ = _type_parts(binding[1].data_type)
                except ValueError as error:
                    raise GXWFormatError('unbounded structure root array integer') from error
                if base_type.casefold() in known_types:
                    raise GXWFormatError('structure member syntax outside observed flat literal indices')
            return None
        root_name, root_index_text, member_name, index_text = parsed.groups()
        binding = self.label(root_name)
        if binding is None:
            return None
        table, root = binding
        try:
            root_type, root_bounds = _type_parts(root.data_type)
        except ValueError as error:
            raise GXWFormatError('unbounded structure root array integer') from error
        if root_type.casefold() not in known_types:
            return None
        declaration = self.declarations[table]
        local_member = declaration.scope == 'local' and table == self.local_table
        if local_member:
            # Newly created FB formals have zero caches; edited formals can
            # retain their former WORD cache. Resolve both from the current
            # declaration and registered structure owner.
            local_root = (declaration.owner_pou_type in (0x1000000, 0x1000002)
                          and root.class_code == CLASS_CODES['VAR'])
            local_inout = (declaration.owner_pou_type == 0x1000002
                           and root.class_code == CLASS_CODES['VAR_IN_OUT'])
            cache_codes = (0, 4) if local_inout and root_bounds else (0,)
            if (not (local_root or local_inout) or local_inout and member_name is None
                    or root.unknown_u32 != 0
                    or local_root and (root.value_extension or root.device or root.iec_address or root.initial_value)
                    or root_bounds and (root_type.casefold() not in self.project_structures
                        or member_name is not None and root_index_text is None)
                    or local_root and member_name is None and root_index_text is not None
                    or root.type_code not in cache_codes or root.array_marker or root.type_reference):
                scope = 'VAR' if local_root else 'IN_OUT'
                raise GXWFormatError('structure member outside observed local ' + scope + ' scope')
        elif declaration.scope != 'global' or root.class_code != CLASS_CODES['VAR_GLOBAL']:
            raise GXWFormatError('structure member outside observed global scope')
        try:
            root_index = int(root_index_text) if root_index_text is not None else None
        except ValueError as error:
            raise GXWFormatError('unbounded structure root array integer') from error
        if root_bounds:
            cache_known = (local_member or root.type_code == 9 and root.array_marker == 1
                           or root.type_code == root.array_marker == 0 and not root.type_reference)
            if (root.unknown_u32 != (0 if local_member else 2) or not cache_known or len(root_bounds) != 1
                    or not -2**31 <= root_bounds[0][0] <= root_bounds[0][1] < 2**31
                    or root_index is not None and not root_bounds[0][0] <= root_index <= root_bounds[0][1]
                    or member_name is not None and root_index is None):
                raise GXWFormatError('constant root index outside observed current structure array bounds')
        elif root.unknown_u32 != (0 if local_member else 1) or root.array_marker or root_index is not None:
            raise GXWFormatError('structure member outside observed scalar scope')
        document = self.structure(root_type)
        if member_name is None:
            # A selected element retains its parent declaration and bounds,
            # but its value type is the structure, not the whole parent array.
            return {'source_table': table, 'root_offset': root.offset,
                    'source_stream': document.logical_name, 'declared_type': root.data_type,
                    'data_type': root.data_type if root_bounds and root_index is None else root_type,
                    'bounds': tuple(root_bounds) if root_index is None else (), 'constant_index': None,
                    **({'root_bounds': tuple(root_bounds), 'root_constant_index': root_index} if root_bounds else {})}
        members = [member for member in document.members if member.name.casefold() == member_name.casefold()]
        if len(members) != 1:
            raise GXWFormatError('missing or ambiguous current structure member: ' + symbol)
        member = members[0]
        try:
            base_type, bounds = _type_parts(member.data_type)
            index = int(index_text) if index_text is not None else None
        except ValueError as error:
            raise GXWFormatError('unbounded structure member array integer') from error
        zero_cache = member.type_code == member.array_marker == 0 and member.type_reference == ''
        cached = (base_type in BASIC_TYPES and member.type_code == BASIC_TYPES[base_type][0]
                  and member.array_marker == int(bool(bounds)) and member.type_reference == '')
        if base_type not in BASIC_TYPES or not (zero_cache or cached):
            raise GXWFormatError('structure member type cache outside observed source binding')
        if bounds:
            if (len(bounds) != 1 or base_type not in ('BOOL', 'WORD') or index is None
                    or not -2**31 <= bounds[0][0] <= index <= bounds[0][1] < 2**31):
                raise GXWFormatError('constant member index outside observed current array bounds')
        elif index is not None:
            raise GXWFormatError('scalar structure member cannot have an array index')
        return {'source_table': table, 'root_offset': root.offset,
                'source_stream': document.logical_name, 'member_offset': member.offset,
                'member': member.name, 'declared_type': member.data_type, 'data_type': base_type,
                'bounds': tuple(bounds), 'constant_index': index,
                **({'root_bounds': tuple(root_bounds), 'root_constant_index': root_index} if root_bounds else {})}

    def structure_member(self, symbol):
        value = self.structure_value(symbol)
        return value if value is not None and 'member' in value else None

    def _formal_type(self, definition, text):
        try:
            return type_mask(text)
        except GXWFormatError as error:
            if definition.get('source_kind') != 'project_labels':
                raise
            try:
                base_type, bounds = _type_parts(text)
                if bounds and (len(bounds) != 1 or not -2**31 <= bounds[0][0] <= bounds[0][1] < 2**31):
                    raise GXWFormatError('compound formal array bounds outside observed rank-one source')
                self.structure(base_type, library=definition.get('library'))
            except (GXWFormatError, ValueError):
                raise error from None
            # The source supplies type identity, without a primitive/ANY mask.
            return None

    def _interface(self, definition, input_count=None):
        return interface(definition, input_count, resolve_type=lambda text: self._formal_type(definition, text),
                         graphical=True)

    def fixed_interface(self, name, kind='FUNCTION_BLOCK'):
        stream, definition = self.definition(name, kind)
        count = None
        suffix = re.fullmatch(re.escape(definition['name']) + r'-([1-9][0-9]*)', name, re.I)
        if suffix:
            # The suffix counts repeated data inputs, not EN or a fixed MUX
            # selector. Ask the shared interface for one repeat first.
            rows = (definition['declarations'].rows if definition.get('source_kind') == 'project_labels'
                    else parse_library_declarations(definition)['rows'])
            classes = [row.class_code if hasattr(row, 'class_code') else CLASS[row['source_class']] for row in rows]
            count = sum(code in (3, 11, 5) for code in classes) + int(suffix[1])
            doc = definition.get('declarations')
            if doc is not None and doc.owner_pou_status is not None and doc.owner_pou_status & 4:
                count += 1
        inputs, outputs = self._interface(definition, count)
        if max(len(inputs),len(outputs)) > 256:
            raise GXWFormatError('source interface exceeds the bounded graphic ports')
        return {'source_stream': stream, 'definition_offset': definition['offset'],
                'inputs': inputs, 'outputs': outputs}

    def st_interface(self, name, kind='FUNCTION_BLOCK'):
        """The same source formals without graphical port/mask restrictions."""
        stream, definition = self.definition(name, kind)
        inputs, outputs = interface(definition, resolve_type=lambda text: None)
        return {'source_stream': stream, 'definition_offset': definition['offset'],
                'inputs': inputs, 'outputs': outputs, 'return_type': definition.get('return_type_raw')}

    def callable(self, node, *, bind_instance=True):
        ins, outs = sorted_ports(node)
        name = node.type_name if node.kind == NodeKind.FUNCTION_BLOCK else node.symbol
        if node.kind == NodeKind.FUNCTION and name.startswith('@'):
            # SC_EDIT's independent operator descriptor table gives three
            # fixed binary forms; the other native operators serialize their
            # repeated input count in the suffix.
            fixed = {'@SUB':2,'@DIV':2,'@NE':2}
            repeated = re.fullmatch(r'@(AND|OR|XOR|ADD|MUL|GT|GE|EQ|LE|LT)-([1-9][0-9]*)',name,re.I)
            expected = fixed.get(name.upper(),int(repeated[2]) if repeated else None)
            if expected is not None and (len(ins)!=expected or len(outs)!=1):
                raise GXWFormatError('intrinsic operator interface differs from its serialized arity')
            # Serialized intrinsic operators have anonymous native formals,
            # rather than a .lif definition. Preserve their numeric connector
            # identity and encoded direction; do not invent library names or
            # assign a concrete overloaded data type from a connected device.
            return {'source_stream': None, 'definition_offset': None,
                'binding': 'serialized_operator', 'ports': [dict(name=str(index),
                    formal_name=None, side='in' if index in ins else 'out', data_type=None,
                    class_code=None, occurrence=1, x=port.local_x, y=port.local_y,
                    negated=bool(port.port_kind_code & 8)) for index, port in enumerate(node.ports)]}
        if bind_instance and node.kind == NodeKind.FUNCTION_BLOCK:
            binding = self.label(node.symbol)
            row = binding[1] if binding else None
            # Native type changes retain the prior reference. Newly created
            # declarations can retain code 0 and an empty reference through
            # cold compilation. Bind their declared type, retaining both fields.
            if not row or row.type_code not in (0, 15) or row.data_type.casefold()!=name.casefold():
                raise GXWFormatError('FB instance requires a matching source label type: ' + node.symbol)
        expected_kind = 'FUNCTION_BLOCK' if node.kind == NodeKind.FUNCTION_BLOCK else 'FUNCTION'
        stream, definition = self.definition(name, expected_kind)
        inputs, outputs = self._interface(definition, len(ins))
        if len(outputs) != len(outs):
            raise GXWFormatError('source formal interface differs from graph output count')
        counts = Counter(row['name'] for row in inputs + outputs)
        ports = [None] * len(node.ports)
        for side, indexes, formals in [('in', ins, inputs), ('out', outs, outputs)]:
            occurrences = Counter()
            repeats = Counter(row['name'] for row in formals)
            for index, formal in zip(indexes, formals):
                if node.ports[index].port_kind_code & 4 and formal['class_code'] != 11:
                    raise GXWFormatError('constant-only port differs from its current source formal')
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

    def source_prototype(self, node, *, bind_instance=True):
        """Bind a cloneable complete source box to its current interface.

        Graphical EN/ENO options belong to the source interface; they do not
        need a special numeric-port cloning exception.  Validate the complete
        backing record before authorizing a clone so an opaque cache tail or
        another record class can never acquire construction permission.
        """
        from .structured_pou import _parse_node
        if (len(node.raw) < 12 or int.from_bytes(node.raw[:4], 'little') != len(node.raw)
                or node.raw[4:8] != b'\x01\0\0\0'):
            raise GXWFormatError('callable prototype requires a complete node record')
        _parse_node(node.raw, node.offset)
        binding = self.callable(node, bind_instance=bind_instance)
        return {'binding':'resolved', **binding}

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
