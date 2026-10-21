"""Bounded offline edits of existing converted SFC source records.

The graph stream is an ordered source representation, not ordinary ladder
PCode. This module decodes its stored references and preserves the original
opaque graph cache. It does not compile SFC or claim PLC execution semantics.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import struct

from .models import GXWFormatError
from .sfc_pou import (NATIVE_SFC_GRAPH_FX_CPUS, NATIVE_SFC_GRAPH_QL_CPUS,
                      SfcSource, parse_sfc_pou, sfc_step_action_reference,
                      sfc_components, sfc_child_body, serialize_sfc_components)
from .token_pou import parse_token_fragment


_INITIAL = {'056c021705', '056c041c05'}
_STEP = '056c020f05'
_SIMPLE_TRANSITION = '056c020405'
_OUTGOING = '0562020005'
_DEPENDENCIES = {'0542020005', '0542010105', '064202001106', '064201011106'}
_JUMP_TRANSITIONS = {'056c021d05', '056c021f05', '056c022005'}
_COMBINED_PARALLEL = '056c020805'
_END = bytes.fromhex('056c010a05')


def _opcode(record):
    return record['raw_hex'][:int(record['raw_hex'][:2], 16) * 2]


def _records(source):
    records = deepcopy(list(source.graph_records))
    # Continued branch dependencies and combined convergence/divergence are
    # independently measured native forms. Decode only these exact opcodes
    # when the shared reader keeps them opaque; retain every unknown record.
    for row in records:
        opcode = _opcode(row)
        if row['kind'] == 'opaque' and (opcode in _DEPENDENCIES or opcode == _COMBINED_PARALLEL):
            raw = bytes.fromhex(row['raw_hex'])
            tokens = parse_token_fragment(raw, 0, len(raw)).tokens
            code = 0x99 if opcode == _COMBINED_PARALLEL else 0x98
            if len(tokens) == 2 and tokens[1].raw[1] == code and len(tokens[1].raw) in (4, 5):
                row.update(kind='transition-reference' if code == 0x99 else 'step-reference',
                           references=[int.from_bytes(tokens[1].raw[2:-1], 'little')])
    return records


def _require_cpu(cpu):
    if cpu not in NATIVE_SFC_GRAPH_FX_CPUS | NATIVE_SFC_GRAPH_QL_CPUS:
        raise GXWFormatError('offline SFC graph edits require an observed concrete CPU source form')


def _source(source):
    # Decode the actual bytes on every operation; cached record dictionaries
    # and positions must never define a different source than the stored body.
    current = parse_sfc_pou(source.raw)
    if current.graph_tokens is None or current.layout['graph']['storage_words'] != [0, 0]:
        raise GXWFormatError('offline SFC graph edits require converted source records')
    return current


def decode_sfc_graph(source: SfcSource, *, cpu: str) -> dict:
    """Project ordered steps, transitions and explicit outgoing references.

    Raw dependency opcodes remain distinct. Positions are produced only for
    the measured one-column linear source form; other graphs retain their
    logical references without inventing native grid coordinates.
    """
    _require_cpu(cpu)
    source = _source(source)
    records = _records(source)
    steps, transitions, issues = [], [], []
    current_step, current_transition = None, None
    for row in records:
        kind = row['kind']
        if kind == 'step-action-reference':
            identity = sfc_step_action_reference(row, cpu=cpu)
            if identity is None:
                issues.append({'offset': row['offset'], 'reason': 'unresolved-step-action-reference'})
            steps.append({'source_offset': row['offset'], 'step_reference': row['references'][0],
                          'action_number': identity[1] if identity else None,
                          'initial': _opcode(row) in _INITIAL})
            current_step, current_transition = row['references'][0], None
        elif kind == 'transition-reference':
            current_transition = {'source_offset': row['offset'], 'number': row['references'][0],
                                  'source_step': current_step, 'opcode': _opcode(row),
                                  'outgoing': [], 'dependencies': []}
            transitions.append(current_transition)
            if current_step is None:
                issues.append({'offset': row['offset'], 'reason': 'transition-without-current-step'})
        elif kind == 'step-reference':
            if current_transition is None:
                issues.append({'offset': row['offset'], 'reason': 'reference-without-current-transition'})
                continue
            reference = {'source_offset': row['offset'], 'step_reference': row['references'][0],
                         'opcode': _opcode(row)}
            if _opcode(row) == _OUTGOING:
                current_transition['outgoing'].append(reference)
            elif _opcode(row) in _DEPENDENCIES:
                current_transition['dependencies'].append(reference)
        elif kind == 'end':
            current_step, current_transition = None, None
        else:
            issues.append({'offset': row['offset'], 'reason': 'uninterpreted-graph-record',
                           'kind': kind, 'raw_hex': row['raw_hex']})
    for values, label in (([r['step_reference'] for r in steps], 'step'),
                          ([r['number'] for r in transitions], 'transition')):
        if len(set(values)) != len(values):
            issues.append({'reason': 'ambiguous-' + label + '-identity'})
    linear = _linear_symbols(source, cpu=cpu)
    nodes = [dict(id='step:' + str(row['step_reference']), kind='initial_step' if row['initial'] else 'step',
                  number=row['step_reference'], action_number=row['action_number'], source_offset=row['source_offset'])
             for row in steps]
    edges = []
    known_steps = {row['step_reference'] for row in steps}
    for row in transitions:
        identity = 'transition:' + str(row['number'])
        nodes.append(dict(id=identity, kind='transition', number=row['number'], source_offset=row['source_offset'],
                          branch_opcode=row['opcode'], dependencies=deepcopy(row['dependencies'])))
        incoming = [row['source_step']]
        if row['opcode'] in ('056c020705', '056c020805', '056c022005'):
            incoming.extend(ref['step_reference'] for ref in row['dependencies'])
        for number in incoming:
            if number not in known_steps:
                issues.append({'reason': 'missing-incoming-step', 'number': number})
            edges.append(dict(source='step:' + str(number), target=identity, kind='condition'))
        for target in row['outgoing']:
            number = target['step_reference']
            if number == 999 and cpu in NATIVE_SFC_GRAPH_QL_CPUS:
                target_id = 'terminal:999'
                if not any(node['id'] == target_id for node in nodes):
                    nodes.append(dict(id=target_id, kind='end'))
            else:
                target_id = 'step:' + str(number)
                if number not in known_steps:
                    issues.append({'reason': 'missing-outgoing-step', 'number': number})
            edges.append(dict(source=identity, target=target_id,
                              kind='jump' if row['opcode'] in _JUMP_TRANSITIONS else 'successor',
                              source_offset=target['source_offset']))
    return {'status': 'source-decoded', 'cpu': cpu, 'records': records,
            'steps': steps, 'transitions': transitions, 'issues': issues,
            'nodes': nodes, 'edges': edges,
            'position_resolution': 'core-linear-source-order' if linear else 'not-established',
            'linear_symbols': linear,
            'scope': 'stored SFC graph references; no execution or native editor display claim'}


def _linear_symbols(source, *, cpu):
    if cpu not in NATIVE_SFC_GRAPH_QL_CPUS:
        return None
    records = source.graph_records
    if not records or len(records) % 4:
        return None
    groups = [records[i:i + 4] for i in range(0, len(records), 4)]
    symbols, steps, transitions = [], [], []
    for index, group in enumerate(groups):
        step, transition, successor, end = group
        if ([row['kind'] for row in group]
                != ['step-action-reference', 'transition-reference', 'step-reference', 'end']
                or _opcode(step) != ('056c021705' if index == 0 else _STEP)
                or len(step['references']) != 1 or _opcode(transition) != _SIMPLE_TRANSITION
                or _opcode(successor) != _OUTGOING):
            return None
        if index + 1 < len(groups):
            following = groups[index + 1][0]
            if following['kind'] != 'step-action-reference' or len(following.get('references', [])) != 1:
                return None
            expected = following['references'][0]
        else:
            expected = 999
        if successor['references'] != [expected]:
            return None
        number, condition = step['references'][0], transition['references'][0]
        steps.append(number); transitions.append(condition)
        symbols.extend(({'kind': 'initial_step' if index == 0 else 'step', 'x': 0, 'y': index * 3, 'number': number},
                        {'kind': 'transition', 'x': 0, 'y': index * 3 + 1, 'number': condition},
                        {'kind': 'line', 'x': 0, 'start': index * 3 + 2, 'end': index * 3 + 2}))
    if len(set(steps)) != len(steps) or len(set(transitions)) != len(transitions):
        return None
    symbols.append({'kind': 'end', 'x': 0, 'y': len(groups) * 3})
    return symbols


def _number(value, name):
    if type(value) is not int or not 0 <= value <= 65535:
        raise GXWFormatError('SFC ' + name + ' must be an integer in 0..65535')
    return value


def _reference(number, code=0x98):
    _number(number, 'reference')
    width = 1 if number <= 255 else 2
    size = width + 3
    return bytes((size, code)) + number.to_bytes(width, 'little') + bytes((size,))


def _replace_graph(source, tokens):
    parse_token_fragment(tokens, 0, len(tokens))
    graph = source.layout['graph']
    start, size = graph['offset'], graph['size']
    cache = source.raw[graph['cache_offset']:graph['cache_offset'] + graph['cache_size']]
    body = bytearray(source.raw[start:start + 12] + struct.pack('<I', len(tokens) + 4) + tokens + cache)
    struct.pack_into('<I', body, 0, len(body))
    rebuilt = (source.raw[:start - 4] + struct.pack('<I', len(body)) + body
               + source.raw[start + size:])
    checked = parse_sfc_pou(rebuilt)
    current = checked.layout['graph']
    if (checked.raw[current['cache_offset']:current['cache_offset'] + current['cache_size']] != cache
            or checked.raw[current['offset'] + current['size']:] != source.raw[start + size:]):
        raise GXWFormatError('offline SFC write changed retained cache or child ownership')
    return rebuilt


def _record_replacements(source, changes):
    graph = source.layout['graph']
    pieces, cursor = [], graph['code_offset']
    for row in source.graph_records:
        offset, raw = row['offset'], bytes.fromhex(row['raw_hex'])
        if offset not in changes:
            continue
        refs = changes[offset]
        original = parse_token_fragment(raw, 0, len(raw)).tokens
        if len(original) != len(refs) + 1:
            raise GXWFormatError('SFC replacement differs from its current source record')
        replacement = original[0].raw + b''.join(
            token.raw if int.from_bytes(token.raw[2:-1], 'little') == number
            else _reference(number, token.raw[1]) for token, number in zip(original[1:], refs))
        pieces.extend((source.raw[cursor:offset], replacement))
        cursor = offset + len(raw)
    pieces.append(source.raw[cursor:graph['code_offset'] + graph['code_size']])
    return _replace_graph(source, b''.join(pieces))


def _sync_graph_objects(before, raw, *, cpu):
    """Maintain only internal registrations owned by the changed graph refs."""
    after = parse_sfc_pou(raw)
    old = decode_sfc_graph(before, cpu=cpu)
    new = decode_sfc_graph(after, cpu=cpu)
    children, actions = sfc_components(after)
    wanted_actions = {row['action_number'] for row in new['steps']}
    removed_actions = {row['action_number'] for row in old['steps']} - wanted_actions
    removed_zooms = {registration['zoom'] for row in actions if row['number'] in removed_actions
                     for registration in row['registrations']}
    actions = [row for row in actions if row['number'] not in removed_actions]
    retained_zooms = {registration['zoom'] for row in actions for registration in row['registrations']}
    wanted_conditions = {row['number'] for row in new['transitions']}
    removed_conditions = {row['number'] for row in old['transitions']} - wanted_conditions
    children = [row for row in children if not (
        row['kind'] == 'transition' and row['number'] in removed_conditions or
        row['kind'] == 'zoom' and row['name'] in removed_zooms - retained_zooms)]

    def name(base):
        used = {row['name'] for row in children + actions}
        candidate, suffix = base, 1
        while candidate in used:
            candidate = base + '_' + str(suffix); suffix += 1
        return candidate

    for number in sorted(wanted_actions - {row['number'] for row in actions}):
        zoom = name('sfc_zoom_' + str(number))
        children.append(dict(kind='zoom', name=zoom, number=None, body=sfc_child_body(b'')))
        actions.append(dict(name=name('sfc_action_' + str(number)), number=number,
                            registrations=[{'qualifier': 0, 'zoom': zoom}]))
    for number in sorted(wanted_conditions - {row['number'] for row in children if row['kind'] == 'transition'}):
        children.append(dict(kind='transition', name=name('sfc_condition_' + str(number)), number=number,
                             body=sfc_child_body(b'')))
    return serialize_sfc_components(after, children=children, actions=actions)


def _child_tokens(context, row, edit, *, allow_draft=False):
    """Use the shared CPU codec and keep every unmodified record's bytes."""
    from .token_encoder import encode_instruction
    from .token_listing import TokenInstruction, decode_token_program
    from .token_profiles import source_token_profile

    supplied = {'records_hex', 'instructions', 'instruction_edits'} & edit.keys()
    if len(supplied) > 1:
        raise GXWFormatError('SFC child token representations are mutually exclusive')
    original = row['body'][20:]
    if not supplied:
        return original
    if 'records_hex' in supplied:
        value = edit['records_hex']
        if not isinstance(value, str) or len(value) > 16 * 1024 * 1024:
            raise GXWFormatError('SFC child records_hex exceeds the source bound')
        try:
            result = bytes.fromhex(value)
        except ValueError as error:
            raise GXWFormatError('SFC child records_hex is not hexadecimal bytes') from error
        parse_token_fragment(result, 0, len(result))
        return result
    profile = source_token_profile(context.project['cpu'], language='sfc')
    if profile is None:
        raise GXWFormatError('SFC child CPU has no lexical instruction profile')
    encoding = context.project['text_encoding']
    listing = decode_token_program(parse_token_fragment(original, 0, len(original)),
                                   profile=profile, text_encoding=encoding)
    from .offline_project import read_offline_project
    storage_context = read_offline_project(context.raw).token_storage_context() if context.raw else None

    def encode(rows, source=None):
        if not isinstance(rows, list) or len(rows) > 65536:
            raise GXWFormatError('SFC child instructions require a bounded list')
        chunks = []
        from .token_encoder import encode_text_record
        from .token_listing import TokenText, TokenLabel
        from difflib import SequenceMatcher
        def identity(record):
            if isinstance(record, TokenText):
                return ('text', record.role, record.text)
            if isinstance(record, TokenLabel):
                return ('op', record.text, ())
            if isinstance(record, TokenInstruction):
                return ('op', record.mnemonic, tuple(record.args))
            return ('opaque', b''.join(token.raw for token in record.tokens))
        def wanted(value):
            if isinstance(value, dict) and set(value) == {'kind', 'text'}:
                return ('text', value['kind'], value['text'])
            if isinstance(value, dict) and set(value) == {'op', 'args'} and isinstance(value['args'], list):
                return ('op', value['op'], tuple(arg if isinstance(arg, str) else arg.get('text')
                    if isinstance(arg, dict) else None for arg in value['args']))
            return ('invalid',)
        retained = {}
        matcher = SequenceMatcher(a=[identity(record) for record in listing.records],
                                  b=[wanted(value) for value in rows], autojunk=False)
        for block in matcher.get_matching_blocks():
            for offset in range(block.size):
                retained[block.b + offset] = listing.records[block.a + offset]
        for index, value in enumerate(rows):
            old = retained.get(index)
            if isinstance(value, dict) and set(value) == {'kind', 'text'}:
                chunks.append(encode_text_record(value['kind'], value['text'], text_encoding=encoding,
                    profile=profile,
                    source_record=old if isinstance(old, TokenText) else None))
                continue
            if not isinstance(value, dict) or set(value) != {'op', 'args'}:
                raise GXWFormatError('SFC child instructions require op and args')
            chunks.append(encode_instruction(value['op'], value['args'], profile=profile,
                          text_encoding=encoding, plc_model=context.project['cpu'],
                          storage_context=storage_context,
                          allow_draft=allow_draft,
                          source_instruction=old if isinstance(old, (TokenInstruction, TokenLabel)) else
                              source if len(rows) == 1 else None))
        return b''.join(chunks)

    if 'instructions' in supplied:
        return encode(edit['instructions'])
    from .token_patch import edit_token_records
    return edit_token_records(listing, edit['instruction_edits'], text_encoding=encoding,
        plc_model=context.project['cpu'], storage_context=storage_context,
        allow_end_boundary=True, allow_draft=allow_draft).body


