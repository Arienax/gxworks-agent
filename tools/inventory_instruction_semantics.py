"""Inventory installed instruction content once, without compiling or admitting calls.

The unit identity is a registered base family plus its real interface and source
conditions. Literal D/P names are members, never extra repair accomplishments.
Source catalogue scopes add candidates only; they do not grant CPU support.
"""
from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping
import copy
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote


EXCLUDED_MODELS = frozenset({'FX5S', 'FX5U', 'FX5UC', 'FX5UJ'})
ASPECTS = ('applicability', 'arity', 'order', 'roles', 'types', 'purpose',
           'storage', 'effects', 'execution', 'disabled')
RULE_VERSION = 'instruction-content-inventory-v1'


def superseded_connection_conflict_ids(facts):
    """Select the documented block-port count already verified by typed operands.

    Width variants can be merged under one owner name. A block-port record for
    another width is not a competing statement about this owner's call shape.
    """
    counts = set()
    for fact in facts:
        if (fact.get('status') == 'source_verified'
                and fact.get('dimension') == 'parameters.typed_operands'):
            operands = (fact.get('value') or {}).get('operands')
            if isinstance(operands, list):
                counts.add(len(operands))
    conflicts = [fact for fact in facts if fact.get('status') == 'conflict'
                 and fact.get('dimension') == 'parameters.connection_role']
    matching = [fact for fact in conflicts
                if len((fact.get('value') or {}).get('symbols') or ()) in counts]
    if counts and conflicts and len(matching) == 1:
        return {fact['id'] for fact in conflicts}
    return set()


def duplicate_variant_conflict_ids(facts):
    """Merge artifacts that repeat one subset of a verified variant table.

    A verified family/owner table is the complete documented form set. A second
    extraction of only one section is not a competing source statement.
    """
    verified = set()
    for fact in facts:
        if fact.get('status') in {'source_verified', 'declared_unverified'}:
            variants = (fact.get('value') or {}).get('section_variants')
            if isinstance(variants, list):
                verified.update(str(item) for item in variants)
    conflicts = [fact for fact in facts if fact.get('status') == 'conflict']
    variants = [fact for fact in conflicts if fact.get('dimension') == 'parameters.variant_family']
    duplicates = {fact['id'] for fact in variants
                  if set(map(str, (fact.get('value') or {}).get('section_variants') or ()))
                  <= verified}
    if verified and duplicates and len(duplicates) == len(variants):
        duplicates.update(fact['id'] for fact in conflicts
                          if fact.get('id', '').startswith('instruction_class_review:'))
    return duplicates


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def declared_family_bindings(registry, models):
    """Read literal, CPU-scoped authored bindings without global alias merging."""
    candidates = {}
    for definition in registry._family_knowledge:
        for fact in definition['facts']:
            if (fact.get('dimension') != 'parameters.variant_family' or fact.get('status') != 'source_verified'
                    or fact.get('verification') != 'source_checked'):
                continue
            for opcode in fact.get('value', {}).get('variant_bindings', {}):
                if opcode not in fact.get('scope', {}).get('forms', []):
                    continue
                for model in set(fact.get('scope', {}).get('models', [])).intersection(models):
                    candidates.setdefault((opcode, model), set()).add(definition['base_opcode'])
    return {key: {'base_opcode': next(iter(bases))} for key, bases in candidates.items() if len(bases) == 1}


def source_family_bindings(universe):
    """Explicit same-section members are identity hints, never call support."""
    candidates = {}
    for source in universe.get('entries', []):
        base = str(source.get('opcode', '')).strip().upper()
        if not base:
            continue
        for member in source.get('variants', []):
            member = str(member).strip().upper()
            if member and member != base:
                candidates.setdefault(member, set()).add(base)
    return {member: {'base_opcode': next(iter(bases))} for member, bases in candidates.items() if len(bases) == 1}


def _family(bindings, opcode, model, registry=None):
    explicit = bindings.get((opcode, model))
    if explicit:
        return explicit['base_opcode']
    form = registry.resolve_catalog_form(opcode, cpu=model) if registry else None
    if form and form.base_mnemonic != opcode:
        return form.base_mnemonic
    source_hint = bindings.get(opcode)
    return source_hint['base_opcode'] if source_hint else form.base_mnemonic if form else opcode


def conditions_for_model(conditions, model):
    result = []
    for original in conditions or ():
        condition = copy.deepcopy(original)
        if isinstance(condition, dict) and condition.get('context') == 'cpu_version':
            for mapped, literal in [('minimum_by_model', 'minimum'),
                                    ('minimum_exclusive_by_model', 'minimum_exclusive')]:
                if mapped in condition:
                    minimum = condition.pop(mapped).get(model)
                    if minimum is not None:
                        condition[literal] = minimum
        if condition not in result:
            result.append(condition)
    return sorted(result, key=canonical)


def _implies(branch, required, model):
    """Compare declared branches symbolically, never invent a project version."""
    from plc.instruction_constraints import version_condition_bounds
    for condition in required:
        if condition in branch:
            continue
        try:
            wanted = version_condition_bounds(condition, model=model)
            bounds = [version_condition_bounds(item, model=model) for item in branch]
        except (ValueError, TypeError, KeyError):
            return False
        if wanted is None:
            return False
        path, minimum, maximum, minimum_exclusive = wanted
        matches = [bound for bound in bounds if bound is not None and bound[0] == path]
        lower = max(((bound[1], bound[3]) for bound in matches if bound[1] is not None), default=None)
        upper = min((bound[2] for bound in matches if bound[2] is not None), default=None)
        if (minimum is not None and (lower is None or lower < (minimum, minimum_exclusive))
                or maximum is not None and (upper is None or upper > maximum)):
            return False
    return True


def _consistent(conditions, model):
    from plc.instruction_constraints import version_condition_bounds
    assignments, versions = {}, {}
    for condition in conditions:
        if not isinstance(condition, Mapping):
            return False
        if set(condition) == {'context', 'equals'}:
            key, value = condition['context'], canonical(condition['equals'])
        elif condition.get('context') == 'device_state' and 'device' in condition and 'equals' in condition:
            key, value = ('device_state', condition['device']), canonical(condition['equals'])
        else:
            key = value = None
        if key is not None:
            if key in assignments and assignments[key] != value:
                return False
            assignments[key] = value
        try:
            bound = version_condition_bounds(condition, model=model)
        except (ValueError, TypeError, KeyError):
            bound = None
        if bound is not None:
            path, minimum, maximum, minimum_exclusive = bound
            prior = versions.setdefault(path, [None, None])
            if minimum is not None:
                prior[0] = max(prior[0], minimum) if prior[0] is not None else minimum
            if maximum is not None:
                prior[1] = min(prior[1], maximum) if prior[1] is not None else maximum
    return all(lower is None or upper is None or lower < upper for lower, upper in versions.values())


def _interface(fact):
    value = fact.get('value') or {}
    mode = next((condition.get('equals') for condition in fact.get('conditions', ())
                 if condition.get('context') == 'project_mode'), None)
    representation = value.get('representation')
    if isinstance(representation, str) and representation.casefold() == 'st_expression':
        return [('st_expression', 'st')]
    if representation == 'native_ladder_instruction' or value.get('native_form') is not None:
        return [('native_ladder_instruction', 'ladder')]
    if mode == 'native_ladder_instruction':
        return [(mode, 'ladder')]
    if mode == 'structured_ladder_block':
        return [(mode, 'ladder')]
    dimension = fact.get('dimension')
    if dimension == 'parameters.st_signature':
        return [('st_call', 'st')]
    if dimension in {'parameters.fbd_signature', 'parameters.fbd_pins'}:
        return [('fbd_call', 'fbd')]
    if dimension == 'parameters.ladder_signature':
        return [('structured_ladder_block', 'ladder')]
    languages = fact.get('scope', {}).get('languages') or []
    if dimension == 'parameters.language_expression':
        return [('st_expression' if language == 'st' else language + '_expression', language)
                for language in languages]
    if dimension == 'parameters.typed_operands':
        # Generated declaration tables retain their parent's literal language.
        if len(languages) == 1:
            language = languages[0]
            return [('native_ladder_instruction' if language == 'ladder' else language + '_call', language)]
        if representation:
            return [(representation, language) for language in languages]
    return []


