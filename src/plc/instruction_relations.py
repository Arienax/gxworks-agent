"""Bind authored relation vocabularies to their own operand declarations.

This is knowledge compilation, not an instruction executor. Relationships must
come from a source clause and its declaration; an opcode is never a rule key.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping


DERIVED_PREFIXES = ('assembly:parameters:', 'assembly:relation:', 'assembly:interface:', 'assembly:call:', 'assembly:definition:', 'modifier:execution')
_STRENGTH = ('conflict', 'unknown', 'not_applicable', 'declared_unverified', 'candidate_evidence', 'source_verified')
_GATES = {'enable', 'enable_condition', 'execution_condition', 'execution_status'}
_UNARY = {
    'absolute_value', 'negation', 'bitwise_not', 'value_mapping',
    'sign_extended_to_target_width', 'value_preserved_in_target_range',
    'bit_pattern_transferred', 'millisecond_count_transferred', 'low_order_bits_retained',
    'decimal_text', 'text_to_integer', 'bcd_to_binary', 'binary_to_bcd',
    'exact_integer_to_single', 'integer_to_single', 'fraction_rounded_off', 'exponent_text',
    'evaluate_acos', 'evaluate_asin', 'evaluate_atan', 'evaluate_cos', 'evaluate_sin',
    'evaluate_tan', 'evaluate_sqrt', 'evaluate_log', 'evaluate_exp',
}
_FOLDS = {'addition', 'multiplication', 'bitwise_and', 'bitwise_or', 'bitwise_xor',
          'comparison_chain', 'comparison_pair', 'minimum', 'maximum'}
_BINARY = {'subtraction', 'division', 'remainder', 'exponentiation'}
_PRIMARY = {'effects.operation', 'effects.dataflow', 'effects.transfer', 'effects.result_rule', 'effects.math_function',
            'effects.conversion', 'effects.rotation', 'effects.documented_operation', 'effects.documented_processing',
            'effects.compare_outputs', 'effects.zone_compare'}


def fact_context_intersection(left, right):
    """Intersect scopes, equalities and exact complementary predicates."""
    from plc.instruction_definition import DefinitionError, Expression, canonical_expression

    positive, negative = set(), set()

    def record(expression):
        if expression.op == 'not':
            negative.add(repr(canonical_expression(expression.args[0])))
        else:
            positive.add(repr(canonical_expression(expression)))
            if expression.op == 'and':
                for argument in expression.args:
                    record(argument)

    scope, conditions = {}, []
    for key in set(left.get('scope', {})) | set(right.get('scope', {})):
        a, b = left.get('scope', {}).get(key), right.get('scope', {}).get(key)
        if a and b:
            if key not in {'models', 'forms', 'languages', 'versions'}:
                if a != b:
                    return None
                scope[key] = copy.deepcopy(a)
            else:
                scope[key] = [value for value in a if value in b]
                if not scope[key]:
                    return None
        else:
            scope[key] = copy.deepcopy(a or b)
    assignments = {}
    for fact in (left, right):
        for condition in fact.get('conditions', []):
            if condition not in conditions:
                conditions.append(copy.deepcopy(condition))
            if not isinstance(condition, Mapping):
                continue
            if isinstance(condition.get('predicate'), Mapping):
                try:
                    expression = Expression.from_mapping(condition['predicate'])
                except (DefinitionError, TypeError, ValueError, KeyError):
                    pass
                else:
                    if expression.value_type.kind == 'bool':
                        record(expression)
                        if positive.intersection(negative):
                            return None
            if set(condition) == {'context', 'equals'}:
                path = (condition['context'],)
            elif (set(condition) == {'context', 'parameter', 'equals'}
                  and condition['context'] == 'operand_value'
                  and isinstance(condition['parameter'], str)):
                path = ('operand_value', condition['parameter'].casefold())
            else:
                continue
            value = condition['equals']
            if path in assignments and (type(assignments[path]) is not type(value) or assignments[path] != value):
                return None
            assignments[path] = value
    return scope, conditions


def same_relation_interface(effect, signature):
    """An overload qualifier belongs to its own interface, not its namesake."""
    left, right = effect.get('value', {}), signature.get('value', {})
    if left.get('instruction_variant') != right.get('instruction_variant'):
        return False
    a, b = left.get('declaration_kind'), right.get('declaration_kind')
    return not (a and b and a != b)


def _source_related(a, b):
    if a['id'] in b.get('depends_on', []) or b['id'] in a.get('depends_on', []):
        return True
    for first in a.get('sources', []):
        for second in b.get('sources', []):
            if first.get('manual_id') and first.get('manual_id') == second.get('manual_id'):
                if first.get('revision') != second.get('revision'):
                    continue
                pages = lambda source: set(source.get('pdf_pages') or [source.get('pdf_page')]) - {None}
                if pages(first).intersection(pages(second)):
                    return True
            elif first.get('source_id') and first.get('source_id') == second.get('source_id'):
                return True
    return False




def normalize_declared_form_labels(facts):
    """Remove an old suffix heuristic from identity; real timing stays separate."""
    result, originals = [], []
    for original in facts:
        fact = original
        if (original['id'].startswith('authored:ql:form:') and original.get('dimension') == 'identity.declared_form'
                and original.get('value', {}).get('form_variant') in {'base', 'pulse', 'enable_aware'}):
            fact = copy.deepcopy(original)
            fact['value'].pop('form_variant')
            originals.append(copy.deepcopy(original))
        result.append(fact)
    return result, originals


def normalize_native_interface_facts(facts):
    """Qualify the native operand contract emitted by the legacy compiler.

    A language-neutral registry row was never an ST/FBD parameter declaration.
    The original remains in the owner's debug history; callable metadata is not
    changed by this source-interface correction.
    """
    native_dimensions = {'identity.form', 'identity.applicability', 'parameters.arity', 'parameters.order',
        'parameters.roles', 'parameters.types', 'parameters.devices', 'parameters.width', 'execution.form',
        'resources.completion', 'resources.pulse_output', 'constraints.numeric_ranges', 'memory.disjoint_ranges'}
    native_contracts = {fact['id'] for fact in facts if fact['id'].startswith('registry:')
                        and fact.get('dimension') in native_dimensions}
    result, originals = [], []
    for original in facts:
        fact = original
        core_native_binding = (isinstance(original.get('value'), Mapping) and original['value'].get('behavior')
                               and native_contracts.intersection(original.get('depends_on', [])))
        if ((original['id'].startswith('registry:') and original.get('dimension') in native_dimensions
             or core_native_binding
             or original['id'].startswith(('native:', 'operand:', 'chart:'))
             or isinstance(original.get('value', {}).get('native_form'), Mapping)
             or original.get('value', {}).get('representation') == 'native_ladder_instruction')
                and original.get('scope', {}).get('languages') in (None, [], ['ladder'])):
            fact = copy.deepcopy(original)
            fact.setdefault('scope', {})['languages'] = ['ladder']
            condition = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
            if condition not in fact.get('conditions', []):
                fact.setdefault('conditions', []).append(condition)
            if fact != original:
                originals.append(copy.deepcopy(original))
        result.append(fact)
    return result, originals


def normalize_interface_parameters(facts):
    """Restore explicit enable arguments omitted by an older table compiler.

    The language signature is the parent of its generated operand table. Only
    its own declared Boolean gate pins may complete that table; data arguments,
    return values and unrelated same-page declarations are never invented.
    """
    calls = {}
    for fact in facts:
        if (fact.get('dimension') not in {'parameters.st_signature', 'parameters.fbd_signature', 'parameters.ladder_signature'}
                or fact.get('verification') != 'source_checked'
                or fact['value'].get('available') is False):
            continue
        calls[fact['id'] + ':operands'] = fact
        for model in fact.get('scope', {}).get('models', []):
            if fact['id'].endswith(':' + model):
                calls[fact['id'][:-len(model) - 1] + ':operands:' + model] = fact
    result, replaced = [], []
    for original in facts:
        call = calls.get(original.get('id'))
        corrected = original
        if call and original.get('dimension') == 'parameters.typed_operands':
            declared = call['value'].get('parameters', [])
            stored = original.get('value', {}).get('operands', [])
            data = [p for p in declared if p.get('name') not in {'EN', 'ENO'}]
            gates = [p for p in declared if p.get('name') in {'EN', 'ENO'}]
            context = fact_context_intersection(original, call)
            if (context is not None and original.get('verification') == 'source_checked'
                    and [p.get('name') for p in stored] == [p.get('name') for p in data]
                    and len(gates) == 2 and {p.get('name') for p in gates} == {'EN', 'ENO'}
                    and all(p.get('type', p.get('data_type')) in ('BOOL', {'kind': 'bool'})
                            and p.get('direction') == ('input' if p['name'] == 'EN' else 'output') for p in gates)
                    and all(a.get('type', a.get('data_type')) == b.get('type', b.get('data_type'))
                            and a.get('direction') == b.get('direction') for a, b in zip(stored, data))):
                corrected = copy.deepcopy(original)
                by_name = {p['name']: p for p in stored}
                operands = []
                for parameter in declared:
                    item = copy.deepcopy(by_name.get(parameter['name'], parameter))
                    if item['name'] in {'EN', 'ENO'}:
                        item.update(enable_pin=True, role='enable_condition' if item['name'] == 'EN' else 'execution_status')
                    operands.append(item)
                corrected['value'].update(operands=operands, operand_count=len(operands))
                corrected['scope'], corrected['conditions'] = context
                corrected['status'] = next((s for s in _STRENGTH if s in {original.get('status'), call.get('status')}), 'unknown')
                corrected['depends_on'] = list(dict.fromkeys([*original.get('depends_on', []), call['id']]))
                corrected.setdefault('sources', []).extend(copy.deepcopy([s for s in call.get('sources', [])
                    if s not in corrected.get('sources', [])]))
                replaced.append(copy.deepcopy(original))
            group = call['value'].get('variadic_parameters')
            current = corrected.get('value', {}).get('operands', [])
            if (group and context is not None and original.get('verification') == 'source_checked'
                    and same_relation_interface(call, original)
                    and len(current) == len(declared)
                    and all(a.get('name') == b.get('name') and a.get('type') == b.get('type')
                            and a.get('direction') == b.get('direction') for a, b in zip(current, declared))
                    and corrected['value'].get('variadic_parameters') != group):
                if corrected is original:
                    corrected = copy.deepcopy(original)
                    replaced.append(copy.deepcopy(original))
                corrected['value']['variadic_parameters'] = copy.deepcopy(group)
                corrected['scope'], corrected['conditions'] = context
                corrected['depends_on'] = list(dict.fromkeys([*corrected.get('depends_on', []), call['id']]))
        result.append(corrected)
    return result, replaced


def interface_relation_facts(facts):
    """Use an explicit shared FBD/structured-Ladder declaration, never native."""
    bridges = [f for f in facts if f.get('dimension') == 'parameters.ladder_signature'
               and f.get('verification') == 'source_checked' and f['value'].get('available') is True
               and {'field': 'project_mode', 'equals': 'structured_ladder_block'} in
                   f['value'].get('required_context', [])]
    fbd = [f for f in facts if f.get('dimension') == 'parameters.fbd_signature'
           and f.get('verification') == 'source_checked' and f['value'].get('available') is True]
    result = []
    for effect in facts:
        if (effect.get('dimension') not in _PRIMARY or effect['id'].startswith(DERIVED_PREFIXES)
                or effect.get('verification') != 'source_checked'
                or 'fbd' not in effect.get('scope', {}).get('languages', [])
                or 'ladder' in effect.get('scope', {}).get('languages', [])):
            continue
        for bridge in bridges:
            if not isinstance(bridge['value'].get('pins'), list) or not bridge['value']['pins']:
                continue
            counterpart = next((f for f in fbd if f['value'].get('name') == bridge['value'].get('name')
                and f['value'].get('pins') == bridge['value'].get('pins')
                and _source_related(f, bridge) and _source_related(effect, f)), None)
            if counterpart is None or not _source_related(effect, bridge):
                continue
            context = fact_context_intersection({**effect, 'scope': {**effect['scope'], 'languages': ['ladder']}}, bridge)
            if context is None:
                continue
            scope, conditions = context
            mode = {'context': 'project_mode', 'equals': 'structured_ladder_block'}
            context = fact_context_intersection({'scope': scope, 'conditions': conditions}, {'conditions': [mode]})
            if context is None:
                continue
            _, conditions = context
            # Existing source-authored aliases take precedence over this adapter.
            if any(f.get('dimension') == effect['dimension'] and f.get('value') == effect['value']
                   and f.get('scope') == scope and f.get('conditions') == conditions for f in facts):
                continue
            prerequisites = [*effect.get('depends_on', []), bridge['id']]
            by_id = {f['id']: f for f in facts}
            if any(p not in by_id or by_id[p].get('scope', {}).get('languages')
                   and 'ladder' not in by_id[p]['scope']['languages'] for p in prerequisites):
                continue
            statuses = {f.get('status') for f in (effect, bridge, counterpart)}
            result.append({'id': 'assembly:interface:' + bridge['id'] + ':' + effect['id'],
                'dimension': effect['dimension'], 'value': copy.deepcopy(effect['value']), 'scope': scope,
                'conditions': conditions, 'status': next((s for s in _STRENGTH if s in statuses), 'unknown'),
                'verification': 'source_checked', 'depends_on': list(dict.fromkeys(prerequisites)),
                'sources': [*copy.deepcopy(effect.get('sources', [])), *copy.deepcopy(bridge.get('sources', [])),
                    {'source_fact_id': effect['id'], 'binding': 'documented_shared_block_declaration'}]})
    return result


def _gate_declaration(signature, facts):
    """Use source-declared EN/ENO roles, rather than counting them as data."""
    for gate in facts:
        value = gate.get('value', {})
        if (gate.get('dimension') not in {'execution.enable', 'execution.trigger'}
                or gate.get('verification') != 'source_checked' or not _source_related(signature, gate)):
            continue
        enable, status = value.get('enable_argument'), value.get('status_argument')
        if value.get('has_EN_ENO') is True:
            enable, status = 'EN', 'ENO'
        if not isinstance(enable, str) or not isinstance(status, str):
            continue
        parameters = {p.get('name'): p for p in signature['value'].get('operands', [])}
        if (enable not in parameters or status not in parameters
                or parameters[enable].get('direction') != 'input'
                or parameters[status].get('direction') not in {'output', 'output_variable'}):
            continue
        def boolean(parameter):
            return parameter.get('type', parameter.get('data_type')) in ('BOOL', {'kind': 'bool'})
        if not boolean(parameters[enable]) or not boolean(parameters[status]):
            continue
        if parameters[enable].get('enable_pin') and parameters[status].get('enable_pin'):
            return signature
        context = fact_context_intersection(signature, gate)
        if context is None:
            continue
        bound = copy.deepcopy(signature)
        bound['scope'], bound['conditions'] = context
        for parameter in bound['value']['operands']:
            if parameter['name'] in {enable, status}:
                parameter['enable_pin'] = True
        bound.setdefault('sources', []).extend(copy.deepcopy(gate.get('sources', [])))
        bound['status'] = next((s for s in _STRENGTH if s in {signature.get('status'), gate.get('status')}), 'unknown')
        bound['_binding_dependencies'] = [gate['id']]
        return bound
    return signature


def _return_declaration(signature, facts):
    """Bind a documented ST return without inventing an output argument."""
    if signature.get('scope', {}).get('languages') != ['st']:
        return signature
    parsed = _operands(signature)
    if not parsed or parsed[1]:
        return signature
    for call in facts:
        value = call.get('value', {})
        returned = value.get('returns')
        if (call.get('dimension') != 'parameters.st_signature'
                or value.get('available') is False or call.get('verification') != 'source_checked'
                or not isinstance(returned, Mapping) or not returned.get('name') or not returned.get('type')
                or not _source_related(signature, call)):
            continue
        if (returned.get('role') in _GATES or any(p.get('name') == returned['name'] and p.get('enable_pin')
                and p.get('direction') in {'output', 'output_variable'} for p in signature['value']['operands'])):
            continue
        names = [p.get('name') for p in signature['value']['operands']]
        if names != [p.get('name') for p in value.get('parameters', [])]:
            continue
        context = fact_context_intersection(signature, call)
        if context is None:
            continue
        bound = copy.deepcopy(signature)
        bound['scope'], bound['conditions'] = context
        bound['value']['operands'].append({**copy.deepcopy(returned), 'direction': 'output', 'returned_value': True})
        bound['value']['return_shape'] = {'operation_result_via': 'return_value', 'returned_by_st_call': True}
        bound['sources'].extend(copy.deepcopy(call.get('sources', [])))
        bound['status'] = next((s for s in _STRENGTH if s in {signature.get('status'), call.get('status')}), 'unknown')
        bound['_binding_dependencies'] = [*signature.get('_binding_dependencies', []), call['id']]
        return bound
    return signature


def _operands(signature):
    value = signature.get('value', {})
    if not isinstance(value, Mapping):
        return None
    operands = value.get('operands')
    if not isinstance(operands, list):
        return None
    if not operands:
        # A printed Native instruction may have no data operands.  Missing or
        # contradictory declarations must not become an empty shared interface.
        if (value.get('declaration_kind') != 'instruction'
                or value.get('representation') != 'native_ladder_instruction'
                or type(value.get('operand_count')) is not int or value['operand_count'] != 0):
            return None
        if 'arity' in value:
            arity = value['arity']
            if (not isinstance(arity, Mapping)
                    or set(arity) not in ({'min', 'max'}, {'minimum', 'maximum'})
                    or any(type(count) is not int or count != 0 for count in arity.values())):
                return None
        if 'native_form' in value:
            native = value['native_form']
            if (not isinstance(native, Mapping)
                    or 'operand_order' in native and native['operand_order'] != []
                    or any(type(native[key]) is not int or native[key] != 0
                           for key in ('minimum_operands', 'maximum_operands') if key in native)):
                return None
        return [], [], {}
    inputs, outputs, aliases = [], [], {}
    for operand in operands:
        name = operand.get('name')
        if not isinstance(name, str) or not name:
            return None
        if operand.get('enable_pin') or operand.get('role') in _GATES:
            continue
        direction = operand.get('direction')
        if direction not in {'input', 'output', 'output_variable', 'in_out'}:
            return None
        if direction in {'input', 'in_out'}:
            inputs.append(operand)
        if direction in {'output', 'output_variable', 'in_out'}:
            outputs.append(operand)
        for token in (name, operand.get('symbol'), operand.get('marker')):
            if not isinstance(token, str) or not token:
                continue
            token = token.upper()
            if token in aliases and aliases[token] != name:
                return None
            aliases[token] = name
    return inputs, outputs, aliases


def normalize_precision_claims(facts):
    """Remove an old unconditional exactness claim for wider integer inputs.

    This source vocabulary correction preserves the original record for debug;
    it does not supply an undocumented rounding algorithm or promote a status.
    """
    signatures = [f for f in facts if f.get('dimension') == 'parameters.typed_operands'
                  and f.get('verification') == 'source_checked' and not f['id'].startswith(DERIVED_PREFIXES)]
    result, replaced = [], []
    for fact in facts:
        value = fact.get('value', {})
        corrected = fact
        if (fact.get('dimension') == 'effects.result_rule' and value.get('rule') == 'exact_integer_to_single'
                and value.get('significant_digits') == 7 and not fact['id'].startswith(DERIVED_PREFIXES)):
            for signature in signatures:
                operands = _operands(signature)
                if (not operands or len(operands[0]) != 1 or len(operands[1]) != 1
                        or not _source_related(fact, signature) or fact_context_intersection(fact, signature) is None):
                    continue
                source = operands[0][0].get('type', operands[0][0].get('data_type'))
                target = operands[1][0].get('type', operands[1][0].get('data_type'))
                if (isinstance(source, Mapping) and source.get('kind') == 'int' and source.get('bits', 0) > 24
                        and isinstance(target, Mapping) and target.get('kind') == 'real' and target.get('bits') == 32):
                    corrected = copy.deepcopy(fact)
                    corrected['value'].update(rule='integer_to_single', exactness='only_when_input_is_representable')
                    corrected['value'].setdefault('unresolved_items', []).append('integer_to_real_rounding_mode')
                    replaced.append(copy.deepcopy(fact))
                    break
        result.append(corrected)
    return result, replaced


def _names(value, aliases):
    values = value if isinstance(value, list) else [value]
    result = []
    for token in values:
        if not isinstance(token, str):
            return None
        if token.upper() in aliases:
            result.append(aliases[token.upper()])
            continue
        sequence = re.fullmatch(r'([A-Za-z_]+)(\d+)\.\.([A-Za-z_]+)(\d+)', token)
        if not sequence or sequence[1].casefold() != sequence[3].casefold():
            return None
        first, last = int(sequence[2]), int(sequence[4])
        if not 0 <= first <= last <= 256:
            return None
        for position in range(first, last + 1):
            resolved = aliases.get((sequence[1] + str(position)).upper())
            if not resolved:
                return None
            result.append(resolved)
    return result or None


def _input_names(value, signature, aliases, inputs):
    tokens = value if isinstance(value, list) else [value]
    names, groups = [], []
    for token in tokens:
        if not isinstance(token, Mapping):
            resolved = _names(token, aliases)
            if not resolved:
                return None, []
            names.extend(resolved)
            continue
        if set(token) != {'parameter_group'} or not isinstance(token['parameter_group'], str):
            return None, []
        prefix = token['parameter_group']
        declared = [group for group in signature['value'].get('variadic_parameters', [])
                    if group.get('name_prefix') == prefix]
        if len(declared) != 1:
            return None, []
        group = declared[0]
        minimum, maximum = group.get('minimum'), group.get('maximum')
        if type(minimum) is not int or type(maximum) is not int or not 1 <= minimum <= maximum <= 256:
            return None, []
        members = [parameter['name'] for parameter in inputs
                   if re.fullmatch(re.escape(prefix) + r'[1-9][0-9]*', parameter['name'])]
        if not minimum <= len(members) <= maximum or members != [prefix + str(i) for i in range(1, len(members) + 1)]:
            return None, []
        names.extend(members)
        groups.append({**copy.deepcopy(group), 'members': members, 'selection': 'supplied_arguments_in_order'})
    return (names, groups) if names and len(set(names)) == len(names) else (None, [])


def _block_relation(value, aliases, inputs, outputs):
    if (value.get('operation') == 'copy' and isinstance(value.get('count'), str)
            and type(value.get('repeat_source')) is bool and type(value.get('width_bits')) is int):
        source, target, count = (_names(value.get(key), aliases) for key in ('source','target','count'))
        if (not source or not target or not count or any(len(v)!=1 for v in (source,target,count))
                or not {source[0],count[0]} <= {p['name'] for p in inputs} or target != [outputs[0]['name']]):
            return None
        return [source[0],count[0]], target, {'kind':'range_fill' if value['repeat_source'] else 'range_copy',
            'source':source[0], 'destination':target[0], 'count_operand':count[0],
            'element_bits':value['width_bits']}
    if value.get('operation') not in {'copy_word_block', 'copy_byte_block', 'copy_bit_block', 'block_transfer'}:
        return None
    def region(token):
        if not isinstance(token, str):
            return None
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9]*)_([A-Za-z][A-Za-z0-9]*)_(words|bytes|bits)', token)
        return match.groups() if match else None
    source = region(value.get('object'))
    target = region(value.get('result'))
    if source and target and source[1:] == target[1:]:
        first, last, count, unit = source[0], target[0], source[1], source[2]
    else:
        first, last = value.get('source'), value.get('destination')
        count, unit = value.get('count_operand', value.get('count')), value.get('unit')
    source_name, target_name, count_name = (_names(token, aliases) for token in (first, last, count))
    if (not source_name or not target_name or not count_name or unit not in {'words', 'bytes', 'bits'}
            or any(len(names) != 1 for names in (source_name, target_name, count_name))):
        return None
    if (not {source_name[0], count_name[0]} <= {p['name'] for p in inputs}
            or target_name != [outputs[0]['name']]):
        return None
    element_bits = {'words': 16, 'bytes': 8, 'bits': 1}[unit]
    return [source_name[0], count_name[0]], target_name, {
        'source': source_name[0], 'destination': target_name[0], 'count_operand': count_name[0],
        'element_bits': element_bits, 'output_region': {'head': target_name[0], 'count_operand': count_name[0], 'unit_bits': element_bits}}


def _comparison_relation(original, signature, inputs, outputs, aliases):
    """Compile explicit comparison layouts using the existing expression type.

    Ordered relation lists and named interval bounds are source data. Neither
    mnemonic spelling nor the width modifier supplies an output arrangement or
    an undocumented reversed-bound policy.
    """
    if len(outputs) != 1 or original.get('result_bit_count') != 3:
        return None
    target = _names(original.get('result_start'), aliases)
    if target != [outputs[0]['name']]:
        return None
    ordered = original.get('operands')
    relations = original.get('relations')
    zone = all(isinstance(original.get(key), str) for key in ('lower', 'upper', 'value'))
    if not (zone or isinstance(ordered, list) and len(ordered) == 2
            and isinstance(relations, list) and len(relations) == 3):
        return None
    tokens = [original[key] for key in ('lower', 'upper', 'value')] if zone else ordered
    names = _names(tokens, aliases)
    if not names or set(names) != {p['name'] for p in inputs}:
        return None
    from plc.instruction_definition import ValueType, Expression, DefinitionError
    types = {}
    try:
        for parameter in inputs:
            kind = parameter.get('value_type', parameter.get('type'))
            if not isinstance(kind, Mapping):
                return None
            kind = ValueType.from_mapping(kind).as_mapping()
            if kind.get('kind') not in {'int', 'real'}:
                return None
            types[parameter['name']] = kind
        if any(kind != types[names[0]] for kind in types.values()):
            return None
        boolean = {'kind': 'bool'}
        def parameter(name):
            return {'op':'parameter', 'name':name, 'type':copy.deepcopy(types[name])}
        def compare(op, left, right):
            return {'op':op, 'args':[left,right], 'type':boolean}
        values, preconditions = [], []
        value = copy.deepcopy(dict(original))
        if zone:
            lower, upper, tested = [parameter(name) for name in names]
            policy = original.get('reversed_bounds')
            if policy == 'effective_upper_equals_lower':
                upper = {'op':'select', 'args':[compare('gt',lower,upper),lower,upper], 'type':types[names[0]]}
            elif policy is None:
                preconditions.append(compare('le',lower,upper))
                value.setdefault('unresolved_items', []).append('reversed_bounds_behavior_not_stated')
            else:
                return None
            values = [compare('gt',lower,tested),
                      {'op':'and','args':[compare('le',lower,tested),compare('le',tested,upper)],'type':boolean},
                      compare('gt',tested,upper)]
            roles = ['lower_comparison_bound','upper_comparison_bound','comparison_value']
        else:
            operators = {'greater':'gt','equal':'eq','less':'lt'}
            if set(relations) != set(operators):
                return None
            values = [compare(operators[relation],parameter(names[0]),parameter(names[1])) for relation in relations]
            roles = ['first_comparison_value','second_comparison_value']
        expressions = [Expression.from_mapping(item).as_mapping() for item in values]
        preconditions = [Expression.from_mapping(item).as_mapping() for item in preconditions]
    except (DefinitionError, TypeError, ValueError, KeyError):
        return None
    value.update(operation='interval_comparison' if zone else 'ordered_comparison', inputs=names, output=target[0],
        outputs=[{'target':{'kind':'bit','parameter':target[0],'offset':offset},'expression':expression}
                 for offset,expression in enumerate(expressions)],
        output_layout={'head':target[0],'bit_count':3,'offsets':[0,1,2]},
        operand_binding={'inputs':[{'parameter':name,'type':copy.deepcopy(types[name])} for name in names],
                         'output':{'parameter':target[0],'type':boolean,'occupied_bit_count':3}},
        parameter_usage=[*[{'parameter':name,'purpose':role,'semantic_access':'read'} for name,role in zip(names,roles)],
                         {'parameter':target[0],'purpose':'comparison_result_head','semantic_access':'write'}])
    if preconditions:
        value['preconditions'] = preconditions
    gates = [p for p in signature['value']['operands'] if p.get('enable_pin') or p.get('role') in _GATES]
    if gates:
        value['operand_binding']['execution_arguments'] = [
            {'parameter':p['name'],'role':'execution_condition' if p['direction']=='input' else 'execution_status'} for p in gates]
    return value


def _state_relation(value, signature, inputs, outputs, aliases):
    """Bind a documented native comparison to the existing typed state target."""
    if (value.get('operation') != 'compose_comparison_contact' or not signature['value'].get('native_form')
            or len(inputs) != 2 or outputs or value.get('composition') not in {'assign', 'and', 'or'}):
        return None
    operator = {'=': 'eq', '<>': 'ne', '>': 'gt', '>=': 'ge', '<': 'lt', '<=': 'le'}.get(value.get('comparison'))
    domain = {'signed_BIN16': ('ANY16', {'kind': 'int', 'bits': 16, 'signed': True}),
              'signed_BIN32': ('ANY32', {'kind': 'int', 'bits': 32, 'signed': True}),
              'REAL': ('REAL', {'kind': 'real', 'bits': 32}),
              'LREAL': ('LREAL', {'kind': 'real', 'bits': 64})}.get(value.get('data_domain'))
    left, right = _names(value.get('left'), aliases), _names(value.get('right'), aliases)
    if (not operator or not domain or not left or not right or len(left) != 1 or len(right) != 1
            or set(left + right) != {p['name'] for p in inputs}
            or any(p.get('type', p.get('data_type')) != domain[0] for p in inputs)):
        return None
    from plc.instruction_definition import Expression
    boolean = {'kind': 'bool'}
    expression = {'op': operator, 'type': boolean, 'args': [
        {'op': 'parameter', 'name': name, 'type': domain[1]} for name in left + right]}
    if value['composition'] != 'assign':
        expression = {'op': value['composition'], 'type': boolean, 'args': [
            {'op': 'state', 'name': 'operation_result', 'type': boolean}, expression]}
    result = copy.deepcopy(dict(value))
    result.update(inputs=left + right, output='operation_result', outputs=[{
        'target': {'kind': 'state', 'parameter': 'operation_result', 'offset': 0},
        'expression': Expression.from_mapping(expression).as_mapping()}],
        operand_binding={'inputs': [{'parameter': name, 'type': copy.deepcopy(domain[1])} for name in left + right],
            'output': {'kind': 'implicit_state', 'parameter': 'operation_result', 'type': boolean}},
        state_reads=['operation_result'] if value['composition'] != 'assign' else [])
    return result




def _special_interface_complete(signature):
    operands = signature['value']['operands']
    if len({p['name'] for p in operands}) != len(operands):
        return False
    for pin in operands:
        if pin.get('enable_pin') or pin.get('role') in _GATES:
            raw = pin.get('type', pin.get('data_type'))
            if raw not in ('BOOL', {'kind': 'bool'}) or pin.get('direction') not in {'input', 'output'}:
                return False
    return True

def _structured_comparison_relation(value, signature, inputs, outputs, aliases):
    """Bind a structured numeric contact without manufacturing a native form."""
    declared = signature['value']
    if (not _special_interface_complete(signature) or declared.get('native_form') or declared.get('representation') == 'native_ladder_instruction'
            or value.get('operation') != 'compose_comparison_contact'
            or len(inputs) != 2 or outputs
            or value.get('composition') not in {'assign', 'and', 'or'}
            or any(p.get('optional') or p.get('direction') != 'input' for p in inputs)):
        return None
    domains = {'signed_BIN16': ('INT', 'ANY16', {'kind': 'int', 'bits': 16, 'signed': True}),
               'signed_BIN32': ('DINT', 'ANY32', {'kind': 'int', 'bits': 32, 'signed': True}),
               'REAL': ('REAL', 'REAL', {'kind': 'real', 'bits': 32}),
               'LREAL': ('LREAL', 'LREAL', {'kind': 'real', 'bits': 64})}
    domain = domains.get(value.get('data_domain'))
    operator = {'=': 'eq', '<>': 'ne', '>': 'gt', '>=': 'ge', '<': 'lt', '<=': 'le'}.get(value.get('comparison'))
    left, right = _names(value.get('left'), aliases), _names(value.get('right'), aliases)
    if (not domain or not operator or not left or not right or len(left) != 1 or len(right) != 1
            or len(set(left + right)) != 2 or set(left + right) != {p['name'] for p in inputs}):
        return None
    from plc.instruction_definition import ValueType, Expression, DefinitionError
    try:
        for pin in inputs:
            raw = pin.get('type', pin.get('data_type'))
            if isinstance(raw, str) and raw in domain[:2]:
                continue
            if not isinstance(raw, Mapping) or ValueType.from_mapping(raw) != ValueType.from_mapping(domain[2]):
                return None
        boolean = {'kind': 'bool'}
        expression = {'op': operator, 'type': boolean, 'args': [
            {'op': 'parameter', 'name': name, 'type': domain[2]} for name in left + right]}
        if value['composition'] != 'assign':
            expression = {'op': value['composition'], 'type': boolean, 'args': [
                {'op': 'state', 'name': 'operation_result', 'type': boolean}, expression]}
        expression = Expression.from_mapping(expression).as_mapping()
    except (DefinitionError, TypeError, ValueError):
        return None
    result = copy.deepcopy(dict(value))
    result.update(inputs=left + right, output='operation_result',
        outputs=[{'target': {'kind': 'state', 'parameter': 'operation_result', 'offset': 0},
                  'expression': expression}],
        operand_binding={'inputs': [{'parameter': p['name'],
            'type': copy.deepcopy(p.get('type', p.get('data_type'))), 'value_type': copy.deepcopy(domain[2])}
            for p in inputs], 'output': {'kind': 'implicit_state', 'parameter': 'operation_result', 'type': boolean}},
        state_reads=['operation_result'] if value['composition'] != 'assign' else [])
    return result


def _documented_comparison_relation(value, signature, inputs, outputs, aliases):
    """Bind source-defined string/calendar ordering without granting execution.

    These domains cannot use the finite-width numeric Expression evaluator.
    Their complete ordering, selector and boundary rules stay on the result.
    """
    if (value.get('operation') != 'compose_comparison_contact' or outputs
            or not _special_interface_complete(signature)
            or value.get('composition') not in {'assign', 'and', 'or'}
            or value.get('comparison') not in {'=', '<>', '>', '>=', '<', '<='}
            or any(p.get('optional') or p.get('direction') != 'input' for p in inputs)):
        return None
    domain = value.get('data_domain')
    rules = value.get('domain_comparison')
    if not isinstance(rules, Mapping):
        return None
    left, right = _names(value.get('left'), aliases), _names(value.get('right'), aliases)
    if (not left or not right or len(left) != 1 or len(right) != 1 or left == right):
        return None
    names = left + right
    if domain == 'ASCII_string':
        required = {'kind': 'ascii_lexicographic_until_null', 'unit_bits': 8, 'terminator': 0,
                    'order': 'first_different_character_code',
                    'equality': 'same_characters_and_terminator_position',
                    'prefix_order': 'longer_is_greater',
                    'read_extent': 'first_difference_or_both_terminators',
                    'device_range_check': 'as_compared'}
        if len(inputs) != 2 or any(rules.get(k) != v for k, v in required.items()):
            return None
        from plc.instruction_definition import DefinitionError, ValueType
        try:
            for pin in inputs:
                raw = pin.get('type', pin.get('data_type'))
                if raw != 'STRING' and (not isinstance(raw, Mapping)
                        or ValueType.from_mapping(raw).kind != 'string'):
                    return None
        except (DefinitionError, TypeError, ValueError):
            return None
    elif domain in {'date_year_month_day', 'time_hour_minute_second'}:
        fields = (('year', 1980, 2079), ('month', 1, 12), ('day', 1, 31)) if domain == 'date_year_month_day' else (
            ('hour', 0, 23), ('minute', 0, 59), ('second', 0, 59))
        expected = [{'name': name, 'word_offset': offset, 'select_bit': 2 - offset, 'range': [low, high]}
                    for offset, (name, low, high) in enumerate(fields)]
        if (len(inputs) != 3 or rules.get('kind') != 'selected_calendar_components'
                or rules.get('components') != expected
                or rules.get('comparison_order') != [item[0] for item in fields]
                or rules.get('equality') != 'all_selected_components_equal'
                or rules.get('reserved_zero_bit_range') != {'first': 3, 'last': 14}
                or rules.get('reserved_bits_nonzero_result') is not False
                or rules.get('empty_selection_result') is not False
                or rules.get('invalid_selector_result') is not False
                or rules.get('current_clock_select_bit') != 15
                or rules.get('current_clock_ignores') != 'S2'
                or rules.get('current_clock_source') != ('current_date' if domain == 'date_year_month_day' else 'current_time')
                or rules.get('component_encoding') != 'BIN16'
                or rules.get('comparison_span_words') != 3
                or domain == 'date_year_month_day' and rules.get('calendar_validation') !=
                    'component_ranges_only_actual_date_existence_not_required'):
            return None
        invalid = rules.get('invalid_components')
        if (not isinstance(invalid, Mapping) or invalid.get('state') != 'SM709'
                or invalid.get('comparison_result') is not False
                or invalid.get('latch') != 'until_reset_or_power_off'
                or invalid.get('explicit_clear_allowed') is not True
                or invalid.get('when') != 'component_outside_declared_range_or_device_span_out_of_range'):
            return None
        selector = _names(rules.get('selector'), aliases)
        if not selector or len(selector) != 1 or selector[0] in names:
            return None
        names += selector
        for pin in inputs:
            raw = pin.get('type', pin.get('data_type'))
            if raw == 'ANY16':
                continue
            if not isinstance(raw, Mapping) or not (raw.get('kind', raw.get('flexible_type')) == 'ANY16'
                    or raw.get('kind') == 'int' and raw.get('bits') == 16):
                return None
    else:
        return None
    if len(set(names)) != len(inputs) or set(names) != {pin['name'] for pin in inputs}:
        return None
    result = copy.deepcopy(dict(value))
    result.update(inputs=names, output='operation_result',
        result_relation={'comparison': value['comparison'], 'left': left[0], 'right': right[0],
                         'composition': value['composition'], 'domain_comparison': copy.deepcopy(rules)},
        operand_binding={'inputs': [{'parameter': pin['name'],
            'type': copy.deepcopy(pin.get('type', pin.get('data_type')))} for pin in inputs],
            'output': {'kind': 'implicit_state', 'parameter': 'operation_result', 'type': {'kind': 'bool'}}},
        state_reads=['operation_result'] if value['composition'] != 'assign' else [])
    if domain != 'ASCII_string':
        result['result_relation']['selector'] = selector[0]
    return result


def _documented_bit_array_relation(effect, signature, inputs, outputs, aliases):
    """Bind documented n-element regions, never a scalar BOOL expression.

    Element types describe referenced elements, not a single-value argument.
    Unstated element/bit ordering and pre-head behavior stay unstated.
    """
    original, declared = effect['value'], signature['value']
    operation = original.get('rule')
    kinds = {'bit_array_to_integer', 'integer_to_bit_array', 'bit_array_copy'}
    relation, domain = original.get('bit_array_relation'), original.get('count_domain')
    if (effect['dimension'] != 'effects.result_rule' or operation not in kinds
            or not isinstance(relation, Mapping) or relation.get('kind') != operation
            or not isinstance(domain, Mapping) or domain.get('constant_only') is not True
            or not _special_interface_complete(signature) or declared.get('native_form')
            or declared.get('representation') == 'native_ladder_instruction'
            or len(inputs) != 2 or len(outputs) != 1
            or any(p.get('optional') or p.get('direction') != 'input' for p in inputs)
            or outputs[0].get('optional') or outputs[0].get('direction') not in {'output', 'output_variable'}
            or ('operand_count' in declared and declared['operand_count'] != len(declared['operands']))):
        return None
    names = [_names(relation.get(key), aliases) for key in
             ('source_parameter', 'destination_parameter', 'count_parameter')]
    if any(not name or len(name) != 1 for name in names):
        return None
    source, destination, count = [name[0] for name in names]
    if (source == count or {source, count} != {p['name'] for p in inputs}
            or destination != outputs[0]['name']
            or relation.get('logical_unit') != 'bit_elements'
            or relation.get('element_bit_order') != 'not_stated'):
        return None
    pins = {pin['name']: pin for pin in [*inputs, *outputs]}
    from plc.instruction_definition import ValueType, DefinitionError
    def kind(pin):
        raw = pin.get('type', pin.get('data_type'))
        return raw.get('flexible_type', raw) if isinstance(raw, Mapping) else raw
    def typed(raw, expected):
        try:
            actual = ValueType.from_mapping(raw) if isinstance(raw, Mapping) else ValueType.from_iec(raw)
            return actual == ValueType.from_mapping(expected)
        except (DefinitionError, TypeError, ValueError, AttributeError):
            return False
    if not typed(kind(pins[count]), {'kind': 'int', 'bits': 16, 'signed': True}):
        return None
    counts = domain.get('allowed_counts')
    if not isinstance(counts, list) or any(type(count) is not int for count in counts):
        return None
    integer_pin = pins[destination] if operation == 'bit_array_to_integer' else pins[source]
    width = 32 if operation == 'bit_array_copy' else _integer_operand_bits({'type': kind(integer_pin)})
    if width not in {16, 32} or counts != list(range(4, width + 1, 4)):
        return None
    reference_names = ([source] if operation == 'bit_array_to_integer' else
                       [destination] if operation == 'integer_to_bit_array' else [source, destination])
    for name in reference_names:
        pin, reference = pins[name], pins[name].get('array_element_reference')
        if (not typed(kind(pin), {'kind': 'bool'}) or pin.get('argument_passing') != 'array_element_reference'
                or not isinstance(reference, Mapping) or reference.get('start') != 'selected_array_element'
                or not typed(reference.get('element_type'), {'kind': 'bool'})):
            return None
        extent = reference.get('extent')
        if (not isinstance(extent, Mapping) or _names(extent.get('parameter'), aliases) != [count]
                or extent.get('unit') != 'logical_elements' or extent.get('constant_only') is not True
                or extent.get('allowed_counts') != counts):
            return None
    if operation != 'bit_array_copy' and any(key in integer_pin for key in
            ('array_element_reference', 'argument_passing')):
        return None
    expected = ({'source_selection': 'n_elements_from_selected_array_element',
                 'destination_selection': 'converted_integer', 'destination_bits_beyond_count': 'zero'}
                if operation == 'bit_array_to_integer' else
                {'source_selection': 'low_order_n_bits',
                 'destination_selection': 'n_elements_from_selected_array_element',
                 'trailing_destination_elements': 'unchanged'} if operation == 'integer_to_bit_array' else
                {'source_selection': 'n_elements_from_selected_array_element',
                 'destination_selection': 'n_elements_from_selected_array_element',
                 'trailing_destination_elements': 'not_stated'})
    base_keys = {'kind', 'source_parameter', 'destination_parameter', 'count_parameter',
                 'logical_unit', 'source_selection', 'destination_selection', 'element_bit_order'}
    if (set(relation) != base_keys | set(expected)
            or set(domain) != {'allowed_counts', 'constant_only'}
            or any(relation.get(key) != value for key, value in expected.items())):
        return None
    returned = declared.get('return_shape')
    gates = [p for p in declared['operands'] if p.get('enable_pin') or p.get('role') in _GATES]
    if not isinstance(returned, Mapping) or returned.get('returned_by_st_call') is not True:
        return None
    if gates:
        if (len(gates) != 2 or {(p['name'], p['direction']) for p in gates} != {('EN', 'input'), ('ENO', 'output')}
                or returned.get('role') != 'ENO_execution_status'
                or returned.get('operation_result_via') != 'output_argument'
                or not typed(returned.get('value_type'), {'kind': 'bool'})
                or returned.get('array_element_reference') or returned.get('argument_passing')):
            return None
    elif (returned.get('role') == 'ENO_execution_status'
            or returned.get('operation_result_via') != 'return_value'
            or kind({'type': returned.get('value_type')}) != kind(outputs[0])
            or any(returned.get(key) != outputs[0].get(key) for key in
                   ('argument_passing', 'array_element_reference'))):
        return None
    def binding(pin):
        return {'parameter': pin['name'], 'type': copy.deepcopy(pin.get('type', pin.get('data_type'))),
                'direction': pin['direction'], **{key: copy.deepcopy(pin[key]) for key in
                ('argument_passing', 'array_element_reference') if key in pin}}
    result = copy.deepcopy(dict(original))
    result.update(operation=operation, inputs=[source, count], output=destination,
        relation_scope='documented_n_element_relation', executor_capability='not_established',
        result_relation={**copy.deepcopy(relation), 'source_parameter': source,
                         'destination_parameter': destination, 'count_parameter': count,
                         'count_domain': copy.deepcopy(domain)},
        operand_binding={'inputs': [binding(pins[source]), binding(pins[count])],
                         'output': binding(outputs[0]), 'return_shape': copy.deepcopy(returned)})
    gap = ('trailing_destination_elements_not_stated' if operation == 'bit_array_copy' else
           'array_element_to_integer_bit_position_not_stated' if operation == 'bit_array_to_integer' else
           'integer_bit_to_array_element_order_not_stated')
    result['unresolved_items'] = list(dict.fromkeys([*result.get('unresolved_items', []), gap]))
    result['full_source_relation_complete'] = False
    return result


def _bit_array_prerequisites(effect, signature, indexed):
    """Require this rule's own declared count, boundary and call prerequisites."""
    if _operands(signature) is None:
        return False
    pending, parents = list(effect.get('depends_on', [])), {}
    while pending:
        identity = pending.pop()
        if identity in parents:
            continue
        parent = indexed.get(identity)
        if parent is None:
            return False
        parents[identity] = parent
        pending.extend(parent.get('depends_on', []))
    if signature['id'] not in parents:
        return False
    if any(parent.get('status') != 'source_verified' or parent.get('verification') != 'source_checked'
           or fact_context_intersection(parent, effect) is None
           or fact_context_intersection(parent, signature) is None for parent in parents.values()):
        return False
    def selected(dimension):
        return [parent['value'] for parent in parents.values() if parent.get('dimension') == dimension]
    domains = selected('constraints.bit_count')
    expected = effect['value'].get('count_domain', {})
    if (not isinstance(expected, Mapping) or len(domains) != 1 or domains[0].get('constant_only') is not True
            or domains[0].get('allowed') != expected.get('allowed_counts')
            or not isinstance(domains[0].get('allowed'), list)
            or any(type(count) is not int for count in domains[0]['allowed'])):
        return False
    operation = effect['value'].get('rule')
    padding = selected('constraints.result_padding')
    if operation == 'bit_array_copy':
        if padding:
            return False
    elif (len(padding) != 1 or padding[0].get('beyond_specified_bits') !=
          ('zero' if operation == 'bit_array_to_integer' else 'unchanged')):
        return False
    enabled = any(p.get('enable_pin') or p.get('role') in _GATES for p in signature['value']['operands'])
    execution = selected('execution.enable' if enabled else 'execution.call_enable')
    if len(execution) != 1:
        return False
    if enabled:
        return (execution[0].get('enable_argument') == 'EN' and execution[0].get('status_argument') == 'ENO'
                and execution[0].get('enabled_form_execution') == 'when_EN_is_TRUE')
    return (execution[0].get('enable_parameter') is False and execution[0].get('enable_gate') == 'none'
            and execution[0].get('evaluation') == 'at_call_site')