def edit_sfc_children(context, edits, *, allow_draft=False) -> tuple[bytes, dict]:
    """Edit existing POU internal objects; never create an engineering POU."""
    source = parse_sfc_pou(context.source.raw)
    children, actions = sfc_components(source)
    if not isinstance(edits, list) or not edits or len(edits) > 8192:
        raise GXWFormatError('SFC child edits require a bounded nonempty list')
    seen, number_changes = set(), {'actions': [], 'transitions': []}
    for edit in edits:
        allowed = {'kind', 'name', 'operation', 'new_name', 'number', 'new_number', 'registrations', 'target_name',
                   'records_hex', 'instructions', 'instruction_edits'}
        if (not isinstance(edit, dict) or set(edit) - allowed or edit.get('kind') not in ('zoom', 'transition', 'action')
                or not isinstance(edit.get('name'), str)):
            raise GXWFormatError('invalid SFC internal object edit')
        kind, name = edit['kind'], edit['name']
        if (kind, name) in seen:
            raise GXWFormatError('SFC child edits repeat the same source object')
        seen.add((kind, name))
        operation = edit.get('operation', 'edit')
        if ('number' in edit and operation != 'create' or operation == 'rename' and 'new_name' not in edit
                or operation == 'renumber' and 'new_number' not in edit):
            raise GXWFormatError('SFC internal number/name change fields do not match the operation')
        rows = actions if kind == 'action' else children
        selected = [row for row in rows if row['name'] == name and (kind == 'action' or row['kind'] == kind)]
        if operation == 'create':
            if selected:
                raise GXWFormatError('SFC internal create identity already exists')
            if kind == 'action':
                row = dict(name=name, number=_number(edit.get('number'), 'number'), registrations=[])
            else:
                row = dict(kind=kind, name=name, number=_number(edit.get('number'), 'number') if kind == 'transition' else None,
                           body=sfc_child_body(b''))
            rows.append(row)
        elif operation in ('edit', 'replace', 'delete', 'rename', 'renumber', 'move_before'):
            if len(selected) != 1:
                raise GXWFormatError('missing or ambiguous SFC internal source object')
            row = selected[0]
            if operation == 'move_before':
                targets = [other for other in rows if other is not row
                    and (kind == 'action' or other['kind'] == kind)
                    and other.get('source_name', other['name']) == edit.get('target_name')]
                if len(targets) != 1:
                    raise GXWFormatError('SFC child move requires a distinct bound target of the same kind')
                rows.remove(row)
                rows.insert(rows.index(targets[0]), row)
            elif 'target_name' in edit:
                raise GXWFormatError('SFC target_name is only used for move_before')
            if operation == 'delete':
                if set(edit) - {'kind', 'name', 'operation'}:
                    raise GXWFormatError('SFC delete cannot also change object fields')
                rows.remove(row); continue
        else:
            raise GXWFormatError('unknown SFC internal object operation')
        if kind == 'action':
            if {'records_hex', 'instructions', 'instruction_edits'} & edit.keys():
                raise GXWFormatError('SFC actions contain registrations; edit their zoom body for instructions')
            if 'registrations' in edit:
                row['registrations'] = deepcopy(edit['registrations'])
        else:
            if 'registrations' in edit:
                raise GXWFormatError('SFC child bodies do not own action registrations')
            row['body'] = sfc_child_body(_child_tokens(context, row, edit, allow_draft=allow_draft), original=row['body'])
        if 'new_name' in edit:
            old_name = row['name']; row['name'] = edit['new_name']
            if kind == 'zoom':
                for action in actions:
                    for registration in action['registrations']:
                        if registration['zoom'] == old_name:
                            registration['zoom'] = row['name']
        if 'new_number' in edit:
            if kind == 'zoom':
                raise GXWFormatError('SFC zoom source objects have no independent number')
            before = row['number']
            row['number'] = _number(edit['new_number'], 'new_number')
            number_changes['actions' if kind == 'action' else 'transitions'].append({'before': before, 'after': row['number']})
    graph = None
    if any(number_changes.values()):
        step_changes = []
        action_changes = {row['before']: row['after'] for row in number_changes['actions']}
        for row in source.graph_records:
            if row['kind'] == 'step-action-reference' and len(row['references']) == 1 and row['references'][0] in action_changes:
                number = row['references'][0]
                step_changes.append({'before': number, 'after': action_changes[number]})
        renumbered = parse_sfc_pou(_renumber_objects(source, dict(kind='renumber', steps=step_changes, **number_changes),
                                                    cpu=context.project['cpu']))
        region = renumbered.layout['graph']
        graph = renumbered.raw[region['offset']:region['offset'] + region['size']]
    result = serialize_sfc_components(source, children=children, actions=actions, graph=graph)
    return result, {'operation': 'offline_sfc_child_edit', 'native_calls': 0,
                    'changed': result != source.raw, 'edits': deepcopy(edits)}