def _references(facts):
    rows = {}
    for fact in facts:
        source_refs = [{key: source[key] for key in ('manual_id', 'manual_number', 'revision', 'pdf_pages',
                       'pdf_page', 'source_id', 'representation') if key in source}
                       for source in fact.get('sources', ())]
        row = {'fact_id': fact['id'], 'dimension': fact['dimension'], 'status': fact.get('status'),
               'sources': source_refs}
        rows[canonical(row)] = row
    return list(rows.values())


def _closure(facts, identity, *, opcode, model, language):
    from plc.instruction_definition import DefinitionError, select_fact_dependencies
    try:
        result = select_fact_dependencies(facts, [identity], opcode=opcode, model=model,
                                          language=language, allow_unknown_scope=True)
    except (DefinitionError, ValueError, TypeError, KeyError) as error:
        return 'invalid_dependency_graph', [], str(error)
    hard_gaps = [gap for gap in result['gaps'] if gap['reason'] != 'scope_context_missing']
    if hard_gaps:
        reason = 'source_conflict' if any(gap['reason'] == 'conflict' for gap in hard_gaps) else 'dependency_incomplete'
        return reason, result['groups'], canonical(hard_gaps)
    checked = all(row.get('status') == 'source_verified' and row.get('verification') == 'source_checked'
                  and row.get('sources') for row in result['groups'])
    return ('answered' if checked else 'source_review_pending'), result['groups'], ''


def _known(value):
    if isinstance(value, Mapping):
        return bool(value) and all(_known(child) for child in value.values())
    if isinstance(value, list):
        return all(_known(child) for child in value)
    return value is not None and (not isinstance(value, str) or value.lower() not in
                                 {'', 'unknown', 'unresolved', 'not_established'})


def _execution_value_known(fact):
    value = fact.get('value')
    if (isinstance(value, Mapping) and (value.get('full_source_relation_complete') is False
                                       or value.get('unresolved_items'))):
        return False
    if (fact.get('dimension') == 'execution.trigger' and isinstance(value, Mapping)
            and value.get('trigger') in {'each_evaluation_when_execution_condition_true',
                                         'each_scan_when_execution_condition_true'}
            and 'executions_per_edge' in value and value['executions_per_edge'] is None):
        # Edge counts do not apply to level execution; every other field still
        # needs an answer, and the caller must separately verify dependencies.
        value = {key: child for key, child in value.items() if key != 'executions_per_edge'}
    return _known(value)


def _type_abstract_name(kind):
    """Abstract type domain name as declared, whether bare or inside a mapping.

    Corpus operands declare the same abstract domain either as ``{"type": "ANY_NUM"}``
    or as ``{"data_type": {"flexible_type": "ANY_NUM"}}`` / ``{"kind": "ANY_NUM"}``.
    Only the name is recovered here; whether a name is acceptable still depends on
    the interface, and no width is inferred from an abstract name.
    """
    if isinstance(kind, str):
        return kind
    if isinstance(kind, Mapping):
        for key in ('kind', 'flexible_type'):
            value = kind.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _type_bits(parameter):
    from plc.instruction_definition import DefinitionError, ValueType
    kind = parameter.get('type', parameter.get('data_type'))
    if parameter.get('type_completeness') == 'partial' or parameter.get('width_bits') in ('unknown', 'unresolved'):
        return None
    name = _type_abstract_name(kind)
    if name is not None and name.startswith('ANY') and name[3:] in {'16', '32', '64'}:
        return int(name[3:])
    try:
        value = ValueType.from_mapping(kind) if isinstance(kind, Mapping) else ValueType.from_iec(kind)
        return value.bits if value.bits else None
    except (DefinitionError, ValueError, TypeError, AttributeError):
        return None


def _reference_kind(parameter):
    kind = parameter.get('type')
    return isinstance(kind, str) and kind in {'DEVICE_REFERENCE', 'ANY_SIMPLE', 'STRING'}


def _string_type(parameter):
    from plc.instruction_definition import DefinitionError, ValueType
    kind = parameter.get('type', parameter.get('data_type'))
    try:
        value = ValueType.from_mapping(kind) if isinstance(kind, Mapping) else ValueType.from_iec(kind)
        return value.kind == 'string'
    except (DefinitionError, ValueError, TypeError, AttributeError):
        return False


def _array_element_type_present(parameter):
    from plc.instruction_definition import DefinitionError, ValueType
    reference = parameter.get('array_element_reference')
    if (not isinstance(reference, Mapping) or reference.get('start') != 'selected_array_element'
            or parameter.get('argument_passing') != 'array_element_reference'):
        return False
    try:
        kind = parameter.get('type', parameter.get('data_type'))
        actual = ValueType.from_mapping(kind) if isinstance(kind, Mapping) else ValueType.from_iec(kind)
        element = ValueType.from_mapping(reference.get('element_type'))
        return actual == element and element.kind == 'bool'
    except (DefinitionError, ValueError, TypeError, AttributeError):
        return False


def _type_present(parameter, interface):
    if parameter.get('type_completeness') == 'partial':
        return False
    if 'array_element_reference' in parameter or parameter.get('argument_passing') == 'array_element_reference':
        return _array_element_type_present(parameter)
    kind = parameter.get('type', parameter.get('data_type'))
    if kind == 'UNION':
        alternatives = parameter.get('alternatives')
        return (isinstance(alternatives, list) and bool(alternatives)
                and all(isinstance(alternative, Mapping) and _type_present(alternative, interface)
                        for alternative in alternatives))
    if 'type_alternatives' in parameter:
        alternatives = parameter['type_alternatives']
        return (isinstance(alternatives, list) and bool(alternatives)
                and all(_type_present({'type': alternative}, interface) for alternative in alternatives))
    if isinstance(kind, Mapping) and kind.get('kind') == 'array':
        return (type(kind.get('index_min')) is int and type(kind.get('index_max')) is int
                and kind['index_max'] >= kind['index_min']
                and _type_present({'type': kind.get('element_type')}, interface))
    if kind == 'ARRAY':
        shape = parameter.get('array') or {}
        return (type(shape.get('length')) is int and shape['length'] > 0
                and _type_present({'type': parameter.get('element_type'), **(
                    {'type_alternatives': parameter['element_type_alternatives']}
                    if 'element_type_alternatives' in parameter else {})}, interface))
    if interface != 'native_ladder_instruction':
        if _string_type(parameter):
            return True
        name = _type_abstract_name(kind)
        if name is not None and (name == 'ANY' or name.startswith('ANY_')):
            return True  # The source declares an abstract language type domain.
    return (_type_bits(parameter) is not None
            or (_reference_kind(parameter) or _string_type(parameter)) and _layout_present(parameter))


def _layout_present(parameter):
    if 'array_element_reference' in parameter or parameter.get('argument_passing') == 'array_element_reference':
        if not _array_element_type_present(parameter):
            return False
        extent = parameter['array_element_reference'].get('extent')
        # This is the source-defined logical access span from the selected
        # element, not the whole array capacity, packing or pointer ABI.
        return (isinstance(extent, Mapping) and extent.get('unit') == 'logical_elements'
                and isinstance(extent.get('parameter'), str) and bool(extent['parameter'])
                and extent.get('constant_only') is True
                and isinstance(extent.get('allowed_counts'), list) and bool(extent['allowed_counts'])
                and all(type(count) is int and count > 0 for count in extent['allowed_counts']))
    layout = parameter.get('string_layout')
    if _string_type(parameter) and isinstance(layout, Mapping):
        return (layout.get('unit_bits') in (8, 16)
                and type(layout.get('terminator')) is int and layout['terminator'] == 0
                and layout.get('extent') == 'through_first_terminator'
                and (layout.get('within_device_range') is True
                     or layout.get('unit_bits') == 8
                     and layout.get('device_range_check') == 'as_compared'
                     and layout.get('short_circuit_extent') == 'first_difference_or_both_terminators'))
    if parameter.get('word_device_layout'):
        layout = parameter['word_device_layout']
        # A typed device head does not establish its conditional payload span.
        return layout.get('head_storage_only') is not True and _known(layout.get('word_count'))
    if parameter.get('occupied_bit_count') is not None:
        return _known(parameter['occupied_bit_count'])
    if parameter.get('write_layout') and _known(parameter['write_layout'].get('count')):
        return True
    kind = parameter.get('type')
    if isinstance(kind, Mapping) and kind.get('kind') == 'array' or kind in ('ARRAY', 'UNION'):
        return _type_present(parameter, 'native_ladder_instruction')
    return _type_bits(parameter) is not None


