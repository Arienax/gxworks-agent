"""Task projection of authored facts, never a PLC admission or source rewrite.

Select relevant roots, then preserve their full prerequisites, conditions and
uncertainties. Unknown applicability is retained; only proven mismatches are
excluded as roots. The authoritative corpus and checker inputs stay untouched.
"""
from __future__ import annotations
import copy
import json
import re
from collections.abc import Mapping
from plc.instruction_definition import DefinitionError, select_fact_dependencies

_ALIASES = {
    'arity': ('parameters.arity', 'parameters.typed_operands'),
    'order': ('parameters.order', 'parameters.source_order', 'parameters.typed_operands'),
    'roles': ('parameters.roles', 'parameters.typed_operands'),
    'types': ('parameters.types', 'parameters.value_type', 'parameters.storage_domain', 'parameters.typed_operands'),
    'devices': ('parameters.devices', 'constraints.operand_devices'),
    'st_call': ('parameters.st_signature', 'parameters.language_expression'),
    'fbd_call': ('parameters.fbd_signature', 'parameters.fbd_pins', 'parameters.language_expression'),
}
_PREFIXES = {'parameters': ('parameters.',), 'memory': ('memory.',), 'effects': ('effects.', 'memory.'), 'execution': ('execution.',),
    'constraints': ('constraints.',), 'same_scan': ('execution.same_scan', 'effects.same_scan'),
    'first_execution': ('execution.first_execution', 'execution.startup', 'execution.mode_change'),
    'jump': ('execution.jump',), 'timing_state': ('execution.',), 'disabled': ('execution.', 'effects.')}
_FACETS = {'purpose': 'purpose', 'unit': 'unit', 'encoding': 'encoding',
           'operand_range': 'range', 'operand_condition': 'condition'}


def _wanted(fact, aspects, *, include_mandatory=True):
    dimension = fact.get('dimension', '')
    # Identity/availability and capability limits must survive narrowing.
    if include_mandatory and (dimension.startswith('identity.') or dimension in {'constraints.cpu_availability', 'execution.core_execution_capability'}):
        return True
    if not aspects:
        return True
    if include_mandatory and set(aspects).intersection(_FACETS) and dimension in {'parameters.arity', 'parameters.order', 'parameters.source_order', 'parameters.typed_operands'}:
        return True
    for aspect in aspects:
        if dimension == aspect or dimension.startswith(aspect + '.') or dimension in _ALIASES.get(aspect, ()) or any(dimension.startswith(p) for p in _PREFIXES.get(aspect, ())):
            return True
        if aspect in _FACETS and (dimension.endswith('.' + _FACETS[aspect]) or
                aspect == 'operand_range' and dimension == 'constraints.numeric_ranges'):
            return True
    return False


def _applicability(fact, *, opcode, model, language, version, context):
    unknown = bool(set(fact.get('scope', {})) - {'forms', 'models', 'languages', 'versions'})
    for key, actual in (('forms', opcode), ('models', model), ('languages', language), ('versions', version)):
        choices = fact.get('scope', {}).get(key)
        if choices:
            if actual is None: unknown = True
            elif actual not in choices: return 'not_applicable'
    from plc.instruction_constraints import _condition
    values = context.get('parameter_values', {})
    values = values if isinstance(values, Mapping) else {}
    for condition in fact.get('conditions', ()):
        try:
            if not _condition(condition, values, context): return 'not_applicable'
        except (DefinitionError, TypeError, ValueError, KeyError):
            unknown = True
    # Source-level conditions must not disappear through a projection.
    if any(source.get('conditions') or source.get('applicability') for source in fact.get('sources', ())):
        unknown = True
    return 'unverified' if unknown else 'applicable'


def _owner_key(packet):
    value = packet.get('value') or {}
    scope = packet.get('scope') or {}
    model = value.get('target_model') or scope.get('model')
    models = (model,) if model else tuple(sorted(scope.get('models') or ()))
    return (str(packet.get('target') or value.get('opcode') or '').upper(), models)


def _packet_nodes(packet):
    yield packet
    for related in (packet.get('value') or {}).get('related_instructions', ()):
        if isinstance(related, dict):
            yield from _packet_nodes({'target': related.get('opcode'), 'value': related})