def _renumber(source, step_numbers, transition_numbers=None):
    records = _records(source)
    if any(row['kind'] in ('opaque', 'block-call-reference') for row in records):
        raise GXWFormatError('offline SFC renumbering cannot retain unresolved graph references')
    changes = {}
    for row in records:
        refs = list(row.get('references', []))
        if row['kind'] in ('step-action-reference', 'step-reference') and refs:
            if row['kind'] == 'step-action-reference' and len(refs) != 1:
                raise GXWFormatError('Q/L renumbering requires the measured single-number step form')
            refs[0] = step_numbers.get(refs[0], refs[0])
        elif row['kind'] == 'transition-reference' and refs:
            refs[0] = (transition_numbers or {}).get(refs[0], refs[0])
        if refs != row.get('references', []):
            changes[row['offset']] = refs
    return _record_replacements(source, changes)


def _renumber_objects(source, edit, *, cpu):
    fields = {'kind', 'steps', 'transitions', 'actions'}
    if set(edit) - fields or not set(edit) & (fields - {'kind'}):
        raise GXWFormatError('SFC renumber requires steps, transitions or actions maps')

    def mapping(key, available):
        values = edit.get(key, [])
        if not isinstance(values, list) or len(values) > 8192:
            raise GXWFormatError('SFC renumber mappings require a bounded list')
        result = {}
        for row in values:
            if not isinstance(row, dict) or set(row) != {'before', 'after'}:
                raise GXWFormatError('SFC renumber mappings require before and after')
            before, after = (_number(row[name], name) for name in ('before', 'after'))
            if before not in available or before in result:
                raise GXWFormatError('SFC renumber source is stale, repeated or missing')
            result[before] = after
        final = [result.get(number, number) for number in available]
        if len(set(final)) != len(final):
            raise GXWFormatError('SFC renumber mapping creates an identity conflict')
        return result

    model = decode_sfc_graph(source, cpu=cpu)
    if model['issues']:
        raise GXWFormatError('SFC renumber cannot update unresolved graph references')
    steps = mapping('steps', {row['step_reference'] for row in model['steps']})
    transitions = mapping('transitions', {row['number'] for row in source.layout['children'] if row['kind'] == 'transition'})
    actions = mapping('actions', {row['number'] for row in source.layout['actions']})
    for row in _records(source):
        if row['kind'] == 'step-action-reference' and len(row['references']) == 1:
            number = row['references'][0]
            if number in steps and number not in actions:
                actions[number] = steps[number]
            if number in actions and number not in steps:
                steps[number] = actions[number]
            if steps.get(number, number) != actions.get(number, number):
                raise GXWFormatError('SFC same-number source form requires joint step/action renumbering')
    if cpu in NATIVE_SFC_GRAPH_QL_CPUS and 999 in steps.values():
        raise GXWFormatError('SFC step number 999 is reserved for the Q/L terminal')
    changes = {}
    for row in _records(source):
        refs = list(row.get('references', []))
        if row['kind'] == 'step-action-reference':
            refs[0] = steps.get(refs[0], refs[0])
            if len(refs) == 2:
                refs[1] = actions.get(refs[1], refs[1])
        elif row['kind'] == 'step-reference':
            refs[0] = steps.get(refs[0], refs[0])
        elif row['kind'] == 'transition-reference':
            refs[0] = transitions.get(refs[0], refs[0])
        if refs != row.get('references', []):
            changes[row['offset']] = refs
    result = parse_sfc_pou(_record_replacements(source, changes))
    children, registrations = sfc_components(result)
    for row in children:
        if row['kind'] == 'transition':
            row['number'] = transitions.get(row['number'], row['number'])
    for row in registrations:
        row['number'] = actions.get(row['number'], row['number'])
    return serialize_sfc_components(result, children=children, actions=registrations)