def _storage_present(parameter, interface, *, sources=()):
    # A language type domain (ANY_BIT/ANY_NUM/STRING) does not establish a
    # physical span. Require an actual width or a source-defined layout on
    # every interface; a concrete ANY16/ANY32 width still qualifies normally.
    conditional = parameter.get('storage_by_type')
    if conditional is not None:
        # This is a source-bound structured value span selected by its actual
        # declaration. It is not an allocation of native device registers.
        kind = parameter.get('type', parameter.get('data_type'))
        members = parameter.get('type_alternatives')
        expected_members = {'ANY_BIT': {'BOOL', 'WORD', 'DWORD'}, 'ANY_NUM': {'INT', 'DINT', 'REAL'},
                            'ANY_INT': {'INT', 'DINT'}}.get(
            _type_abstract_name(kind))
        scoped_numeric = (_type_abstract_name(kind) == 'ANY_NUM' and isinstance(members, list)
                          and all(isinstance(member, str) for member in members)
                          and set(members) == {'INT', 'DINT', 'REAL', 'LREAL'})
        if scoped_numeric:
            expected_members = {'INT', 'DINT', 'REAL', 'LREAL'}
        if (interface == 'native_ladder_instruction' or expected_members is None
                or not isinstance(members, list) or len(members) != len(expected_members)
                or any(not isinstance(member, str) for member in members)
                or set(members) != expected_members
                or not isinstance(conditional, Mapping) or set(conditional) != {'selected_by', 'branches'}
                or conditional.get('selected_by') != 'actual_parameter_type'):
            return False
        branches = conditional.get('branches')
        if not isinstance(branches, list) or len(branches) != len(members):
            return False
        def source_identity(source):
            if (not isinstance(source, Mapping) or not isinstance(source.get('manual_id'), str)
                    or not source['manual_id'] or not isinstance(source.get('revision'), str) or not source['revision']
                    or not isinstance(source.get('pdf_pages'), list) or not source['pdf_pages']
                    or any(type(page) is not int or page <= 0 for page in source['pdf_pages'])):
                return None
            return source['manual_id'], source['revision'], tuple(sorted(source['pdf_pages']))
        source_ids = {identity for source in sources if (identity := source_identity(source)) is not None}
        seen = set()
        for branch in branches:
            support_required = scoped_numeric and isinstance(branch, Mapping) and branch.get('type') in {'REAL', 'LREAL'}
            branch_keys = {'type', 'occupied_bit_count', 'sources'} | ({'type_support_scope'} if support_required else set())
            if (not isinstance(branch, Mapping) or set(branch) != branch_keys
                    or not isinstance(branch.get('type'), str) or branch['type'] not in members
                    or branch['type'] in seen or type(branch.get('occupied_bit_count')) is not int
                    or branch['occupied_bit_count'] != _type_bits({'type': branch['type']})
                    or not isinstance(branch.get('sources'), list) or not branch['sources']
                    or any(source_identity(source) not in source_ids for source in branch['sources'])):
                return False
            if scoped_numeric:
                required_sources = {('structured_fundamentals_o', 'O', (34,)),
                                    ('structured_fundamentals_o', 'O', (35,)),
                                    ('fxcpu_application_k', 'K', (34,))}
                if not required_sources <= {source_identity(source) for source in branch['sources']}:
                    return False
            if support_required:
                support = branch.get('type_support_scope')
                expected_models = ['FX3S', 'FX3G', 'FX3GC', 'FX3U', 'FX3UC'] if branch['type'] == 'REAL' else []
                expected_families = [] if branch['type'] == 'REAL' else ['universal_model_qcpu', 'lcpu']
                if (not isinstance(support, Mapping) or set(support) != {
                        'basis', 'documented_models', 'documented_cpu_families', 'unlisted_targets',
                        'instruction_availability', 'sources'}
                        or support.get('basis') != 'elementary_data_type_table_footnote'
                        or support.get('documented_models') != expected_models
                        or support.get('documented_cpu_families') != expected_families
                        or support.get('unlisted_targets') != 'support_not_established_by_this_type_table'
                        or support.get('instruction_availability') != 'separate_own_instruction_scope'
                        or not isinstance(support.get('sources'), list) or not support['sources']
                        or any(source_identity(source) != ('structured_fundamentals_o', 'O', (34,))
                               for source in support['sources'])):
                    return False
            seen.add(branch['type'])
        return seen == set(members)
    return _layout_present(parameter)


def _typed_reference_step(value):
    """Recognize checked mathematical steps without granting an executor."""
    from plc.instruction_definition import DefinitionError, Expression, ValueType
    from plc.instruction_effects import validate_behavior
    bindings = value.get('state_bindings')
    iteration = value.get('iteration')
    step = value.get('reference_step')
    fixed_step = 'state_bindings' not in value and 'iteration' not in value
    if not (isinstance(step, Mapping) and isinstance(step.get('outputs'), list)):
        return False
    if not fixed_step and not (isinstance(bindings, Mapping) and bindings and isinstance(iteration, Mapping)):
        return False
    if fixed_step:
        bindings = {}
    def checked_expression(raw):
        expression = Expression.from_mapping(raw)
        pending = [expression.as_mapping()]
        while pending:
            item = pending.pop()
            if item.get('op') == 'state' and item.get('name') not in bindings:
                raise DefinitionError('Reference state is not bound')
            pending.extend(item.get('args', []))
        return expression
    try:
        if fixed_step:
            for output in step['outputs']:
                if not isinstance(output, Mapping):
                    return False
                checked_expression(output.get('expression'))
            validate_behavior({'behavior': 'expression_write', 'outputs': step['outputs']})
            return True
        if checked_expression(iteration.get('count_expression')).value_type.kind != 'int':
            return False
        for binding in bindings.values():
            if not isinstance(binding, Mapping):
                return False
            if 'type' in binding:
                ValueType.from_mapping(binding['type'])
            if 'derived_value' in binding:
                checked_expression(binding['derived_value'])
        outputs = []
        for output in step['outputs']:
            if not isinstance(output, Mapping) or not isinstance(output.get('target'), Mapping):
                return False
            target = dict(output['target'])
            expression = checked_expression(output.get('expression'))
            if target.get('kind') == 'selected_operand_span':
                span = bindings.get(target.get('effective_bits'), {})
                if (target.get('parameter') != value.get('output') or expression.value_type.kind != 'int'
                        or not isinstance(span, Mapping)
                        or span.get('word_device_bits') != expression.value_type.bits):
                    return False
                target = {'kind': 'word', 'parameter': target['parameter'], 'offset': 0}
            elif target.get('kind') == 'bit' and 'device' in target:
                target = {'kind': 'bit', 'parameter': target['device'], 'offset': 0}
            outputs.append({'target': target, 'expression': expression.as_mapping()})
        validate_behavior({'behavior': 'expression_write', 'outputs': outputs})
    except (DefinitionError, TypeError, ValueError, KeyError):
        return False
    return True