def project_packet(packet, *, plc_model, language=None, aspects=None, project_mode=None,
                   instance_context=None, version=None, query=None, _dependency_pool=None):
    """Return a detached, explicitly partial packet with complete root closures."""
    result = copy.deepcopy(packet)
    value = result.get('value')
    if not isinstance(value, dict) or not isinstance(value.get('facts'), list):
        return result
    from plc.target_identity import target_members
    members = target_members(plc_model)
    owner_models = (packet.get('scope') or {}).get('models', ())
    model = (packet.get('scope') or {}).get('model') or value.get('target_model') or (
        next((member for member in members if member in owner_models), None) if owner_models else None) or plc_model
    owner_mismatch = (model not in members and model != plc_model) or bool(owner_models and not set(owner_models).intersection(members))
    opcode = str(packet.get('target') or value.get('opcode') or '').upper()
    context = copy.deepcopy(dict(instance_context)) if isinstance(instance_context, Mapping) else {}
    context.pop('memory_reader', None)  # Retrieval never executes a caller-supplied memory reader.
    context['$PLC_MODEL'] = model
    if project_mode is not None: context['project_mode'] = project_mode
    aspects = list(dict.fromkeys([aspects] if isinstance(aspects, str) else aspects or ()))
    facts = value['facts']
    by_id = {f.get('id'): f for f in facts}
    gaps, roots, selected = [], [], set()
    if len(by_id) != len(facts):
        result.setdefault('gaps', []).append('fact_selection_duplicate_identity')
        result['fact_selection'] = {'whole_instruction_complete': False, 'gaps': [{'reason': 'duplicate_identity'}]}
        return result  # Do not arbitrarily choose an ambiguous fact.
    applicability = {identity: _applicability(f, opcode=opcode, model=model, language=language,
        version=version, context=context) for identity, f in by_id.items()}
    signature_pool = (_dependency_pool or {}).get(_owner_key(packet), {})
    def signature_complete(identity):
        closure_facts, seen, active = {}, set(), set()
        def visit(key):
            if key in active: return False
            if key in seen: return True
            fact = by_id.get(key)
            if fact is None:
                candidates = {json.dumps(f, sort_keys=True): f for f in signature_pool.get(key, ())}
                if len(candidates) != 1: return False
                fact = next(iter(candidates.values()))
            if _applicability(fact, opcode=opcode, model=model, language=language,
                    version=version, context=context) != 'applicable': return False
            active.add(key)
            if not all(visit(dep) for dep in fact.get('depends_on', ())): return False
            active.remove(key)
            seen.add(key)
            closure_facts[key] = fact
            return True
        if not visit(identity): return False
        try:
            closure = select_fact_dependencies(list(closure_facts.values()), [identity],
                opcode=opcode, model=model, language=language, version=version)
            return not closure['gaps'] and closure.get('source_verification_complete', False)
        except (DefinitionError, TypeError, ValueError, KeyError):
            return False
    signatures, unsafe_signature = set(), False
    for identity, fact in by_id.items():
        if fact.get('dimension') not in {'parameters.order', 'parameters.source_order'}:
            continue
        if applicability[identity] == 'not_applicable' or not fact.get('value', {}).get('symbols'):
            continue
        if not signature_complete(identity):
            unsafe_signature = True
            gaps.append({'fact_id': identity, 'reason': 'operand_order_dependency_unresolved'})
        else:
            signatures.add(tuple(fact['value']['symbols']))
    positions = set()
    if len(signatures) > 1:
        gaps.append({'reason': 'ambiguous_operand_order'})
    elif signatures and not unsafe_signature:
        for index, symbol in enumerate(next(iter(signatures)), start=1):
            if isinstance(symbol, str) and re.search(r'(?<![A-Za-z0-9_])' + re.escape(symbol) + r'(?![A-Za-z0-9_])', str(query or ''), re.I):
                positions.add(index)
    for identity, fact in by_id.items():
        position = re.match(r'parameters\.(\d+)\.', fact.get('dimension', ''))
        selected_position = not positions or not position or int(position[1]) in positions
        if not owner_mismatch and selected_position and _wanted(fact, aspects) and applicability[identity] != 'not_applicable':
            roots.append(identity)
    for aspect in aspects:
        if not any(_wanted(by_id[identity], [aspect], include_mandatory=False) for identity in roots):
            gaps.append({'reason': 'requested_aspect_not_selected', 'aspect': aspect})
    if owner_mismatch: gaps.append({'reason': 'owner_model_not_requested'})
    # Complete prerequisites from this retrieval's same-owner pool only.
    # Added prerequisites never become extra task roots.
    pool = (_dependency_pool or {}).get(_owner_key(packet), {})
    visited = set()
    def gather(identity):
        if identity in visited: return
        visited.add(identity)
        if identity not in by_id:
            candidates = pool.get(identity, ())
            unique = {json.dumps(f, sort_keys=True): f for f in candidates}
            if len(unique) == 1:
                fact = copy.deepcopy(next(iter(unique.values())))
                by_id[identity] = fact
                facts.append(fact)
                applicability[identity] = _applicability(fact, opcode=opcode, model=model,
                    language=language, version=version, context=context)
            elif len(unique) > 1:
                gaps.append({'fact_id': identity, 'reason': 'ambiguous_dependency_identity'})
        for dependency in by_id.get(identity, {}).get('depends_on', ()):
            gather(dependency)
    for identity in roots: gather(identity)
    # Use the shared atomic closure checker for diagnostics. Unknown/conflicting
    # roots are deliberately still delivered below, with their original status.
    try:
        closure = select_fact_dependencies(facts, roots, opcode=opcode, model=model,
            language=language, version=version, allow_unknown_scope=True)
        gaps.extend(closure['gaps'])
    except (DefinitionError, TypeError, KeyError, ValueError):
        gaps.append({'reason': 'fact_selection_graph_invalid'})
    def include(identity, stack=()):
        if identity in stack:
            gaps.append({'fact_id': identity, 'reason': 'cyclic_dependency'}); return
        if identity in selected: return
        fact = by_id.get(identity)
        if fact is None:
            gaps.append({'fact_id': identity, 'reason': 'missing_dependency'}); return
        selected.add(identity)
        if applicability[identity] != 'applicable':
            gaps.append({'fact_id': identity, 'reason': 'dependency_' + applicability[identity]})
        for dep in fact.get('depends_on', ()): include(dep, (*stack, identity))
    for identity in roots: include(identity)
    value['facts'] = [f for f in facts if f.get('id') in selected]
    if isinstance(value.get('related_instructions'), list):
        related_values = []
        for related in value['related_instructions']:
            if not isinstance(related, dict): continue
            projected = project_packet({'kind': 'instruction', 'target': related.get('opcode'),
                'value': related}, plc_model=plc_model, language=language, aspects=aspects,
                project_mode=project_mode, instance_context=instance_context, version=version, query=query,
                _dependency_pool=_dependency_pool)
            related_values.append(projected['value'])
            gaps.extend(projected.get('fact_selection', {}).get('gaps', ()))
        value['related_instructions'] = related_values
    for fact in value['facts']:
        fact['selection_applicability'] = applicability[fact['id']]
    # A selected empty packet is still explicit evidence of a coverage gap.
    if not roots: gaps.append({'reason': 'no_applicable_requested_facts'})
    result['fact_selection'] = {'policy': 'task_roots_with_full_dependencies_v1',
        'language': language, 'project_mode': project_mode, 'version': version,
        'aspects': aspects, 'root_ids': roots, 'selected_count': len(selected),
        'available_count': len(facts), 'partial_projection': len(selected) != len(facts),
        'gaps': list({json.dumps(g, sort_keys=True): g for g in gaps}.values()),
        'whole_instruction_complete': False}
    if gaps:
        result['gaps'] = list(dict.fromkeys([*result.get('gaps', []), 'task_selection_unresolved']))
    return result