def _shape(symbol):
    return tuple(sorted((key, value) for key, value in symbol.items() if key != 'number'))


def _validate_rebuild_symbols(source, symbols, *, cpu):
    _require_cpu(cpu)
    if not isinstance(symbols, list) or not symbols or len(symbols) > 8192:
        raise GXWFormatError('SFC rebuilding requires a bounded complete symbol list')
    fields = {'initial_step': {'x', 'y', 'number'}, 'step': {'x', 'y', 'number'},
              'transition': {'x', 'y', 'number'}, 'end': {'x', 'y'}, 'jump': {'x', 'y', 'number'},
              'line': {'x', 'start', 'end'}, **{
                  kind: {'start', 'end', 'y'} for kind in
                  ('parallel_branch', 'parallel_coupling', 'selective_branch', 'selective_coupling')}}
    seen, steps, transitions = set(), [], []
    for symbol in symbols:
        required = fields.get(symbol.get('kind')) if isinstance(symbol, dict) and isinstance(symbol.get('kind'), str) else None
        optional = {'action_number'} if isinstance(symbol, dict) and symbol.get('kind') in ('step', 'initial_step') else set()
        if (required is None or not required | {'kind'} <= set(symbol) or set(symbol) - required - {'kind'} - optional
                or any(type(symbol[key]) is not int or not 0 <= symbol[key] <= 32767 for key in required | (optional & symbol.keys()))
                or _shape(symbol) in seen):
            raise GXWFormatError('SFC rebuilding has unknown, repeated or invalid symbols')
        seen.add(_shape(symbol))
        if symbol['kind'] in ('step', 'initial_step', 'end'):
            if symbol['y'] % 3:
                raise GXWFormatError('SFC steps require the observed three-row alignment')
            if symbol['kind'] != 'end':
                steps.append(symbol['number'])
        elif symbol['kind'] == 'transition':
            transitions.append(symbol['number'])
        if 'start' in symbol and (symbol['start'] > symbol['end']
                or symbol['kind'] != 'line' and symbol['start'] == symbol['end']):
            raise GXWFormatError('SFC symbol span is reversed')
    if (cpu in NATIVE_SFC_GRAPH_QL_CPUS and 999 in steps
            or len(set(steps)) != len(steps)
            or len(set(transitions)) != len(transitions)
            or sum(row['kind'] == 'initial_step' for row in symbols) != 1
            or sum(row['kind'] in ('end', 'jump') for row in symbols) < 1
            or sum(row['kind'] == 'end' for row in symbols) > 1):
        raise GXWFormatError('SFC rebuilding requires unique step/transition identities, one initial step and a terminal')
    for row in symbols:
        if 'action_number' in row:
            _number(row['action_number'], 'action_number')
            if cpu in NATIVE_SFC_GRAPH_QL_CPUS and row['number'] != row['action_number']:
                raise GXWFormatError('Q/L source steps select their same-number action registration')
    if cpu in NATIVE_SFC_GRAPH_FX_CPUS and any(row['kind'] == 'end' for row in symbols):
        raise GXWFormatError('FX SFC rebuilding requires explicit jump symbols instead of a Q/L END')
    if any(row['kind'] == 'jump' and row['number'] not in steps for row in symbols):
        raise GXWFormatError('SFC rebuilding jumps must select existing graph steps')