def _concrete_relation(fact):
    """Require result content in addition to an operation/body label."""
    from knowledge.instruction_presentation import substantive_effect
    from plc.instruction_definition import DefinitionError, Expression
    from plc.instruction_effects import validate_behavior
    from plc.instruction_relations import _UNARY, _BINARY, _FOLDS
    value = fact.get('value') or {}
    if value.get('full_source_relation_complete') is False or value.get('unresolved_items'):
        return False
    state_content = (value.get('state_transitions') and _known(value['state_transitions'])
                     and (value.get('state_accesses') or value.get('state_bindings')))
    fixed_or_iterated_step = 'reference_step' in value and _typed_reference_step(value)
    if not substantive_effect(fact) and not state_content and not fixed_or_iterated_step:
        return False
    def actual(item):
        if not isinstance(item, Mapping):
            return False
        if 'reference_step' in item:
            return _typed_reference_step(item)
        if item.get('outputs'):
            try:
                validate_behavior({'behavior': item.get('behavior', item.get('kind', 'expression_write')),
                                   'outputs': item['outputs']})
                return True
            except (DefinitionError, TypeError, ValueError, KeyError):
                return False
        if item.get('behavior'):
            try:
                validate_behavior(item)
                return item['behavior'] != 'parameter_layout'
            except (DefinitionError, TypeError, ValueError, KeyError):
                return False
        if any(actual(rule) for rule in item.get('rules', ()) if isinstance(rule, Mapping)):
            return True
        # The ordinary binder uses both source spellings for arithmetic folds.
        # Normalize only this predicate; preserve the actual relation and all
        # source/type/active-pin qualifiers and dependency closure unchanged.
        operation = {'add': 'addition', 'multiply': 'multiplication'}.get(
            item.get('operation'), item.get('operation'))
        if operation == 'compare_values':
            binding = item.get('operand_binding')
            names, count = item.get('inputs'), item.get('input_count')
            if (not fact['id'].startswith('assembly:relation:') or not isinstance(binding, Mapping)
                    or not isinstance(names, list) or len(names) < 2
                    or any(not isinstance(name, str) or not name for name in names)
                    or len(set(names)) != len(names) or not isinstance(count, Mapping)
                    or item.get('comparison') not in ('eq', 'ne', 'gt', 'ge', 'lt', 'le')
                    or item.get('aggregation') not in ('all_adjacent_pairs', 'pair')):
                return False
            inputs, output = binding.get('inputs'), binding.get('output')
            if (not isinstance(inputs, list) or len(inputs) != len(names)
                    or not all(isinstance(pin, Mapping) and pin.get('parameter') == name
                               and _type_present(pin, 'fbd_call')
                               and type(pin.get('optional', False)) is bool
                               for pin, name in zip(inputs, names))
                    or not isinstance(output, Mapping) or output.get('type') not in ('BOOL', {'kind': 'bool'})
                    or not isinstance(item.get('output'), str) or not item['output']
                    or output.get('parameter') != item['output']
                    or item.get('result', item['output']) != item['output']):
                return False
            required = sum(not pin.get('optional', False) for pin in inputs)
            return (type(count.get('minimum')) is int and type(count.get('maximum')) is int
                    and count['minimum'] == required >= 2 and count['maximum'] == len(names)
                    and (item['aggregation'] != 'pair' or len(names) == required == 2))
        if (fact['id'].startswith('assembly:relation:') and item.get('operand_binding')
                and operation in (_UNARY | _BINARY | _FOLDS | {'transfer', 'copy_value'})):
            return True  # Rebuilt by the ordinary relation/type binder.
        if item.get('mathematical_value_relation') is True and item.get('input_value_type') and item.get('result_value_type'):
            return True
        relation_keys = {'algorithm', 'mapping', 'relation', 'value_relation', 'mathematical_relation',
                         'selector_cases', 'processing_modes', 'character_mapping', 'alphabet',
                         'output_value_range', 'state_transitions', 'destination_bit_values',
                         'result_by_selector', 'output_rules', 'cases', 'conversion_rules',
                         'result_relation', 'output_transitions', 'ordinary_outputs', 'flicker_composition'}
        if item.get('state_transitions') and _known(item['state_transitions']) and (
                item.get('state_accesses') or item.get('state_bindings')):
            return True
        if item.get('operation') and (item.get('inputs') is not None or item.get('state_accesses')) and item.get('output'):
            if any(_known(item.get(key)) for key in relation_keys if key in item):
                return True
        for child in item.values():
            if isinstance(child, Mapping) and 'op' in child and 'type' in child:
                try:
                    Expression.from_mapping(child)
                    if item.get('operation') and (item.get('output') or item.get('destination')):
                        return True
                except (DefinitionError, TypeError, ValueError, KeyError):
                    pass
        return False
    return actual(value)


def _partial_effect_content(fact):
    """Recognize described partial results without counting them as complete."""
    value = fact.get('value') or {}
    if value.get('full_source_relation_complete') is not False and not value.get('unresolved_items'):
        return False
    # This temporary view only asks whether result content exists. The original
    # flags, source status and dependency closure remain authoritative below.
    content = {key: child for key, child in value.items()
               if key not in {'full_source_relation_complete', 'unresolved_items'}}
    return _concrete_relation({**fact, 'value': content})


def _content_cases(facts, model):
    declaration_dimensions = {'parameters.typed_operands', 'parameters.st_signature',
                              'parameters.fbd_signature', 'parameters.ladder_signature',
                              'parameters.fbd_pins', 'parameters.language_expression'}
    declarations = [fact for fact in facts if fact.get('dimension') in declaration_dimensions
                    and _interface(fact) and fact.get('value', {}).get('available') is not False]
    typed_interfaces = {context for fact in declarations if fact.get('dimension') == 'parameters.typed_operands'
                        and fact.get('status') == 'source_verified' and fact.get('verification') == 'source_checked'
                        for context in _interface(fact)}
    declarations = [fact for fact in declarations if fact.get('dimension') == 'parameters.typed_operands'
                    or not any(context in typed_interfaces for context in _interface(fact))]
    positives = [fact for fact in facts if fact.get('dimension') in
                 {'constraints.cpu_availability', 'identity.applicability'}
                 and fact.get('value', {}).get('available') is True]
    result = {}
    for declaration in declarations:
        for interface, language in _interface(declaration):
            conditions = conditions_for_model(declaration.get('conditions', []), model)
            if interface == 'native_ladder_instruction' and not any(
                    condition.get('context') == 'project_mode' for condition in conditions):
                conditions.append({'context': 'project_mode', 'equals': interface})
            branch = conditions
            for positive in positives:
                scope = positive.get('scope') or {}
                if scope.get('languages') and language not in scope['languages']:
                    continue
                qualified = conditions_for_model([*branch, *positive.get('conditions', [])], model)
                if _consistent(qualified, model):
                    branch = qualified
            for branch in [branch]:
                value = declaration.get('value') or {}
                kind = value.get('declaration_kind', 'operator' if interface.endswith('_expression') else 'instruction')
                variant = value.get('instruction_variant', '')
                key = canonical([interface, language, kind, variant, branch])
                result[key] = {'interface': interface, 'language': language, 'declaration_kind': kind,
                               'instruction_variant': variant, 'conditions': branch}
    if not result:
        result['unbound'] = {'interface': 'source_interface_review', 'language': None,
                            'declaration_kind': 'source_declaration', 'instruction_variant': '', 'conditions': []}
    return list(result.values())


