"""CPU-selected callable interfaces bound to the existing graph and labels.

Source formals describe graphic connections; execution and IN_OUT copyback are
not inferred here. Failed or ambiguous bindings remain explicit read issues.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
import re

from .container_writer import validate_cfb_streams
from .declarations import (BASIC_TYPES, CLASS_CODES, _type_parts, local_table_for,
                           parse_declarations, parse_structure_declarations, resolve_label)
from .library_sources import decode_library_archive, parse_library_source, parse_library_declarations
from .models import GXWFormatError, NodeKind
from .project_metadata import (current_rows, logical_mapping, read_project_text_context,
                               read_project_compile_options, project_source_groups)
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


def interface(definition, input_count=None, *, resolve_type=None):
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
        r['type_code'] = (resolve_type or type_mask)(r['declared_type'])
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
        mapping = logical_mapping(outer['projectdatalist.xml'])
        payloads = validate_cfb_streams(outer['_hdb'])
        streams = {name: payloads[key] for name, key in mapping.items() if key in payloads}
        contexts = [read_project_text_context(value) for name, value in streams.items() if name.endswith('.prj')]
        if len(contexts) != 1:
            raise GXWFormatError('one project CPU context is required for source library selection')
        if declarations is None:
            declarations = {name: parse_declarations(value, logical_name=name)
                            for name, value in streams.items() if name.endswith(('.lh', '.gh', '.lnl'))}
        else:
            # Supplied draft declarations override the matching current bytes.
            # Library formals still belong to this project snapshot.
            declarations = {**{name: parse_declarations(value, logical_name=name)
                for name, value in streams.items() if name.endswith('.lnl')}, **declarations}
        libraries, issues = {}, []
        source_groups, source_issues = project_source_groups(outer['projectdatalist.xml'])
        issues.extend(source_issues)
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
        local = self.local_table
        if self.source_groups is not None and local is None:
            return None
        return resolve_label(self.declarations, self.program_name, symbol,
                             global_variable_hiding=self.global_variable_hiding, local_table=local)

    @property
    def local_table(self):
        return local_table_for(self.declarations, self.program_name, self.source_groups)

    def declaration_kind(self, row):
        if row.type_code == 15:
            return 'function_block'
        if row.type_code == 0:
            matches = self.catalog.get(row.data_type.casefold(), [])
            if len(matches) == 1 and matches[0][1]['kind'] == 'FUNCTION_BLOCK':
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
        matches = self.catalog.get(name.casefold(), [])
        if len(matches) != 1:
            raise GXWFormatError('missing or ambiguous CPU-selected source callable: ' + name)
        stream, definition = matches[0]
        if definition['kind'] != kind:
            raise GXWFormatError('graph kind differs from source definition')
        return stream, definition

    def structure(self, name, *, library=None):
        """Resolve one current type owner; a name alone is not a source."""
        key = name.casefold()
        if library is not None:
            candidates = self.library_structures.get((library.casefold(), key), ())
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
        return interface(definition, input_count, resolve_type=lambda text: self._formal_type(definition, text))

    def fixed_interface(self, name, kind='FUNCTION_BLOCK'):
        stream, definition = self.definition(name, kind)
        inputs, outputs = self._interface(definition)
        if not 1 <= max(len(inputs), len(outputs)) <= 256:
            raise GXWFormatError('source interface has no bounded observed graphic ports')
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
        if bind_instance and node.kind == NodeKind.FUNCTION_BLOCK:
            binding = self.label(node.symbol)
            row = binding[1] if binding else None
            # Native type changes retain the prior reference. Newly created
            # declarations can retain code 0 and an empty reference through
            # cold compilation. Bind their declared type, retaining both fields.
            if not row or row.type_code not in (0, 15) or row.data_type != name:
                raise GXWFormatError('FB instance requires a matching source label type: ' + node.symbol)
        matches = self.catalog.get(name.casefold(), [])
        if not matches and node.kind == NodeKind.FUNCTION:
            base = re.sub(r'-[1-9][0-9]*$', '', name)
            candidates = self.catalog.get(base.casefold(), [])
            if len(candidates) == 1:
                definition = candidates[0][1]
                if definition.get('source_kind') == 'project_labels':
                    extensible = any(row.class_code == 12 for row in definition['declarations'].rows)
                else:
                    extensible = any(row['source_class'] == 'VAR_IN_EXT'
                        for row in parse_library_declarations(definition)['rows'])
                if extensible:
                    matches = candidates
        if len(matches) != 1:
            raise GXWFormatError('missing or ambiguous CPU-selected source callable: ' + name)
        stream, definition = matches[0]
        expected_kind = 'FUNCTION_BLOCK' if node.kind == NodeKind.FUNCTION_BLOCK else 'FUNCTION'
        if definition['kind'] != expected_kind:
            raise GXWFormatError('graph kind differs from source definition')
        inputs, outputs = self._interface(definition, len(ins))
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