def _symbol_graph(symbols):
    """Expand measured writer symbols into reciprocal geometric ports."""
    grid = {}

    def cell(x, y, kind, ports, number=None, action_number=None):
        if (x, y) in grid:
            raise GXWFormatError('SFC rebuild symbols overlap the same current cell')
        grid[x, y] = {'x': x, 'y': y, 'kind': kind, 'ports': set(ports), 'number': number,
                      'action_number': action_number}

    for row in symbols:
        kind = row['kind']
        if kind in ('initial_step', 'step', 'transition', 'end', 'jump'):
            cell(row['x'], row['y'], kind, ('up',) if kind in ('end', 'jump') else ('up', 'down'),
                 row.get('number'), row.get('action_number'))
        elif kind == 'line':
            for y in range(row['start'], row['end'] + 1):
                cell(row['x'], y, kind, ('up', 'down'))
        else:
            branch = kind.endswith('_branch')
            for x in range(row['start'], row['end'] + 1):
                ports = {'down' if branch else 'up'}
                if x == row['start']:
                    ports.add('up' if branch else 'down')
                if x > row['start']:
                    ports.add('left')
                if x < row['end']:
                    ports.add('right')
                cell(x, row['y'], kind, ports)
    directions = {'up': (0, -1, 'down'), 'down': (0, 1, 'up'),
                  'left': (-1, 0, 'right'), 'right': (1, 0, 'left')}
    for identity, row in grid.items():
        for port in row['ports']:
            dx, dy, reciprocal = directions[port]
            other = grid.get((identity[0] + dx, identity[1] + dy))
            if other is None or reciprocal not in other['ports']:
                if port == 'up' and row['kind'] == 'initial_step':
                    continue
                raise GXWFormatError('SFC rebuild has an unconnected ' + port + ' port at ' + str(identity))
    nodes = {key: row for key, row in grid.items() if row['kind'] not in (
        'line', 'parallel_branch', 'parallel_coupling', 'selective_branch', 'selective_coupling')}

    def nearby(identity, direction):
        row = grid[identity]
        dx, dy, reciprocal = directions[direction]
        first = (identity[0] + dx, identity[1] + dy)
        if direction not in row['ports'] or first not in grid:
            return {}
        queue, seen, found = [(first, frozenset())], set(), {}
        while queue:
            key, kinds = queue.pop()
            if key in seen:
                continue
            seen.add(key)
            current = grid[key]
            if key in nodes:
                found[key] = kinds
                continue
            kinds = kinds | {current['kind']}
            for port in (direction, 'left', 'right'):
                if port in current['ports']:
                    dx, dy, reciprocal = directions[port]
                    target = (key[0] + dx, key[1] + dy)
                    if target in grid and reciprocal in grid[target]['ports']:
                        queue.append((target, kinds))
        return found

    following = {key: nearby(key, 'down') for key in nodes}
    previous = {key: nearby(key, 'up') for key in nodes}
    initial, = [key for key, row in nodes.items() if row['kind'] == 'initial_step']
    queue, seen = [initial], set()
    while queue:
        key = queue.pop()
        if key not in seen:
            seen.add(key); queue.extend(following[key])
    if seen != nodes.keys():
        raise GXWFormatError('SFC rebuild contains disconnected source symbols')
    return nodes, following, previous