def inspect_owner(entry, registry, *, composed_facts=None, family_bindings=None):
    """Inspect actual content and atomic prerequisites, independent of Core."""
    from knowledge.structured_facts import _aspect_state, instruction_aspect_gap_reasons, _disabled_call_output_coverage
    from plc.instruction_knowledge import compose_programming_facts
    from plc.instruction_relations import same_relation_interface
    opcode, model = entry['opcode'], entry['target_model']
    facts = composed_facts
    if facts is None:
        facts, _ = compose_programming_facts(registry, opcode, cpu=model, existing=entry.get('facts', []))
    base = _family(family_bindings or {}, opcode, model, registry)
    limitations = [fact for fact in facts if str(fact.get('dimension', '')).endswith(('use_limits', 'unresolved_source'))
                   or fact.get('value', {}).get('gap_kind') in {'source_not_stated', 'reviewed_source_not_stated'}]
    limitation_ids = {fact['id'] for fact in limitations}
    rows = []
    for case in _content_cases(facts, model):
        interface, language, branch = case['interface'], case['language'], case['conditions']
        selected = []
        for fact in facts:
            scope = fact.get('scope') or {}
            if scope.get('languages') and language and language not in scope['languages']:
                continue
            required = conditions_for_model(fact.get('conditions', []), model)
            if not _implies(branch, required, model):
                continue
            if fact.get('dimension') == 'parameters.typed_operands':
                contexts = _interface(fact)
                if contexts and (interface, language) not in contexts:
                    continue
                if fact.get('value', {}).get('instruction_variant', '') != case['instruction_variant']:
                    continue
            selected.append({**fact, 'selection_applicability': 'applicable'})
        packet = {'kind': 'instruction', 'target': opcode,
                  'value': {**entry, 'facts': [fact for fact in selected if fact['id'] not in limitation_ids]}}
        negative = [fact for fact in selected if fact.get('dimension') in
                    {'constraints.cpu_availability', 'identity.applicability'}
                    and fact.get('value', {}).get('available') is False and fact.get('status') == 'source_verified']
        positive = [fact for fact in selected if (fact.get('dimension') in
                    {'constraints.cpu_availability', 'identity.applicability',
                     'identity.language_availability'}
                    or fact.get('dimension') in {'parameters.st_signature', 'parameters.fbd_signature'}
                    and fact.get('scope', {}).get('languages') in (['st'], ['fbd']))
                    and fact.get('value', {}).get('available') is True and fact.get('status') == 'source_verified']
        excluded = bool(negative) and not positive and all(_closure(selected, fact['id'], opcode=opcode,
                         model=model, language=language)[0] == 'answered' for fact in negative)
        states, remaining = {}, []
        signatures = [fact for fact in selected if fact.get('dimension') == 'parameters.typed_operands'
                      and isinstance(fact.get('value', {}).get('operands'), list)]
        for facet in ASPECTS:
            if excluded:
                state = 'not_applicable'
            elif interface == 'source_interface_review' and facet in {'arity', 'order', 'roles', 'types', 'purpose', 'storage'}:
                state = 'source_review_pending'
            elif facet == 'storage':
                state = 'answered' if any(all(_storage_present(parameter, interface, sources=fact.get('sources', ()))
                    for parameter in fact['value']['operands'])
                    and _closure(selected, fact['id'], opcode=opcode, model=model, language=language)[0] == 'answered'
                    for fact in signatures) else 'missing'
            else:
                state = _aspect_state(packet, facet, model=model, language=language, query='')
            if facet == 'applicability' and positive and not excluded:
                state = 'answered'  # A verified language-specific availability fact is applicability evidence.
            if facet == 'types' and state == 'answered' and signatures and not any(
                    all(_type_present(parameter, interface)
                        for parameter in fact['value']['operands']) for fact in signatures):
                state = 'source_review_pending'
            if facet == 'effects' and not excluded:
                # Error writes describe the error response, not the normal
                # input-to-destination result, even when those writes are typed.
                normal_effects = [fact for fact in selected
                                  if str(fact.get('dimension', '')).startswith('effects.')
                                  and fact.get('dimension') != 'effects.error_response']
                effects = [fact for fact in normal_effects if _concrete_relation(fact)]
                closures = [_closure(selected, fact['id'], opcode=opcode, model=model, language=language)[0]
                            for fact in effects]
                if 'answered' in closures:
                    state = 'answered'
                elif effects:
                    state = 'source_review_pending' if 'source_review_pending' in closures else 'unresolved'
                else:
                    partial = [fact for fact in normal_effects if _partial_effect_content(fact)]
                    partial_closures = [_closure(selected, fact['id'], opcode=opcode,
                        model=model, language=language)[0] for fact in partial]
                    state = ('source_review_pending' if 'source_review_pending' in partial_closures
                             else 'unresolved') if partial else 'missing'
            if facet == 'execution' and not excluded and state != 'answered':
                execution = [fact for fact in selected if fact['id'] not in limitation_ids
                             and str(fact.get('dimension', '')).startswith('execution.')
                             and fact.get('dimension') not in {'execution.core_execution_capability',
                                                              'execution.disabled_state'}]
                if not any(fact.get('status') == 'conflict' for fact in execution) and any(
                       _execution_value_known(fact) and _closure(selected, fact['id'], opcode=opcode,
                       model=model, language=language)[0] == 'answered' for fact in execution):
                    state = 'answered'
                if any(_concrete_relation(fact) and _known(fact.get('value', {}).get('execution_requirements'))
                       and _closure(selected, fact['id'], opcode=opcode, model=model, language=language)[0] == 'answered'
                       for fact in selected):
                    state = 'answered'
            if facet == 'disabled' and not excluded and state != 'answered':
                off_slots = {'command_OFF', 'on_command_fall', 'when_disabled', 'when_not_executed',
                             'disabled_state', 'disabled_effect', 'en_false'}
                def has_off_rule(value):
                    return (isinstance(value, Mapping) and (any(key in off_slots and _known(child)
                        for key, child in value.items()) or any(has_off_rule(child) for child in value.values()))
                        or isinstance(value, list) and any(has_off_rule(child) for child in value))
                if any(_concrete_relation(fact) and has_off_rule(fact.get('value')) and _closure(selected,
                       fact['id'], opcode=opcode, model=model, language=language)[0] == 'answered' for fact in selected):
                    state = 'answered'
                if any(_concrete_relation(fact) and fact.get('value', {}).get('execution_requirements', {}).get(
                       'call_each_scan_for_accurate_measurement') is True and _known(fact['value'][
                       'execution_requirements'].get('jumping_over_call')) and _closure(selected, fact['id'],
                       opcode=opcode, model=model, language=language)[0] == 'answered' for fact in selected):
                    state = 'answered'  # A documented mandatory call excludes an ordinary disabled-call mode.
            if (facet == 'disabled' and state == 'answered' and _disabled_call_output_coverage(selected,
                    opcode=opcode, model=model, language=language) is False):
                state = 'unresolved'
            states[facet] = state
            if state in {'answered', 'not_applicable'}:
                continue
            reasons = instruction_aspect_gap_reasons(packet, facet, state=state, model=model, language=language)
            status = ('source_conflict_pending' if 'source_conflict' in reasons else
                      'source_review_pending' if state in {'source_review_pending', 'unverified', 'unresolved'}
                      else 'content_missing')
            remaining.append({'facet': facet, 'reason': ('正式来源未绑定到明确接口' if interface == 'source_interface_review'
                else '参数类型或独立占用仍不完整' if facet in {'types', 'storage'}
                else '没有完整输入、结果或状态关系' if facet == 'effects'
                else '实际事实及其依赖未完整回答此项'), 'status': status, 'gap_reasons': reasons})
        conflicts = [fact for fact in selected if fact.get('status') == 'conflict']
        duplicate_conflicts = (duplicate_variant_conflict_ids(facts)
                               | superseded_connection_conflict_ids(facts))
        if duplicate_conflicts:
            conflicts = [fact for fact in conflicts if fact['id'] not in duplicate_conflicts]
        if conflicts:
            remaining.append({'facet': 'source_conflicts', 'reason': '保留的自身来源声明存在明确矛盾，需逐项判读',
                'status': 'source_conflict_pending', 'fact_ids': [fact['id'] for fact in conflicts]})
        evidence = [fact for fact in selected if str(fact.get('dimension', '')).split('.', 1)[0] in
                    {'parameters', 'effects', 'execution', 'constraints', 'memory', 'resources'}]
        rows.append({**case, 'vendor': entry.get('vendor', 'mitsubishi'), 'base_opcode': base,
            'opcode': opcode, 'target_model': model, 'states': states, 'remaining': remaining,
            'excluded': excluded, 'evidence_refs': _references(evidence),
            'limitations': [{**_references([fact])[0], 'value': copy.deepcopy(fact['value'])}
                for fact in limitations if fact['id'] in {row['id'] for row in selected}]})
    def unresolved_cpu(row):
        return any(condition.get('context') == 'cpu_version_not_resolved'
                   for condition in row.get('conditions', ()))
    def case_key(row):
        return (row.get('interface'), row.get('language'), row.get('declaration_kind'),
                row.get('instruction_variant'))
    resolved = {case_key(row) for row in rows if not unresolved_cpu(row) and not row.get('remaining')}
    rows = [row for row in rows if not unresolved_cpu(row) or case_key(row) not in resolved]
    return rows, native_evidence(entry, facts, base)