def project_instruction_records(records, **options):
    """Narrow authored JSON envelopes only; leave legacy evidence unmodified."""
    records = list(records)
    dependency_pool = {}
    for record in records:
        for line in str(record.get('text', '')).splitlines():
            if not line.startswith('{'): continue
            try: packet = json.loads(line)
            except ValueError: continue
            if not isinstance(packet, dict) or packet.get('content_origin') != 'authored_fact' or packet.get('kind') != 'instruction': continue
            for node in _packet_nodes(packet):
                key = _owner_key(node)
                # No explicit owner identity means no safe cross-record join.
                if not key[0] or not key[1]: continue
                pool = dependency_pool.setdefault(key, {})
                for fact in (node.get('value') or {}).get('facts', ()):
                    pool.setdefault(fact.get('id'), []).append(fact)
    results = []
    for record in records:
        row = copy.deepcopy(record)
        lines = str(row.get('text', '')).splitlines()
        for index, line in enumerate(lines):
            if not line.startswith('{'): continue
            try: packet = json.loads(line)
            except ValueError: continue
            if not isinstance(packet, dict) or packet.get('content_origin') != 'authored_fact' or packet.get('kind') != 'instruction': continue
            projected = project_packet(packet, _dependency_pool=dependency_pool, **options)
            lines[index] = json.dumps(projected, ensure_ascii=False, separators=(',', ':'))
            row['text'] = '\n'.join(lines)
            row['fact_selection'] = copy.deepcopy(projected.get('fact_selection', {}))
            break
        results.append(row)
    return results