def _special_result_relation(effect, signature, inputs, outputs):
    """Bind source-shaped TIME/FIND rules, retaining source boundary clauses.

    Missing boundary/string clauses stay explicit gaps; the operation label by
    itself is insufficient to claim a complete effects relation.
    """
    original = effect['value']
    operation = original.get('rule')
    if (not _special_interface_complete(signature)
            or effect['dimension'] != 'effects.result_rule' or operation not in {'time_addition', 'substring_search'}
            or len(inputs) != 2 or len(outputs) != 1
            or any(p.get('optional') or p['direction'] != 'input' for p in inputs)):
        return None
    def matches(pin, token, mapping):
        raw = pin.get('type', pin.get('data_type'))
        if raw == token or raw == mapping:
            return True
        return (token == 'TIME' and isinstance(raw, Mapping)
                and raw == {**mapping, 'signed': True})
    time = {'kind': 'int', 'bits': 32, 'unit': 'ms'}
    string = {'kind': 'string', 'encoding': 'binary'}
    integer = {'kind': 'int', 'bits': 16, 'signed': True}
    if operation == 'time_addition':
        if (not all(matches(p, 'TIME', time) for p in [*inputs, *outputs])
                or original.get('time_unit_ms') != 1 or original.get('result_type') != 'time'):
            return None
        boundary, flags = original.get('boundary_behavior'), original.get('result_flags')
        complete = {
            'boundary_behavior': (isinstance(boundary, Mapping)
                and boundary.get('underflow') == boundary.get('overflow') == 'not_operation_error'
                and boundary.get('accurate_result_available') is False),
            'result_flags': (isinstance(flags, Mapping) and all(
                isinstance(flags.get(flag), Mapping) and flags[flag].get('on') and flags[flag].get('off')
                for flag in ('M8020', 'M8021', 'M8022')))}
        relation = {'operator': 'addition', 'arguments': [p['name'] for p in inputs], 'unit': 'ms'}
    else:
        if (not all(matches(p, 'STRING', string) for p in inputs)
                or not matches(outputs[0], 'INT', integer) or original.get('result_type') != 'word_signed'
                or type(original.get('position_origin')) is not int or original['position_origin'] != 1
                or type(original.get('not_found')) is not int or original['not_found'] != 0):
            return None
        conditions = original.get('string_conditions')
        missing_terminator = conditions.get('missing_terminator') if isinstance(conditions, Mapping) else None
        complete = {
            'match_selection': original.get('match_selection') == 'first_occurrence',
            'string_conditions': (isinstance(conditions, Mapping)
                and conditions.get('unit_bits') == 8 and type(conditions.get('terminator')) is int
                and conditions['terminator'] == 0 and conditions.get('extent') == 'through_first_terminator'
                and conditions.get('within_device_range') is True and isinstance(missing_terminator, Mapping)
                and missing_terminator.get('error_code') == 'K6706'
                and missing_terminator.get('flag') == 'M8067' and missing_terminator.get('code_register') == 'D8067')}
        relation = {'operator': 'substring_search', 'searched_string': inputs[0]['name'],
            'search_string': inputs[1]['name'], 'position_origin': 1, 'not_found': 0}
    result = copy.deepcopy(dict(original))
    result.update(operation=operation, inputs=[p['name'] for p in inputs], output=outputs[0]['name'],
        result_relation=relation, operand_binding={
            'inputs': [{'parameter': p['name'], 'type': copy.deepcopy(p.get('type', p.get('data_type')))} for p in inputs],
            'output': {'parameter': outputs[0]['name'], 'type': copy.deepcopy(outputs[0].get('type', outputs[0].get('data_type')))}})
    aliases = _operands(signature)[2]
    for key in ('inputs', 'input', 'source'):
        if key in original and _names(original[key], aliases) != result['inputs']:
            return None
    for key in ('output', 'result', 'destination', 'target'):
        if key in original and _names(original[key], aliases) != [result['output']]:
            return None
    if outputs[0].get('returned_value'):
        result['operand_binding']['output']['kind'] = 'return_value'
    if signature['value'].get('return_shape'):
        result['operand_binding']['return_shape'] = copy.deepcopy(signature['value']['return_shape'])
    for key, established in complete.items():
        if not established:
            result.setdefault('unresolved_items', []).append(key + '_not_stated')
        if key in original:
            relation[key] = copy.deepcopy(original[key])
    return result