def native_evidence(entry, facts, base):
    native = [fact for fact in facts if ('native_ladder_instruction', 'ladder') in _interface(fact)]
    signatures = []
    fields = ('name', 'type', 'data_type', 'direction', 'type_completeness', 'passing', 'range',
              'word_device_layout', 'occupied_bit_count', 'enable_pin', 'role', 'array', 'element_type', 'components')
    for fact in native:
        if fact['dimension'] != 'parameters.typed_operands':
            continue
        value = fact.get('value') or {}
        operands = value.get('operands')
        valid = isinstance(operands, list)
        closure = _closure(facts, fact['id'], opcode=entry['opcode'], model=entry['target_model'], language='ladder')[0]
        parameters = [{key: copy.deepcopy(parameter[key]) for key in fields if key in parameter}
                      for parameter in operands] if valid else []
        signatures.append({'fact_id': fact['id'], 'status': fact.get('status'),
            'source_verified': closure == 'answered', 'representation': value.get('representation'),
            'declaration_kind': value.get('declaration_kind'), 'operand_count': value.get('operand_count'),
            'count': value.get('operand_count'),
            'order': [parameter.get('name') for parameter in parameters], 'operands': parameters,
            'type_complete': valid and all(_type_present(parameter, 'native_ladder_instruction') for parameter in operands),
            'conditions': copy.deepcopy(fact.get('conditions', [])), 'source_refs': _references([fact])})
    order_ids = [fact['id'] for fact in facts if fact['dimension'] in {'parameters.order', 'parameters.source_order'}
                 and ('native_ladder_instruction', 'ladder') in _interface(fact)]
    arity_ids = [fact['id'] for fact in facts if fact['dimension'] == 'parameters.arity'
                 and ('native_ladder_instruction', 'ladder') in _interface(fact)]
    return {'vendor': entry.get('vendor', 'mitsubishi'), 'opcode': entry['opcode'],
        'target_model': entry['target_model'], 'base_opcode': base, 'call_available': entry.get('call_available'),
        'native': {'declared': bool(native), 'typed_fact_ids': [row['fact_id'] for row in signatures],
            'order_fact_ids': order_ids, 'arity_fact_ids': arity_ids,
            'source_verified': any(row['source_verified'] for row in signatures),
            'partial': bool(signatures) and not any(row['type_complete'] and row['source_verified'] for row in signatures),
            'signatures': signatures}}


def aggregate_units(rows, *, units=None, finalize=True):
    units = {} if units is None else units
    for row in rows:
        descriptor = [row['vendor'], row['base_opcode'], row['interface'], row.get('declaration_kind', ''),
                      row.get('instruction_variant', ''), row.get('conditions', [])]
        key = canonical(descriptor)
        unit = units.setdefault(key, {'id': 'instruction|' + '|'.join(quote(canonical(item), safe='') for item in descriptor),
            'domain': 'instruction', 'vendor': row['vendor'], 'base_opcode': row['base_opcode'],
            'interface': row['interface'], 'declaration_kind': row.get('declaration_kind', ''),
            'instruction_variant': row.get('instruction_variant', ''), 'conditions': row.get('conditions', []),
            'models': [], 'forms': [], 'remaining': [], 'limitations': [], 'evidence_refs': [],
            '_remaining_index': {}, '_reference_index': {'evidence_refs': {}, 'limitations': {}},
            'delivery': {'status': 'not_rechecked_by_inventory', 'content_path': 'normal_compose_and_dependency_closure'}})
        for field, value in [('models', row['target_model']), ('forms', row['opcode'])]:
            if value not in unit[field]:
                unit[field].append(value)
        for missing in row['remaining']:
            marker = canonical(missing)
            previous = unit['_remaining_index'].get(marker)
            if previous is None:
                previous = {**copy.deepcopy(missing), '_key': marker, 'members': []}
                unit['remaining'].append(previous)
                unit['_remaining_index'][marker] = previous
            previous['members'].append({'model': row['target_model'], 'form': row['opcode']})
        for field in ['evidence_refs', 'limitations']:
            index = unit['_reference_index'][field]
            for reference in row.get(field, []):
                marker = canonical(reference)
                previous = index.get(marker)
                if previous is None:
                    previous = {**copy.deepcopy(reference), 'members': []}
                    unit[field].append(previous); index[marker] = previous
                member = {'model': row['target_model'], 'form': row['opcode']}
                if member not in previous['members']:
                    previous['members'].append(member)
    if not finalize:
        return units
    for unit in units.values():
        unit['models'].sort(); unit['forms'].sort()
        unit.pop('_remaining_index', None); unit.pop('_reference_index', None)
        for item in unit['remaining']:
            item.pop('_key', None)
    return sorted(units.values(), key=lambda unit: unit['id'])