def encode_sfc_symbols(source: SfcSource, symbols, *, cpu: str) -> bytes:
    """Encode measured SFC reference records from a complete connected grid.

    Branch dependencies are emitted from the actual predecessor sets, in the
    native source's row/column order. No frozen graph bytes are templates.
    """
    source = _source(source)
    if decode_sfc_graph(source, cpu=cpu)['issues']:
        raise GXWFormatError('SFC rebuilding cannot discard unresolved source graph records')
    _validate_rebuild_symbols(source, symbols, cpu=cpu)
    nodes, following, previous = _symbol_graph(symbols)
    order = lambda key: (key[1], key[0])
    steps = sorted((key for key, row in nodes.items() if row['kind'] in ('step', 'initial_step')), key=order)
    step_order = {key: index for index, key in enumerate(steps)}
    old_actions = {row['references'][0]: row['references'][-1] for row in _records(source)
                   if row['kind'] == 'step-action-reference'}
    conditions = {}
    for key, row in nodes.items():
        if row['kind'] != 'transition':
            continue
        predecessors = previous[key]
        successors = following[key]
        if (not predecessors or not successors
                or any(nodes[item]['kind'] not in ('step', 'initial_step') for item in predecessors)
                or any(nodes[item]['kind'] not in ('step', 'end', 'jump') for item in successors)):
            raise GXWFormatError('SFC transitions must connect existing steps and step/end/jump targets')
        incoming_parallel = len(predecessors) > 1
        outgoing_parallel = len(successors) > 1
        owner = max(predecessors, key=lambda item: step_order[item])
        paths = set().union(*successors.values())
        if incoming_parallel and outgoing_parallel:
            opcode = 0x08
        elif incoming_parallel:
            opcode = 0x07
        elif outgoing_parallel:
            opcode = 0x06
        elif 'selective_coupling' in paths:
            opcode = 0x0b
        elif len(following[owner]) > 1:
            opcode = 0x05
        else:
            opcode = 0x04
        target_jump = any(nodes[item]['kind'] == 'jump' for item in successors)
        if target_jump:
            opcode = {0x04: 0x1d, 0x0b: 0x1f, 0x07: 0x20}.get(opcode, opcode)
        dependencies = []
        if incoming_parallel:
            dependencies = sorted((item for item in predecessors if item != owner), key=order, reverse=True)
        elif 'selective_coupling' in paths:
            target, = successors
            merged = previous[target]
            ordered = sorted(merged, key=lambda item: (step_order[max(previous[item], key=lambda step: step_order[step])], order(item)))
            if ordered[-1] == key:
                dependencies = [max(previous[item], key=lambda step: step_order[step]) for item in reversed(ordered[:-1])]
        conditions.setdefault(owner, []).append((key, opcode, dependencies, successors, incoming_parallel))
    result = []
    for key in steps:
        row = nodes[key]
        fx = cpu in NATIVE_SFC_GRAPH_FX_CPUS
        initial = row['kind'] == 'initial_step'
        result.extend((bytes.fromhex('056c041c05' if initial else '056c041405') if fx
                       else bytes.fromhex('056c021705' if initial else _STEP), _reference(row['number'])))
        if fx:
            action = row['action_number'] if row['action_number'] is not None else old_actions.get(row['number'], row['number'])
            result.append(_reference(action))
        for condition, opcode, dependencies, successors, parallel in sorted(conditions.get(key, []), key=lambda item: order(item[0])):
            result.extend((bytes((5, 0x6c, 2, opcode, 5)), _reference(nodes[condition]['number'], 0x99)))
            for index, dependency in enumerate(dependencies):
                result.append(bytes.fromhex(('0542020005' if parallel else '0542010105') if index == 0
                    else ('064202001106' if parallel else '064201011106')))
                result.append(_reference(nodes[dependency]['number']))
            for target in sorted(successors, key=order):
                row_target = nodes[target]
                result.extend((bytes.fromhex(_OUTGOING), _reference(999 if row_target['kind'] == 'end' else row_target['number'])))
        result.append(_END)
    return b''.join(result)