def _integer_operand_bits(parameter):
    from plc.instruction_definition import ValueType, DefinitionError
    raw = parameter.get('type', parameter.get('data_type'))
    if isinstance(raw, str) and re.fullmatch(r'ANY(?:16|32)', raw):
        return int(raw[3:])
    try:
        kind = ValueType.from_mapping(raw) if isinstance(raw, Mapping) else ValueType.from_iec(raw)
        return kind.bits if kind.kind == 'int' else None
    except (DefinitionError, AttributeError, TypeError):
        return None


def _byte_exchange_relation(value, signature, inputs, outputs, aliases):
    if (value.get('operation') != 'exchange_word_bytes' or value.get('width_bits') != 16
            or value.get('byte_bits') != 8 or value.get('write_semantics') != 'in_place'):
        return None
    names = _names(value.get('data_operand'), aliases)
    declared = {p['name']: p for p in [*inputs, *outputs]}
    if not names or len(names) != 1 or set(names) != set(declared) or _integer_operand_bits(declared[names[0]]) != 16:
        return None
    from plc.instruction_definition import Expression
    name = names[0]
    integer = {'kind': 'int', 'bits': 16, 'signed': False}
    parameter = {'op': 'parameter', 'name': name, 'type': integer}
    constant = lambda number: {'op': 'constant', 'type': integer, 'value': number}
    expression = {'op': 'bit_or', 'type': integer, 'args': [
        {'op': 'shift_left', 'type': integer, 'args': [
            {'op': 'bit_and', 'type': integer, 'args': [parameter, constant(255)]}, constant(8)]},
        {'op': 'shift_right', 'type': integer, 'args': [parameter, constant(8)]}]}
    storage = copy.deepcopy(declared[name].get('type', declared[name].get('data_type')))
    result = copy.deepcopy(dict(value))
    result.update(knowledge_kind='manual_behavior', inputs=[name], output=name, data_operand=name,
        executor_capability='not_established',
        outputs=[{'target': {'kind': 'word', 'parameter': name, 'offset': 0},
                  'expression': Expression.from_mapping(expression).as_mapping()}],
        operand_binding={'inputs': [{'parameter': name, 'type': storage, 'value_type': integer}],
                         'output': {'parameter': name, 'type': storage, 'result_value_type': integer}},
        parameter_usage=[{'parameter': name, 'purpose': 'word_whose_two_bytes_are_exchanged_in_place',
            'semantic_access': 'read_write', 'operation_value_type': integer, 'result_value_type': integer,
            'word_device_layout': {'device_unit_bits': 16, 'word_count': 1, 'low_word_offset': 0,
                                   'high_words_follow': True, 'applies_to': '16_bit_word_devices'}}])
    return result