def prepare_representatives(output):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.instruction_knowledge import compose_programming_facts
    output.mkdir(parents=True, exist_ok=True)
    store = registry._definition_owner_store
    names = {'MOV', 'ZCP', 'DZCPP', 'ANS', 'ANR', 'DSFR', '+', 'ADD', 'TTMR', 'STMR'}
    headers = [row for row in store.iter_owner_headers() if row['opcode'] in names
               and row['target_model'] in {'FX3U', 'Q03UDV'}]
    bindings = declared_family_bindings(registry, {row['target_model'] for row in headers})
    cache, results = [], []
    started = time.monotonic()
    for header in headers:
        entry = store.load_owner((header['vendor'], header['opcode'], header['target_model']))
        composed, _ = compose_programming_facts(registry, entry['opcode'], cpu=entry['target_model'], existing=entry.get('facts', []))
        rows, _ = inspect_owner(entry, registry, composed_facts=composed, family_bindings=bindings)
        cache.append({'entry': {key: entry[key] for key in ('vendor', 'opcode', 'target_model', 'call_available') if key in entry},
                      'composed_facts': composed})
        results.extend([{key: row[key] for key in ('base_opcode', 'opcode', 'target_model', 'interface',
                         'conditions', 'states', 'remaining')} for row in rows])
    seconds = time.monotonic() - started
    summary = {'fact_bundle_version': store.fact_bundle_version, 'manifest': str(store.manifest_path),
               'owners': len(headers), 'elapsed_seconds': round(seconds, 2),
               'seconds_per_owner': round(seconds / max(1, len(headers)), 3),
               'cache_reused_by_formal_traversal': True}
    (output / 'representative-owner-cache.json').write_text(json.dumps({'summary': summary, 'entries': cache},
        ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    (output / 'representatives.json').write_text(json.dumps({'summary': summary, 'units': results},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print(canonical({'stage': 'representatives_complete', **summary}), flush=True)
    return 0


def _candidate_universe(registry, headers, source_universe, models):
    candidates = {(row['vendor'], row['opcode'], row['target_model']): [] for row in headers}
    for opcode in registry.known_mnemonics():
        form = registry.resolve_catalog_form(opcode)
        if form is None:
            continue
        for model in models:
            if form.spec.cpu_support and model not in form.spec.cpu_support:
                continue
            candidates.setdefault(('mitsubishi', opcode, model), []).append({'origin': 'registry_candidate'})
    for source in source_universe.get('entries', []):
        reference = {key: source[key] for key in ('id', 'manual_id', 'manual_number', 'revision', 'page_start',
                                                  'page_end', 'opcode', 'variants') if key in source}
        for opcode in source.get('variants') or [source['opcode']]:
            for model in set(source.get('candidate_models') or ()).intersection(models):
                candidates.setdefault(('mitsubishi', str(opcode).strip().upper(), model), []).append(
                    {'origin': 'source_scope_candidate_not_support', **reference})
    for key in getattr(registry, '_cpu_contracts', {}):
        if len(key) == 3 and key[2] in models:
            candidates.setdefault(key, []).append({'origin': 'scoped_registry_call_table_candidate'})
    return candidates


def _inspection_key(facts, entry, base):
    """Reuse only equal composed graphs; exact CPU conditions remain in the key."""
    model = entry['target_model']
    normalized = []
    for original in facts:
        fact = {**original, 'scope': dict(original.get('scope') or {}),
                'conditions': conditions_for_model(original.get('conditions', []), model)}
        fact['_inventory_model_scope'] = not fact['scope'].get('models') or model in fact['scope']['models']
        if fact['scope'].get('models') == [model]:
            fact['scope']['models'] = ['<owner-model>']
        if fact.get('dimension') in {'constraints.cpu_availability', 'identity.applicability'}:
            value = fact.get('value') or {}
            fact['_inventory_model_state'] = value.get('model_states', {}).get(model)
            if list(value.get('model_states', {})) == [model]:
                fact['value'] = {**value, 'model_states': {'<owner-model>': value['model_states'][model]}}
        normalized.append(fact)
    return canonical([entry['vendor'], entry['opcode'], base, entry.get('call_available'), normalized])


def run_inventory(output, source_universe_path, *, partition_index=None, partition_count=1):
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    from plc.instruction_knowledge import compose_programming_facts
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    store = registry._definition_owner_store
    if store is None:
        raise ValueError('An installed owner manifest is required')
    all_headers = list(store.iter_owner_headers())
    eligible = [row for row in all_headers if row['target_model'] not in EXCLUDED_MODELS]
    models = sorted({row['target_model'] for row in eligible})
    start = len(eligible) * (partition_index or 0) // partition_count
    end = len(eligible) * ((partition_index or 0) + 1) // partition_count
    headers = eligible[start:end] if partition_index is not None else eligible
    source_universe = json.loads(source_universe_path.read_text(encoding='utf-8'))
    candidates = _candidate_universe(registry, eligible, source_universe, models) if partition_index is None else {}
    bindings = declared_family_bindings(registry, models)
    bindings.update(source_family_bindings(source_universe))
    print(canonical({'stage': 'planned', 'version': store.fact_bundle_version, 'models': len(models),
                     'owners_to_hydrate_once': len(headers), 'union_candidates': len(candidates),
                     'partition_index': partition_index, 'partition_count': partition_count,
                     'owner_range': [start, end]}), flush=True)
    cached = {}
    cache_path = output / 'representative-owner-cache.json'
    if cache_path.is_file():
        cache = json.loads(cache_path.read_text(encoding='utf-8'))
        if (cache['summary']['fact_bundle_version'] != store.fact_bundle_version
                or cache['summary']['manifest'] != str(store.manifest_path)):
            raise ValueError('Representative inputs belong to a different installed bundle')
        cached = {(row['entry']['vendor'], row['entry']['opcode'], row['entry']['target_model']): row for row in cache['entries']}
    units, failures = {}, []
    assessment_stream = (output / f'assessment-rows-{partition_index}.jsonl').open('w', encoding='utf-8') if partition_index is not None else None
    native_count = 0
    native_path = output / ('native-evidence-index.json' if partition_index is None else f'native-evidence-index-{partition_index}.json')
    native_stream = native_path.open('w', encoding='utf-8')
    native_stream.write('{"summary":' + canonical({'fact_bundle_version': store.fact_bundle_version,
        'models': models, 'owners': len(headers), 'excluded_models': sorted(EXCLUDED_MODELS), 'hydrate_passes': 1}) + ',"owners":[')
    core = Counter()
    inspection_cache, previous_opcode, inspected_graphs = {}, None, 0
    for index, header in enumerate(headers, 1):
        key = header['vendor'], header['opcode'], header['target_model']
        try:
            if key in cached:
                entry = cached[key]['entry']
                composed = cached[key]['composed_facts']
            else:
                entry = store.load_owner(key)
                composed, _ = compose_programming_facts(registry, entry['opcode'], cpu=entry['target_model'],
                                                       existing=entry.get('facts', []))
            if previous_opcode != entry['opcode']:
                inspection_cache.clear(); previous_opcode = entry['opcode']
            marker = _inspection_key(composed, entry, _family(bindings, entry['opcode'], entry['target_model'], registry))
            if marker in inspection_cache:
                old_rows, old_evidence = inspection_cache[marker]
                inspected = [{**row, 'target_model': entry['target_model']} for row in old_rows]
                evidence = {**old_evidence, 'target_model': entry['target_model']}
            else:
                inspected, evidence = inspect_owner(entry, registry, family_bindings=bindings, composed_facts=composed)
                inspection_cache[marker] = inspected, evidence
                inspected_graphs += 1
            if assessment_stream is None:
                aggregate_units(inspected, units=units, finalize=False)
            else:
                for row in inspected:
                    assessment_stream.write(canonical(row) + '\n')
            if native_count:
                native_stream.write(',')
            native_stream.write(canonical(evidence)); native_count += 1
            core[str(entry.get('call_available'))] += 1
        except Exception as error:
            failures.append({'owner': list(key), 'error': str(error), 'error_type': type(error).__name__})
            failed_row = {'vendor': header['vendor'], 'base_opcode': _family(bindings, header['opcode'], header['target_model'], registry),
                'opcode': header['opcode'], 'target_model': header['target_model'],
                'interface': 'source_interface_review', 'conditions': [], 'evidence_refs': [], 'limitations': [],
                'remaining': [{'facet': 'owner', 'reason': str(error), 'status': 'content_or_dependency_invalid'}]}
            if assessment_stream is None:
                aggregate_units([failed_row], units=units, finalize=False)
            else:
                assessment_stream.write(canonical(failed_row) + '\n')
        if index % 1000 == 0:
            print(canonical({'stage': 'traversing', 'owners': index, 'total': len(headers),
                'elapsed_seconds': round(time.monotonic() - started, 1), 'distinct_composed_graphs_inspected': inspected_graphs,
                'failures': len(failures)}), flush=True)
    if partition_index is not None:
        assessment_stream.close(); native_stream.write(']}'); native_stream.close()
        summary = {'partition_index': partition_index, 'partition_count': partition_count, 'owner_range': [start, end],
            'fact_bundle_version': store.fact_bundle_version, 'manifest': str(store.manifest_path),
            'models': models, 'owners': len(headers), 'eligible_owners': len(eligible), 'native_index_owners': native_count,
            'owner_failures': failures, 'core_call_available_separate': dict(core),
            'representative_owners_reused': sum(key in cached for key in
                ((header['vendor'], header['opcode'], header['target_model']) for header in headers)),
            'distinct_composed_graphs_inspected': inspected_graphs,
            'elapsed_seconds': round(time.monotonic() - started, 1)}
        (output / f'partition-{partition_index}-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(canonical({'stage': 'partition_complete', **summary}), flush=True)
        return 1 if failures else 0
    present = {(row['vendor'], row['opcode'], row['target_model']) for row in headers}
    missing_owners = sorted(set(candidates) - present)
    for vendor, opcode, model in missing_owners:
        aggregate_units([{'vendor': vendor, 'base_opcode': _family(bindings, opcode, model, registry),
            'opcode': opcode, 'target_model': model, 'interface': 'source_interface_review', 'conditions': [],
            'remaining': [{'facet': 'owner', 'reason': '目录或调用表中的候选尚无正式owner，需核对自身型号及接口来源',
                           'status': 'source_review_pending'}],
            'evidence_refs': [{'source_candidate': source} for source in candidates[vendor, opcode, model]], 'limitations': []}],
            units=units, finalize=False)
    units = aggregate_units([], units=units)
    summary = {'rule_version': RULE_VERSION, 'manifest': str(store.manifest_path),
        'database': str(store.database_path), 'fact_bundle_version': store.fact_bundle_version,
        'models': models, 'model_count': len(models), 'installed_owners': len(headers),
        'excluded_models': sorted(EXCLUDED_MODELS), 'excluded_owners': len(all_headers) - len(headers),
        'source_candidate_entries': len(source_universe.get('entries', [])), 'union_candidate_owners': len(candidates),
        'missing_candidate_owners': len(missing_owners), 'base_families': len({row['base_opcode'] for row in units}),
        'units': len(units), 'units_with_remaining': sum(bool(row['remaining']) for row in units),
        'remaining_by_facet': dict(Counter(item['facet'] for row in units for item in row['remaining'])),
        'remaining_by_status': dict(Counter(item['status'] for row in units for item in row['remaining'])),
        'conditional_units': sum(bool(row['conditions']) for row in units),
        'limitations': sum(len(row['limitations']) for row in units),
        'core_call_available_separate': dict(core), 'owner_failures': failures,
        'hydrate_passes': 1, 'compose_calls': len(headers) - len(failures),
        'representative_owners_reused': len(cached), 'native_index_owners': native_count,
        'distinct_composed_graphs_inspected': inspected_graphs,
        'elapsed_seconds': round(time.monotonic() - started, 1),
        'knowledge_does_not_certify_core_execution': True, 'compiled_or_installed': False,
        'delivery_scope': 'normal composition and content dependencies; no new MCP/model/native run'}
    (output / 'instruction-semantics-inventory.json').write_text(json.dumps({'summary': summary, 'units': units},
        ensure_ascii=False, indent=2), encoding='utf-8')
    native_stream.write(']}'); native_stream.close()
    print(canonical({'stage': 'complete', **summary}), flush=True)
    return 1 if failures else 0


def merge_partitions(output, source_universe_path, partition_count):
    """Merge saved assessments only; never hydrate or compose another owner."""
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY as registry
    started = time.monotonic()
    summaries = [json.loads((output / f'partition-{index}-summary.json').read_text(encoding='utf-8'))
                 for index in range(partition_count)]
    store = registry._definition_owner_store
    all_headers = list(store.iter_owner_headers())
    headers = [row for row in all_headers if row['target_model'] not in EXCLUDED_MODELS]
    models = sorted({row['target_model'] for row in headers})
    expected_start = 0
    for index, part in enumerate(summaries):
        if (part['partition_index'] != index or part['partition_count'] != partition_count
                or part['fact_bundle_version'] != store.fact_bundle_version or part['manifest'] != str(store.manifest_path)
                or part['owner_range'][0] != expected_start or part['owners'] != part['owner_range'][1] - expected_start):
            raise ValueError('Partition scope, version or range mismatch')
        expected_start = part['owner_range'][1]
    if expected_start != len(headers):
        raise ValueError('Partitions do not cover the installed GXWorks2 owner range')
    universe = json.loads(source_universe_path.read_text(encoding='utf-8'))
    bindings = declared_family_bindings(registry, models); bindings.update(source_family_bindings(universe))
    units, assessment_count = {}, 0
    for index in range(partition_count):
        with (output / f'assessment-rows-{index}.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                aggregate_units([json.loads(line)], units=units, finalize=False); assessment_count += 1
    candidates = _candidate_universe(registry, headers, universe, models)
    present = {(row['vendor'], row['opcode'], row['target_model']) for row in headers}
    missing_owners = sorted(set(candidates) - present)
    for vendor, opcode, model in missing_owners:
        aggregate_units([{'vendor': vendor, 'base_opcode': _family(bindings, opcode, model, registry),
            'opcode': opcode, 'target_model': model, 'interface': 'source_interface_review', 'conditions': [],
            'remaining': [{'facet': 'owner', 'reason': '目录或调用表中的候选尚无正式owner，需核对自身型号及接口来源',
                           'status': 'source_review_pending'}],
            'evidence_refs': [{'source_candidate': source} for source in candidates[vendor, opcode, model]], 'limitations': []}],
            units=units, finalize=False)
    units = aggregate_units([], units=units)
    native_count = 0
    with (output / 'native-evidence-index.json').open('w', encoding='utf-8') as stream:
        stream.write('{"summary":' + canonical({'fact_bundle_version': store.fact_bundle_version, 'models': models,
            'owners': len(headers), 'excluded_models': sorted(EXCLUDED_MODELS), 'formal_partition_owner_overlap': 0}) + ',"owners":[')
        for index in range(partition_count):
            part = json.loads((output / f'native-evidence-index-{index}.json').read_text(encoding='utf-8'))
            for owner in part['owners']:
                if native_count: stream.write(',')
                stream.write(canonical(owner)); native_count += 1
        stream.write(']}')
    failures = [failure for part in summaries for failure in part['owner_failures']]
    core = Counter()
    for part in summaries: core.update(part['core_call_available_separate'])
    calibration_path = output / 'performance-calibration.json'
    calibration = json.loads(calibration_path.read_text(encoding='utf-8')) if calibration_path.is_file() else None
    summary = {'rule_version': RULE_VERSION, 'manifest': str(store.manifest_path), 'database': str(store.database_path),
        'fact_bundle_version': store.fact_bundle_version, 'models': models, 'model_count': len(models),
        'installed_owners': len(headers), 'excluded_models': sorted(EXCLUDED_MODELS),
        'excluded_owners': len(all_headers) - len(headers), 'source_candidate_entries': len(universe.get('entries', [])),
        'union_candidate_owners': len(candidates), 'missing_candidate_owners': len(missing_owners),
        'base_families': len({unit['base_opcode'] for unit in units}), 'units': len(units),
        'units_with_remaining': sum(bool(unit['remaining']) for unit in units),
        'remaining_by_facet': dict(Counter(item['facet'] for unit in units for item in unit['remaining'])),
        'remaining_by_status': dict(Counter(item['status'] for unit in units for item in unit['remaining'])),
        'conditional_units': sum(bool(unit['conditions']) for unit in units),
        'limitations': sum(len(unit['limitations']) for unit in units), 'owner_failures': failures,
        'core_call_available_separate': dict(core), 'native_index_owners': native_count,
        'formal_partition_owner_overlap': 0, 'partitions': summaries, 'performance_calibration': calibration,
        'assessment_rows': assessment_count, 'merge_owner_hydrations': 0, 'merge_compose_calls': 0,
        'knowledge_does_not_certify_core_execution': True, 'compiled_or_installed': False,
        'elapsed_merge_seconds': round(time.monotonic() - started, 1),
        'delivery_scope': 'normal composition and content dependencies; no new MCP/model/native run'}
    (output / 'instruction-semantics-inventory.json').write_text(json.dumps({'summary': summary, 'units': units},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print(canonical({'stage': 'complete', **summary}), flush=True)
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-universe', type=Path, required=True)
    parser.add_argument('--representatives-only', action='store_true',
                        help='Prepare a small reusable owner cache before the one complete traversal')
    parser.add_argument('--partition-index', type=int)
    parser.add_argument('--partition-count', type=int, default=1)
    parser.add_argument('--merge-partitions', type=int)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / 'src'), str(root)]
    if args.partition_count < 1 or args.partition_index is not None and not 0 <= args.partition_index < args.partition_count:
        parser.error('Partition index must be within the positive partition count')
    if args.merge_partitions:
        return merge_partitions(args.output, args.source_universe, args.merge_partitions)
    return prepare_representatives(args.output) if args.representatives_only else run_inventory(args.output,
        args.source_universe, partition_index=args.partition_index, partition_count=args.partition_count)


if __name__ == '__main__':
    raise SystemExit(main())