def _bound_graph(context, graph):
    current = context.native_graph(graph) if graph is not None else None
    if current is None:
        raise GXWFormatError('this SFC coordinate edit requires graph evidence bound to the current source')
    return current


def _jump_record(context, source, model, edit, graph):
    before = _number(edit.get('before_step_reference'), 'before_step_reference')
    offset = None
    if 'source_offset' in edit:
        offset = edit['source_offset']
        if type(offset) is not int or not 0 <= offset < len(source.raw):
            raise GXWFormatError('SFC source_offset must select a current source byte offset')
    if not {'x', 'y'} <= set(edit):
        # A source offset can replace the target of an already encoded jump.
        # It cannot silently turn an ordinary forward connection into a jump.
        selected = [row for transition in model['transitions'] if transition['opcode'] in _JUMP_TRANSITIONS
                    for row in transition['outgoing']
                    if row['step_reference'] == before and row['source_offset'] == offset]
    else:
        current = _bound_graph(context, graph)
        x, y = (_number(edit.get(key), key) for key in ('x', 'y'))
        selected_cell = [cell for cell in current['cells'] if (cell['x'], cell['y']) == (x, y)]
        if (len(selected_cell) != 1 or selected_cell[0]['result'] != 1
                or selected_cell[0]['type'] != 15 or selected_cell[0]['number'] != before):
            raise GXWFormatError('SFC jump cell does not match its current source-bound target')
        topology = current['topology']
        if topology['issues']:
            raise GXWFormatError('SFC jump has unresolved graph connections')
        by_id = {row['id']: row for row in current['cells']}
        adjacent = {key: set() for key in by_id}
        for segment in topology['segments']:
            adjacent[segment['source']].add(segment['target'])
            adjacent[segment['target']].add(segment['source'])
        queue, seen, conditions = [selected_cell[0]['id']], set(), set()
        while queue:
            identity = queue.pop()
            if identity in seen:
                continue
            seen.add(identity)
            cell = by_id[identity]
            if cell['result'] == 2:
                conditions.add(cell['number']); continue
            if identity != selected_cell[0]['id'] and cell['result'] == 1:
                continue
            queue.extend(adjacent[identity] - seen)
        if len(conditions) != 1:
            raise GXWFormatError('SFC jump does not bind to one current transition')
        condition = next(iter(conditions))
        selected = [row for transition in model['transitions'] if transition['number'] == condition
                    for row in transition['outgoing'] if row['step_reference'] == before]
        if offset is not None:
            selected = [row for row in selected if row['source_offset'] == offset]
    if len(selected) != 1:
        raise GXWFormatError('SFC jump source reference is missing or ambiguous')
    return selected[0]['source_offset']


def edit_sfc_source(context, graph_edits, *, graph=None) -> tuple[bytes, dict]:
    """Apply existing source-bound graph edits without invoking a native tool.

    Cached native grid evidence can bind an existing coordinate request. With
    no such evidence a jump can instead name its explicit ``source_offset``.
    Rebuilds derive measured branch reference records from the complete grid.
    """
    cpu = context.project['cpu']
    _require_cpu(cpu)
    source = _source(context.source)
    model = decode_sfc_graph(source, cpu=cpu)
    if not isinstance(graph_edits, list) or not graph_edits or len(graph_edits) > 8192:
        raise GXWFormatError('SFC graph edits require a bounded nonempty list')
    if any(not isinstance(edit, dict) for edit in graph_edits):
        raise GXWFormatError('SFC graph edits must be objects')
    special = [edit for edit in graph_edits if edit.get('kind') in ('swap_steps', 'rebuild', 'renumber')]
    if special:
        if len(graph_edits) != 1 or model['issues']:
            raise GXWFormatError('SFC rebuilding or swaps require one complete resolved edit')
        edit = special[0]
        if edit['kind'] == 'renumber':
            result = _renumber_objects(source, edit, cpu=cpu)
        elif edit['kind'] == 'swap_steps':
            if set(edit) != {'kind', 'left_step_reference', 'right_step_reference'}:
                raise GXWFormatError('SFC step swap fields are invalid')
            if cpu not in NATIVE_SFC_GRAPH_QL_CPUS:
                raise GXWFormatError('offline step swaps require the Q/L same-number action form')
            left = _number(edit['left_step_reference'], 'left_step_reference')
            right = _number(edit['right_step_reference'], 'right_step_reference')
            ordinary = {row['step_reference'] for row in model['steps'] if not row['initial'] and row['step_reference'] != 999}
            if left == right or left not in ordinary or right not in ordinary:
                raise GXWFormatError('SFC step swaps require two distinct existing ordinary steps')
            for number in (left, right):
                if len([row for row in source.layout['actions'] if row['number'] == number]) != 1:
                    raise GXWFormatError('SFC step swap has an unresolved same-number action')
            result = _renumber(source, {left: right, right: left})
        else:
            if set(edit) != {'kind', 'symbols'}:
                raise GXWFormatError('SFC rebuilding requires the complete symbol list')
            symbols = edit['symbols']
            _validate_rebuild_symbols(source, symbols, cpu=cpu)
            result = _replace_graph(source, encode_sfc_symbols(source, symbols, cpu=cpu))
            result = _sync_graph_objects(source, result, cpu=cpu)
    else:
        changes, selected = {}, set()
        by_offset = {row['offset']: row for row in source.graph_records}
        for edit in graph_edits:
            if edit.get('kind') == 'jump':
                required = {'kind', 'before_step_reference', 'target_step_reference'}
                if (not required <= set(edit) or set(edit) - required - {'source_offset', 'x', 'y'}
                        or 'source_offset' not in edit and not {'x', 'y'} <= set(edit)
                        or bool({'x', 'y'} & set(edit)) and not {'x', 'y'} <= set(edit)):
                    raise GXWFormatError('SFC jump fields require a current coordinate or source offset')
                target = _number(edit['target_step_reference'], 'target_step_reference')
                if len([row for row in model['steps'] if row['step_reference'] == target]) != 1:
                    raise GXWFormatError('SFC jump target must select one existing step')
                offset = _jump_record(context, source, model, edit, graph)
                refs = [target]
            else:
                if set(edit) != {'step_reference', 'action_number'}:
                    raise GXWFormatError('SFC action edits require step_reference and action_number')
                reference = _number(edit['step_reference'], 'step_reference')
                action = _number(edit['action_number'], 'action_number')
                rows = [row for row in source.graph_records if row['kind'] == 'step-action-reference'
                        and row['references'][0] == reference]
                if len(rows) != 1 or len(rows[0]['references']) != 2:
                    raise GXWFormatError('SFC action edit requires one existing explicit step/action pair')
                if len([row for row in source.layout['actions'] if row['number'] == action]) != 1:
                    raise GXWFormatError('SFC action edit requires one existing action registration')
                offset, refs = rows[0]['offset'], [reference, action]
            if offset in selected:
                raise GXWFormatError('SFC graph edits repeat the same current source record')
            selected.add(offset)
            if refs != by_offset[offset]['references']:
                changes[offset] = refs
        result = _record_replacements(source, changes)
    after = parse_sfc_pou(result)
    return result, {'operation': 'offline_sfc_graph_edit', 'changed': result != source.raw,
                    'native_calls': 0, 'cache': 'original-bytes-preserved',
                    'registrations_and_children': ('source-bindings-updated' if
                        sfc_components(after) != sfc_components(source) else 'original-bytes-preserved'),
                    'edits': deepcopy(graph_edits),
                    'graph': decode_sfc_graph(after, cpu=cpu),
                    'limitations': ['SFC execution and compiler output are separate evidence']}