def _rotation_relation(value, signature, inputs, outputs, aliases):
    """Bind source-declared rotation rules to their own typed interface."""
    if (value.get('operation') != 'rotate' or value.get('direction') not in {'left', 'right'}
            or value.get('carry_flag') not in {'included', 'excluded'}
            or value.get('count_policy') != 'modulo_effective_operand_bits'
            or value.get('operand_width_policy') != 'word_width_or_digit_specified_bits'
            or value.get('bits_per_digit') != 4 or value.get('carry_result') != 'last_shifted_bit'
            or value.get('reference_domain') != 'positive_effective_count'
            or not isinstance(value.get('carry_device'), str) or not value['carry_device']):
        return None
    data, count = _names(value.get('data_operand'), aliases), _names(value.get('count_operand'), aliases)
    if not data or not count or len(data) != 1 or len(count) != 1 or data == count:
        return None
    declared = {p['name']: p for p in [*inputs, *outputs]}
    if (set(declared) != set(data + count) or count[0] not in {p['name'] for p in inputs}
            or data[0] not in {p['name'] for p in outputs}):
        return None
    from plc.instruction_definition import Expression
    def storage_type(name):
        return declared[name].get('type', declared[name].get('data_type'))
    bits = value.get('width_bits')
    if bits not in {16, 32} or _integer_operand_bits(declared[data[0]]) != bits or _integer_operand_bits(declared[count[0]]) != 16:
        return None
    integer = lambda width=bits: {'kind': 'int', 'bits': width, 'signed': False}
    boolean = {'kind': 'bool'}
    constant = lambda number, width=bits: {'op': 'constant', 'type': integer(width), 'value': number}
    state = lambda name, kind=None: {'op': 'state', 'name': name, 'type': kind or integer()}
    def expression(op, *args, kind=None, wrap=False):
        return {'op': op, 'type': kind or integer(), 'args': list(args), **({'overflow': 'wrap'} if wrap else {})}
    word = state('data_before_step')
    last = state('active_highest_bit', integer(16))
    mask = state('active_data_mask')
    right = value['direction'] == 'right'
    edge = expression('bit_and', word, constant(1)) if right else expression('shift_right', word, last)
    carry = expression('ne', edge, constant(0), kind=boolean)
    incoming = (expression('select', state('carry_before_step', boolean), constant(1), constant(0))
                if value['carry_flag'] == 'included' else copy.deepcopy(edge))
    shifted = expression('shift_right' if right else 'shift_left', word, constant(1, 16), wrap=not right)
    rotated = expression('bit_or', shifted, expression('shift_left', incoming, last) if right else incoming)
    rotated = expression('bit_and', rotated, mask)
    normalized = {'op': 'mod', 'type': integer(16), 'args': [
        {'op': 'parameter', 'name': count[0], 'type': integer(16)}, state('effective_operand_bits', integer(16))]}
    layout = {'device_unit_bits': 16, 'low_word_offset': 0, 'high_words_follow': True,
              'applies_to': '16_bit_word_devices', 'value_bits': bits, 'word_count': bits//16}
    result = copy.deepcopy(dict(value))
    result.update(knowledge_kind='manual_behavior', inputs=[data[0], count[0]], output=data[0],
        data_operand=data[0], count_operand=count[0], executor_capability='not_established',
        state_bindings={
            'data_before_step': {'source_parameter': data[0], 'type': integer(), 'inactive_container_bits': 'zero',
                                 'sampled_at': 'before_each_reference_step'},
            'carry_before_step': {'source_device': value['carry_device'], 'type': boolean,
                                  'sampled_at': 'before_each_reference_step'},
            'effective_operand_bits': {'source': 'actual_operand_device_form', 'word_device_bits': bits,
                'digit_specified_bit_device': {'bits_per_digit': 4, 'minimum_bits': 4, 'maximum_bits': bits}},
            'active_highest_bit': {'derived_value': Expression.from_mapping(expression(
                'sub', state('effective_operand_bits', integer(16)), constant(1, 16),
                kind=integer(16))).as_mapping(), 'type': integer(16)},
            'active_data_mask': {'derived_value': Expression.from_mapping(expression(
                'sub', expression('shift_left', constant(1, 64),
                    state('effective_operand_bits', integer(16)), kind=integer(64)),
                constant(1, 64), kind=integer(64))).as_mapping(), 'type': integer()}},
        iteration={'count_expression': Expression.from_mapping(normalized).as_mapping(),
                   'reference_domain': 'positive_effective_count', 'state_update': 'simultaneous_after_each_step',
                   'scope': 'mathematical_reference_for_one_instruction_evaluation_not_multiple_scans'},
        reference_step={'outputs': [
            {'target': {'kind': 'selected_operand_span', 'parameter': data[0], 'effective_bits': 'effective_operand_bits'},
             'expression': Expression.from_mapping(rotated).as_mapping()},
            {'target': {'kind': 'bit', 'device': value['carry_device']},
             'expression': Expression.from_mapping(carry).as_mapping()}]},
        operand_binding={'inputs': [
            {'parameter': data[0], 'type': copy.deepcopy(storage_type(data[0])), 'value_type': integer()},
            {'parameter': count[0], 'type': copy.deepcopy(storage_type(count[0])), 'value_type': integer(16)}],
            'output': {'parameter': data[0], 'type': copy.deepcopy(storage_type(data[0])), 'result_value_type': integer()}},
        parameter_usage=[
            {'parameter': data[0], 'purpose': 'bit_pattern_rotated_in_place', 'semantic_access': 'read_write',
             'operation_value_type': integer(), 'result_value_type': integer(), 'word_device_layout': layout,
             'range': {'digit_specified_bit_devices': {'bits_per_digit': 4, 'minimum_bits': 4, 'maximum_bits': bits,
                        'partial_width_supported': True}}},
            {'parameter': count[0], 'purpose': 'rotation_count_reduced_modulo_actual_operand_bits', 'semantic_access': 'read',
             'operation_value_type': integer(16), 'word_device_layout': {**layout, 'value_bits': 16, 'word_count': 1},
             'range': {'recommended_minimum': 0, 'recommended_maximum': bits-1,
                       'larger_nonnegative_counts': 'modulo_effective_operand_bits'}}])
    return result


def _state_access_relation(value, signature, inputs, outputs, aliases):
    """Bind explicit state effects, with zero or one declared data result.

    State identities and access directions are source data. Binding them does
    not supply an undocumented state algorithm or an execution implementation.
    """
    accesses = value.get('state_accesses')
    operation = value.get('operation')
    if (not isinstance(accesses, list) or not accesses or len(outputs) > 1
            or not isinstance(value.get('inputs'), list)
            or not isinstance(operation, str) or operation in {'', 'unknown', 'not_established'}):
        return None
    for access in accesses:
        target = access.get('target') if isinstance(access, Mapping) else None
        if (not isinstance(target, Mapping) or target.get('kind') != 'state'
                or not isinstance(target.get('parameter'), str) or not target['parameter']
                or access.get('access') not in {'read', 'write', 'read_write'}):
            return None
    names, groups = (([], []) if not value['inputs'] and not inputs else
                     _input_names(value['inputs'], signature, aliases, inputs))
    if names is None or groups or len(names) != len(inputs) or set(names) != {p['name'] for p in inputs}:
        return None
    target = _names(value.get('output'), aliases) if outputs else None
    if (outputs and target != [outputs[0]['name']] or not outputs and value.get('output') is not None):
        return None
    result = copy.deepcopy(dict(value))
    result['inputs'] = names
    result['operand_binding'] = {'inputs': [
        {'parameter': p['name'], 'type': copy.deepcopy(p.get('type', p.get('data_type')))} for p in inputs]}
    if outputs:
        result['output'] = target[0]
        result['operand_binding']['output'] = {
            'parameter': target[0], 'type': copy.deepcopy(outputs[0].get('type', outputs[0].get('data_type')))}
    return result


def _relation(effect, signature):
    original = effect.get('value', {})
    if (not isinstance(original, Mapping) or original.get('knowledge_scope') == 'operation_summary'
            or original.get('behavior') or original.get('outputs') or original.get('rules')):
        return None
    declared = _operands(signature)
    if declared is None:
        return None
    inputs, outputs, aliases = declared
    if original.get('rule') in {'bit_array_to_integer', 'integer_to_bit_array', 'bit_array_copy'}:
        return _documented_bit_array_relation(effect, signature, inputs, outputs, aliases)
    byte_exchange = _byte_exchange_relation(original, signature, inputs, outputs, aliases)
    if byte_exchange is not None:
        return byte_exchange
    rotation = _rotation_relation(original, signature, inputs, outputs, aliases)
    if rotation is not None:
        return rotation
    comparison = _comparison_relation(original, signature, inputs, outputs, aliases)
    if comparison is not None:
        return comparison
    state = _state_relation(original, signature, inputs, outputs, aliases)
    if state is not None:
        return state
    structured = _structured_comparison_relation(original, signature, inputs, outputs, aliases)
    if structured is not None:
        return structured
    documented = _documented_comparison_relation(original, signature, inputs, outputs, aliases)
    if documented is not None:
        return documented
    if original.get('operation') == 'compose_comparison_contact':
        return None
    if original.get('rule') in {'time_addition', 'substring_search'}:
        return _special_result_relation(effect, signature, inputs, outputs)
    if 'state_accesses' in original:
        return _state_access_relation(original, signature, inputs, outputs, aliases)
    if len(outputs) != 1:
        return None
    operation = original.get('operation') or original.get('rule')
    if not isinstance(operation, str) or operation in {'', 'unknown', 'not_established'}:
        return None
    names, groups = _input_names(original.get('inputs', original.get('input', original.get('source'))), signature, aliases, inputs)
    target = _names(original.get('output', original.get('result', original.get('destination', original.get('target')))), aliases)
    extras = {'input_groups': groups} if groups else {}
    block = _block_relation(original, aliases, inputs, outputs)
    # Literal relations already name both sides. Their original qualifiers,
    # widths, rounding and boundary properties are copied without reinterpretation.
    if block:
        names, target, extras = block
    elif names and target and len(target) == 1:
        pass
    elif (original.get('input_binding') == 'documented_two_data_or_three_data_form'
          and signature['value'].get('native_form') and operation in {'add', 'multiply', 'bitwise_and', 'bitwise_or', 'bitwise_xor'}
          and len(inputs) == 2 and target == [outputs[0]['name']]):
        names = [p['name'] for p in inputs]
    elif effect['dimension'] == 'effects.result_rule' and operation in _UNARY and len(inputs) == 1:
        names, target = [inputs[0]['name']], [outputs[0]['name']]
    elif effect['dimension'] == 'effects.result_rule' and operation in _BINARY and len(inputs) == 2:
        names, target = [p['name'] for p in inputs], [outputs[0]['name']]
    elif effect['dimension'] == 'effects.result_rule' and operation in _FOLDS:
        count = original.get('operand_pins')
        if len(inputs) < 2 and not (len(inputs) == 1 and isinstance(count, Mapping)
                and type(count.get('minimum')) is int and type(count.get('maximum')) is int
                and 2 <= count['minimum'] <= count['maximum']):
            return None
        names, target = [p['name'] for p in inputs], [outputs[0]['name']]
        if len(inputs) == 1:
            extras['input_groups'] = [{'parameter': names[0], 'minimum': count['minimum'], 'maximum': count['maximum']}]
    elif effect['dimension'] == 'effects.result_rule' and operation == 'select_by_condition' and len(inputs) == 3:
        false, true = _names(original.get('condition_FALSE'), aliases), _names(original.get('condition_TRUE'), aliases)
        if not false or not true or len(false) != 1 or len(true) != 1:
            return None
        selectors = [p for p in inputs if p['name'] not in {*false, *true}]
        if len(selectors) != 1 or selectors[0].get('type', selectors[0].get('data_type')) not in ('BOOL', {'kind': 'bool'}):
            return None
        names, target = [p['name'] for p in inputs], [outputs[0]['name']]
        extras['selector'] = selectors[0]['name']
    elif operation in {'transfer', 'copy_value'} and len(inputs) == 1 and original.get('width_bits'):
        names, target = [inputs[0]['name']], [outputs[0]['name']]
    elif operation in _UNARY and original.get('object') == 'argument_value' and len(inputs) == 1:
        names, target = [inputs[0]['name']], [outputs[0]['name']]
    else:
        return None
    input_names = {p['name'] for p in inputs}
    if not set(names) <= input_names or target != [outputs[0]['name']]:
        return None
    value = copy.deepcopy(dict(original))
    value.update(operation=operation, inputs=names, output=target[0], **extras)
    if original.get('result_components') and not original.get('output_layout'):
        value.setdefault('unresolved_items', []).append('result_component_layout')
    if original.get('outcomes') and not original.get('result_mapping') and not original.get('output_layout'):
        value.setdefault('unresolved_items', []).append('comparison_result_mapping')
    value['operand_binding'] = {
        'inputs': [{'parameter': p['name'], 'type': copy.deepcopy(p.get('type', p.get('data_type'))),
                    **({'type_alternatives': copy.deepcopy(p['type_alternatives'])}
                       if 'type_alternatives' in p else {}),
                    **({'optional': True} if p.get('optional') else {})} for p in inputs if p['name'] in names],
        'output': {'parameter': target[0], 'type': copy.deepcopy(outputs[0].get('type', outputs[0].get('data_type')))},
    }
    from plc.instruction_definition import ValueType, DefinitionError
    try:
        if isinstance(original.get('input_value_type'), Mapping):
            interpretation = ValueType.from_mapping(original['input_value_type']).as_mapping()
            for parameter in value['operand_binding']['inputs']:
                parameter['value_type'] = copy.deepcopy(interpretation)
        if isinstance(original.get('result_value_type'), Mapping):
            value['operand_binding']['output']['result_value_type'] = ValueType.from_mapping(original['result_value_type']).as_mapping()
    except DefinitionError:
        return None
    layout = value.get('output_layout')
    if isinstance(layout, Mapping) and _names(layout.get('head'), aliases) == target:
        value['operand_binding']['output']['write_layout'] = {**copy.deepcopy(layout), 'head': target[0]}
    for key in ('type_alternatives', 'array', 'element_type', 'element_type_alternatives', 'occupied_bit_count',
                'word_device_layout', 'form_width_bits', 'encoding'):
        if key in outputs[0]:
            value['operand_binding']['output'][key] = copy.deepcopy(outputs[0][key])
    gates = [p for p in signature['value']['operands'] if p.get('enable_pin') or p.get('role') in _GATES]
    if gates:
        value['operand_binding']['execution_arguments'] = [
            {'parameter': p['name'], 'role': 'execution_condition' if p['direction'] == 'input' else 'execution_status'}
            for p in gates]
    if outputs[0].get('returned_value'):
        value['operand_binding']['output']['kind'] = 'return_value'
    returned = signature['value'].get('return_shape')
    if returned:
        value['operand_binding']['return_shape'] = copy.deepcopy(returned)
    # An explicit source symbol/marker names a bound operand. Preserve that
    # relation while translating aliases to the declaration used by this form.
    for key in ('selector', 'lower_limit', 'upper_limit', 'count', 'condition_FALSE', 'condition_TRUE', 'when_false', 'when_true'):
        bound = _names(original.get(key), aliases)
        if bound and len(bound) == 1:
            value[key] = bound[0]
            if key in {'selector','lower_limit','upper_limit','count'} and bound[0] in input_names:
                use = {'parameter':bound[0],'purpose':key,'semantic_access':'read'}
                if use not in value.setdefault('parameter_usage',[]):
                    value['parameter_usage'].append(use)
                if not any(p['parameter']==bound[0] for p in value['operand_binding']['inputs']):
                    declared = next(p for p in inputs if p['name']==bound[0])
                    value['operand_binding']['inputs'].append({'parameter':bound[0],
                        'type':copy.deepcopy(declared.get('type',declared.get('data_type'))),
                        **({'type_alternatives': copy.deepcopy(declared['type_alternatives'])}
                           if 'type_alternatives' in declared else {})})
    return value


def bind_relation_facts(facts):
    """Compile actual relation/declaration bindings, retaining every qualifier."""
    signatures = [_return_declaration(_gate_declaration(f, facts), facts) for f in facts if f.get('dimension') == 'parameters.typed_operands'
                  and not f['id'].startswith(DERIVED_PREFIXES)]
    result = []
    indexed = {f['id']: f for f in facts}
    for effect in facts:
        if (effect.get('dimension') not in _PRIMARY or effect['id'].startswith(('assembly:parameters:', 'assembly:relation:'))
                or effect.get('verification') != 'source_checked' or effect.get('status') in {'unknown', 'not_applicable'}):
            continue
        for signature in signatures:
            if (signature.get('verification') != 'source_checked' or not _source_related(effect, signature)
                    or not same_relation_interface(effect, signature)):
                continue
            context = fact_context_intersection(effect, signature)
            if context is None:
                continue
            special = effect.get('value', {}).get('rule') in {'time_addition', 'substring_search',
                'bit_array_to_integer', 'integer_to_bit_array', 'bit_array_copy'} or (
                effect.get('value', {}).get('operation') == 'compose_comparison_contact'
                and (not signature['value'].get('native_form')
                     or effect['value'].get('data_domain') in {
                         'ASCII_string', 'date_year_month_day', 'time_hour_minute_second'}))
            if special:
                pending, seen = [effect, signature], set()
                missing = False
                while pending:
                    parent = pending.pop()
                    for dependency in [*parent.get('depends_on', []), *parent.get('_binding_dependencies', [])]:
                        if dependency not in indexed:
                            missing = True
                            break
                        if dependency not in seen:
                            seen.add(dependency)
                            pending.append(indexed[dependency])
                    if missing:
                        break
                if missing:
                    continue
            if (effect.get('value', {}).get('rule') in {
                    'bit_array_to_integer', 'integer_to_bit_array', 'bit_array_copy'}
                    and not _bit_array_prerequisites(effect, signature, indexed)):
                continue
            value = _relation(effect, signature)
            if value is None:
                continue
            gates = [p for p in signature['value']['operands'] if p.get('enable_pin') or p.get('role') in _GATES]
            if gates and isinstance(value.get('operand_binding'), dict):
                value['operand_binding'].setdefault('execution_arguments', [
                    {'parameter': p['name'], 'role': 'execution_condition' if p['direction'] == 'input' else 'execution_status'}
                    for p in gates])
            scope, conditions = context
            native = signature['value'].get('native_form') or signature['value'].get('representation') == 'native_ladder_instruction'
            if native and 'ladder' in scope.get('languages', []):
                native_condition = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
                probe = fact_context_intersection({'scope': scope, 'conditions': conditions}, {'conditions': [native_condition]})
                if probe is None:
                    continue
                _, conditions = probe
            statuses = {effect.get('status'), signature.get('status')}
            result.append({'id': 'assembly:relation:' + signature['id'] + ':' + effect['id'],
                'dimension': 'effects.bound_relation', 'value': value, 'scope': scope,
                'conditions': conditions, 'status': next((s for s in _STRENGTH if s in statuses), 'unknown'),
                'verification': 'source_checked', 'sources': copy.deepcopy([*effect.get('sources', []), *signature.get('sources', [])]),
                'depends_on': [effect['id'], signature['id'], *signature.get('_binding_dependencies', [])]})
    return result


def call_boundary_facts(facts):
    """Retain caller-owned evaluation for a plain scalar ST function.

    No invocation produces no call result; this does not assert a device hold
    rule or the internal disabled behavior of a function block.
    """
    pure = _UNARY | _FOLDS | _BINARY | {'add', 'subtract', 'multiply', 'divide', 'convert_type',
        'copy_value', 'select_value', 'clamp_value', 'select_by_condition'}
    calls = [f for f in facts if f.get('dimension') == 'parameters.st_signature'
             and f['value'].get('enable') is False and f['value'].get('available') is not False
             and isinstance(f['value'].get('returns'), Mapping)]
    evaluations = [f for f in facts if f.get('dimension') == 'execution.call_enable'
                   and f['value'].get('enable_parameter') is False and f['value'].get('enable_gate') == 'none'
                   and f['value'].get('evaluation') == 'at_call_site']
    result = []
    for relation in facts:
        value = relation.get('value', {})
        if (relation.get('dimension') != 'effects.bound_relation' or value.get('operation') not in pure
                or value.get('unresolved_items') or value.get('full_source_relation_complete') is False):
            continue
        returned = value.get('operand_binding', {}).get('return_shape', {})
        if not returned.get('returned_by_st_call'):
            continue
        for call in calls:
            if call['value']['returns'].get('name') != value['output'] or not _source_related(relation, call):
                continue
            base_context = fact_context_intersection(relation, call)
            if base_context is None:
                continue
            for evaluation in evaluations:
                if not _source_related(relation, evaluation):
                    continue
                context = fact_context_intersection({'scope': base_context[0], 'conditions': base_context[1]}, evaluation)
                if context is None or 'st' not in context[0].get('languages', []):
                    continue
                parents = (relation, call, evaluation)
                if any(f.get('verification') != 'source_checked' or f.get('status') != 'source_verified' for f in parents):
                    continue
                result.append({'id': 'assembly:call:' + relation['id'] + ':' + call['id'] + ':' + evaluation['id'],
                    'dimension': 'execution.disabled_state', 'scope': {**context[0], 'languages': ['st']},
                    'conditions': context[1], 'status': 'source_verified', 'verification': 'source_checked',
                    'value': {'enable_argument': 'absent', 'evaluation': 'at_call_site',
                        'when_not_executed': 'no_call_result_is_produced', 'result_assignment_owner': 'caller_expression_or_control_flow'},
                    'sources': copy.deepcopy([source for f in parents for source in f.get('sources', [])]),
                    'depends_on': [f['id'] for f in parents]})
                break
    return result