def edit_sfc_program(context, *, graph_edits=None, child_edits=None, graph=None, save_policy='checked') -> tuple[bytes, dict]:
    """Apply graph, registration and child edits as one in-memory transaction."""
    current = replace(context, source=parse_sfc_pou(context.source.raw))
    reports = []
    if child_edits is not None and (not isinstance(child_edits, list) or not child_edits):
        raise GXWFormatError('SFC child edits require a nonempty list')
    if child_edits is not None:
        if any(not isinstance(row, dict) or not isinstance(row.get('kind'), str) or
               not isinstance(row.get('name'), str) for row in child_edits):
            raise GXWFormatError('SFC child edits require source kind and name identities')
        identities = [(row['kind'], row['name']) for row in child_edits]
        if len(set(identities)) != len(identities):
            raise GXWFormatError('SFC child edits repeat the same source object')
    registry_changed = any(row.get('operation') in ('create', 'delete', 'rename', 'renumber') or
                           {'new_name', 'new_number', 'registrations'} & row.keys() for row in child_edits or [])
    if registry_changed and current.source.graph_tokens is None:
        raise GXWFormatError('SFC internal registration changes require a decoded current graph')
    creates = [row for row in child_edits or [] if isinstance(row, dict) and row.get('operation') == 'create']
    remaining = [row for row in child_edits or [] if row not in creates]
    if creates:
        raw, report = edit_sfc_children(current, creates, allow_draft=save_policy == 'draft')
        current = replace(current, source=parse_sfc_pou(raw)); reports.append(report)
    if graph_edits is not None:
        raw, report = edit_sfc_source(current, graph_edits, graph=graph)
        current = replace(current, source=parse_sfc_pou(raw)); reports.append(report)
    if remaining:
        raw, report = edit_sfc_children(current, remaining, allow_draft=save_policy == 'draft')
        current = replace(current, source=parse_sfc_pou(raw)); reports.append(report)
    if not reports:
        raise GXWFormatError('SFC edit requires graph or internal object changes')
    # Changes may not turn a resolved binding into a dangling registration.
    previous = set(context.source.diagnostics)
    added = [issue for issue in current.source.diagnostics if issue not in previous]
    if save_policy == 'draft':
        added = [issue for issue in added if not issue.startswith('uninterpreted SFC action qualifier ')]
    if added:
        raise GXWFormatError('SFC edit leaves unresolved source ownership: ' + '; '.join(added))
    cache = 'original-bytes-preserved'
    if (current.source.raw != context.source.raw and (graph_edits is not None or registry_changed)
            and current.source.graph_tokens is not None):
        # Independent FX3U/Q00J controls compile, save and cold-reopen this
        # original minimal cache form after object and topology changes. The
        # old cache contains native transient values and describes old source.
        region = current.source.layout['graph']
        original = current.source
        body = bytearray(original.raw[region['offset']:region['cache_offset']] + struct.pack('<I', 4))
        struct.pack_into('<I', body, 0, len(body))
        children, actions = sfc_components(original)
        raw = serialize_sfc_components(original, children=children, actions=actions, graph=bytes(body))
        current = replace(current, source=parse_sfc_pou(raw))
        cache = 'invalidated-to-native-minimal-form'
    return current.source.raw, {'operation': 'offline_sfc_program_edit', 'native_calls': 0,
        'changed': current.source.raw != context.source.raw, 'transactions': reports,
        'cache': cache,
        'graph': (decode_sfc_graph(current.source, cpu=context.project['cpu']) if current.source.graph_tokens is not None else
                  {'status': 'not-established', 'storage_words': current.source.layout['graph']['storage_words'],
                   'reason': 'graph bytes retained in an unconverted storage mode'})}
