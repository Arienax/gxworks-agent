"""Shared instruction-family presentation for MCP and model messages.

The input facts remain the Core/debug evidence. This view groups their scopes
and statuses, joins parameter purposes, and composes reviewed family knowledge.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping


def _key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _unique(values):
    return list({_key(value): value for value in values}.values())


def _source_conditions(fact, project):
    return _unique(project(source[key]) for source in fact.get('sources', [])
                   for key in ('conditions', 'applicability') if source.get(key))


SHARED_INSTRUCTION_KINDS = frozenset({'instruction_modifiers', 'instruction_execution'})


def instruction_delivery_languages(query, language=None, fallback=None):
    """Select one requested language, or the two sides of a rewrite."""
    pattern = re.compile(r'\b(?:ST|FBD|Ladder|structured\s+text)\b|结构化文本|结构化梯形图|梯形图', re.I)
    def canonical(token):
        token = ' '.join(token.casefold().split())
        return 'st' if token in {'st', 'structured text', '结构化文本'} else 'fbd' if token == 'fbd' else 'ladder'
    mentioned = list(dict.fromkeys(canonical(match.group()) for match in pattern.finditer(str(query))))
    rewrite = re.search(r'改写|转换|转成|改成|重写|rewrite|convert|translate', str(query), re.I)
    explicit = re.search(r'(?:使用|采用|用|以|using\s+|in\s+)\s*(' + pattern.pattern + r')', str(query), re.I)
    target = str(language or '').lower()
    if target not in {'ladder', 'st', 'fbd'}:
        if explicit:
            target = canonical(explicit.group(1))
        elif mentioned:
            target = mentioned[-1] if rewrite else mentioned[0]
        else:
            target = fallback if fallback in {'ladder', 'st', 'fbd'} else 'ladder'
    if rewrite and len(mentioned) == 2:
        source = next((item for item in mentioned if item != target), target)
        return list(dict.fromkeys([source, target]))
    return [target]

_MISSING = object()


def resolve_form_value(value, form):
    """Read a concrete form from a factored presentation (no PLC evaluation)."""
    if isinstance(value, Mapping):
        if set(value) == {'by_form'}:
            selected = next((row.get('value', {}) for row in value['by_form'] if form in row.get('forms', [])), _MISSING)
            return _MISSING if selected is _MISSING else resolve_form_value(selected, form)
        result = {}
        for key, child in value.items():
            resolved = resolve_form_value(child, form)
            if resolved is not _MISSING:
                result[key] = resolved
        return result
    if isinstance(value, list):
        return [resolve_form_value(child, form) for child in value]
    return copy.deepcopy(value)


def factor_form_values(entries):
    """Factor equal fields; differing fields keep explicit literal selectors."""
    entries = [(list(forms), value) for forms, value in entries]
    first = entries[0][1]
    if all(_key(value) == _key(first) for _, value in entries):
        return copy.deepcopy(first)
    if all(isinstance(value, Mapping) for _, value in entries):
        keys = list(dict.fromkeys(key for _, value in entries for key in value))
        result = {}
        for key in keys:
            children = [(forms, value[key]) for forms, value in entries if key in value]
            if len(children) != len(entries):
                result[key] = {'by_form': [{'forms': forms, 'value': copy.deepcopy(value)} for forms, value in children]}
            else:
                result[key] = factor_form_values(children)
        return result
    if (all(isinstance(value, list) for _, value in entries)
            and all(len(value) == len(first) for _, value in entries)):
        return [factor_form_values([(forms, value[index]) for forms, value in entries])
                for index in range(len(first))]
    variants = {}
    for forms, value in entries:
        row = variants.setdefault(_key(value), {'forms': [], 'value': copy.deepcopy(value)})
        row['forms'] = list(dict.fromkeys([*row['forms'], *forms]))
    return {'by_form': list(variants.values())}


def _merge_components(components, *, default_forms, parameter_tables=False):
    output, index = [], {}
    for original in components:
        item = copy.deepcopy(original)
        scope = item.get('scope') or {}
        forms = item.pop('forms', None) or scope.pop('forms', None) or default_forms
        if not scope:
            item.pop('scope', None)
        keys = ['scope', 'conditions', 'knowledge_status', 'selection_applicability']
        if parameter_tables:
            keys.extend(['representation','declaration_kind'])
        context = {key: item.pop(key) for key in keys if key in item}
        marker = _key(context)
        prior = index.get(marker)
        if prior is not None and set(prior['forms']).intersection(forms):
            if all(_key(resolve_form_value(prior['value'], form)) == _key(resolve_form_value(item, form)) for form in forms if form in prior['forms']):
                forms = [form for form in forms if form not in prior['forms']]
                if not forms:
                    continue
            else:
                prior = None  # Conflicting same-form observations remain separate.
        if prior is None:
            prior = {**context, 'forms': list(forms), 'value': item}
            index[marker] = prior
            output.append(prior)
            continue
        entries = [([form], resolve_form_value(prior['value'], form)) for form in prior['forms']]
        entries.extend(([form], resolve_form_value(item, form)) for form in forms)
        prior['forms'] = list(dict.fromkeys([*prior['forms'], *forms]))
        prior['value'] = factor_form_values(entries)
    return [{**{key: value for key, value in item.items() if key != 'value'}, **item['value']} for item in output]


def substantive_effect(fact):
    """An operation name or operand role alone is not a result definition."""
    value = fact.get('value') or {}
    if not isinstance(value, Mapping):
        return False
    if value.get('knowledge_scope') == 'operation_summary':
        return False
    if value.get('unresolved_items') or value.get('full_source_relation_complete') is False:
        return False
    if value.get('behavior'):
        from plc.instruction_effects import validate_behavior
        from plc.instruction_definition import DefinitionError, UnknownInstructionSemantics
        try:
            validate_behavior(value)
            return True
        except (DefinitionError, UnknownInstructionSemantics):
            return False
    rules = value.get('rules')
    if isinstance(rules, list) and rules:
        return any(substantive_effect({'dimension': fact.get('dimension'), 'value': rule}) for rule in rules)
    if fact.get('dimension', '').startswith(('effects.error', 'effects.operation_error')):
        return False
    if value.get('outputs'):
        from plc.instruction_effects import validate_behavior
        from plc.instruction_definition import DefinitionError
        try:
            validate_behavior({'behavior': value.get('kind', 'expression_write'), 'outputs': value['outputs']})
            return True
        except (DefinitionError, TypeError, ValueError, KeyError):
            return False
    # These are source-authored relation structures, not mnemonic heuristics.
    # Each needs both its input/state binding and a concrete result relation.
    shapes = (
        ('source', 'destination', 'count_operand', 'element_bits'),
        ('inputs', 'output', 'operation'),
        ('source', 'target', 'operation'),
        ('source', 'target', 'from', 'to'),
        ('combination', 'operand_role', 'result_type'),
        ('operands', 'relation', 'result'),
        ('comparison_basis', 'result', 'result_owner'),
        ('device', 'trigger', 'pulse_state', 'active_cycles'),
        ('target', 'device_state_follows'),
        ('selection', 'target_state'),
        ('frequency_operand', 'count_operand', 'output'),
        ('source_operand', 'destination_operand', 'count_operand'),
    )
    def bound(item):
        return item is not None and item not in ('', 'unknown', 'unresolved', 'not_established')
    return any(all(key in value and bound(value[key]) for key in keys) for keys in shapes)


def _semantic_view(value, project):
    """Render existing typed expressions; preserve types and overflow policy."""
    parameters, intermediate = {}, []
    binary = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/', 'mod': '%',
              'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<=', 'eq': '==', 'ne': '!=',
              'and': 'AND', 'or': 'OR', 'bit_and': '&', 'bit_or': '|', 'bit_xor': '^',
              'shift_left': '<<', 'shift_right': '>>'}
    def expression(node):
        op, args = node['op'], node.get('args', [])
        if op in {'parameter', 'state', 'device'}:
            claim = {'name': node['name'], 'type': project(node['type']), 'kind': op}
            parameters.setdefault(_key(claim), claim)
            return node['name']
        if op == 'constant':
            return json.dumps(node['value'], ensure_ascii=False)
        rendered = [expression(arg) for arg in args]
        if op in binary and len(rendered) == 2:
            text = '(' + (' ' + binary[op] + ' ').join(rendered) + ')'
        elif op in {'not', 'bit_not'}:
            text = ('NOT ' if op == 'not' else '~') + '(' + rendered[0] + ')'
        else:
            text = op + '(' + ', '.join(rendered) + ')'
        if node['type'].get('kind') != 'bool':
            detail = {'expression': text, 'type': project(node['type'])}
            if node.get('overflow') not in (None, '', 'unknown', 'unresolved', 'not_established'):
                detail['overflow'] = node['overflow']
            intermediate.append(detail)
        return text
    def visit(item):
        if isinstance(item, Mapping):
            if 'op' in item and 'type' in item:
                from plc.instruction_definition import Expression, DefinitionError
                try:
                    Expression.from_mapping(item)
                except (DefinitionError, TypeError, ValueError, KeyError):
                    return project(item)
                return {'expression': expression(item), 'type': project(item['type'])}
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return item
    result = visit(project(value))
    if isinstance(result, dict):
        if parameters:
            result['expression_parameters'] = list(parameters.values())
        if intermediate:
            result['expression_types'] = _unique(intermediate)
    return result


def documented_knowledge(packet, *, model=None, language=None):
    value = packet.get('value') or {}
    scope = packet.get('scope') or {}
    model = model or value.get('target_model') or (packet.get('_knowledge_context') or {}).get('model') or scope.get('model')
    if not model and len(scope.get('models', [])) == 1:
        model = scope['models'][0]
    if not model:
        return None
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    return DEFAULT_INSTRUCTION_REGISTRY.instruction_knowledge(
        packet.get('target') or value.get('opcode'), cpu=model, language=language, facts=value.get('facts'),
        context=packet.get('_knowledge_context') or {})


def enrich_instruction_records(records, *, plc_model):
    """Add normal compiled family groups before task/dependency selection."""
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from plc.instruction_knowledge import compose_programming_facts
    output = []
    for original in records:
        row = copy.deepcopy(original)
        lines = str(row.get('text') or '').splitlines()
        for index, line in enumerate(lines):
            if not line.startswith('{'):
                continue
            try:
                packet = json.loads(line)
            except ValueError:
                continue
            value = packet.get('value') if isinstance(packet, Mapping) else None
            if (not isinstance(packet, Mapping) or packet.get('kind') != 'instruction' or not isinstance(value, dict)
                    or value.get('catalogue_only') or not isinstance(value.get('facts'), list)):
                continue
            owner_models = (packet.get('scope') or {}).get('models') or []
            owner_model = value.get('target_model') or (owner_models[0] if len(owner_models) == 1 else plc_model)
            value['facts'], replaced = compose_programming_facts(DEFAULT_INSTRUCTION_REGISTRY,
                packet.get('target'), cpu=owner_model, existing=value['facts'])
            if replaced:
                value.setdefault('superseded_facts', []).extend(replaced)
            lines[index] = json.dumps(packet, ensure_ascii=False, separators=(',', ':'))
            row['text'] = '\n'.join(lines)
            break
        output.append(row)
    return output


def _groups(facts, project, *, scope, parameter_bindings=None, relation_bindings=None, tables=(), folded=(), language=None):
    """Group equal contexts without discarding conditions or dependency edges."""
    groups = {}
    referenced = {key for fact in facts for key in fact.get('depends_on', [])}
    owner = next((f.get('scope', {}).get('forms', [None])[0] for f in facts
                  if f.get('scope', {}).get('forms')), 'instruction')
    local, claims = {}, {}
    for fact in facts:
        if not fact.get('id'):
            continue
        claim = {key: project(fact[key]) for key in
                 ('dimension', 'value', 'scope', 'conditions', 'status', 'verification', 'depends_on',
                  'requires', 'note', 'selection_applicability') if key in fact}
        claim['applicability_conditions'] = _source_conditions(fact, project)
        marker = _key(claim)
        local[fact['id']] = claims.setdefault(marker, str(owner) + '.fact-' + str(len(claims) + 1))
    for identity in sorted(referenced - local.keys()):
        local[identity] = str(owner) + '.fact-' + str(len(local) + 1)
    for fact in facts:
        if fact.get('id') in folded and fact.get('id') not in referenced and not fact.get('depends_on'):
            continue
        binding = (parameter_bindings or {}).get(fact.get('id'))
        context = {key: project(fact[key]) for key in
                   ('scope', 'conditions', 'selection_applicability') if fact.get(key)}
        source_conditions = _source_conditions(fact, project)
        if source_conditions:
            context['applicability_conditions'] = source_conditions
        context['knowledge_status'] = fact.get('status') or fact.get('knowledge_status', 'unknown')
        if context.get('selection_applicability') == 'applicable':
            context.pop('selection_applicability')
        if context.get('scope', {}).get('forms'):
            context['forms'] = context['scope'].pop('forms')
        if context.get('scope', {}).get('models') == (scope or {}).get('models'):
            context['scope'].pop('models')
        if language and language in context.get('scope', {}).get('languages', []) and fact.get('selection_applicability') != 'not_applicable':
            context['scope'].pop('languages')
        if not context.get('scope'):
            context.pop('scope', None)
        if context.get('scope') == scope:
            context.pop('scope')
        if binding and fact.get('id') not in referenced and not fact.get('depends_on'):
            table = next(table for table in tables if table['id'] == binding)
            table_context = {key: table[key] for key in
                ('scope', 'conditions', 'selection_applicability', 'knowledge_status', 'forms') if table.get(key)}
            if context == table_context:
                continue
        group = groups.setdefault(_key(context), {**context, 'semantics': {}})
        dimension = fact.get('dimension', '')
        relation = (relation_bindings or {}).get(fact.get('id'))
        item = ({'parameter_table': binding} if binding else {'instruction_relation': local[relation]}
                if relation else _semantic_view(fact.get('value'), project))
        if fact.get('id') in referenced or fact.get('depends_on') or fact.get('note') or fact.get('requires'):
            item = {'value': item}
            if fact.get('id'):
                item['id'] = local[fact['id']]
            if fact.get('depends_on'):
                item['depends_on'] = [local[key] for key in fact['depends_on']]
                missing = [local[key] for key in fact['depends_on'] if key not in {f.get('id') for f in facts}]
                if missing:
                    item['missing_prerequisites'] = missing
            for key in ('requires', 'note'):
                if key in fact:
                    item[key] = project(fact[key])
        bucket = group['semantics'].setdefault(dimension, [])
        if item not in bucket:
            bucket.append(item)
    # A single claim need not be wrapped in another array.
    for group in groups.values():
        group['semantics'] = {key: values[0] if len(values) == 1 else values
                              for key, values in group['semantics'].items()}
    return list(groups.values())


def _parameter_tables(facts, project):
    """Join scoped operand facets, retaining the status of every joined claim."""
    from plc.instruction_relations import fact_context_intersection, same_relation_interface, _STRENGTH
    tables, used, table_sources, facet_tables = [], {}, {}, {}
    facets = []
    def purpose_covers(richer, original):
        if not original or richer == original:
            return True
        if not isinstance(richer, Mapping) or set(richer) != {'uses'} or not isinstance(richer['uses'], list):
            return False
        if isinstance(original, str):
            return {'semantic_role': original} in richer['uses']
        return (isinstance(original, Mapping) and set(original) == {'uses'}
                and isinstance(original['uses'], list) and all(use in richer['uses'] for use in original['uses']))
    def same_table_context(left, right):
        if left.get('scope') != right.get('scope') or left.get('status') != right.get('status'):
            return False
        a, b = left.get('value', {}), right.get('value', {})
        if (not same_relation_interface(left, right)
                or a.get('variadic_parameters') != b.get('variadic_parameters')):
            return False
        first, second = left.get('conditions') or [], right.get('conditions') or []
        if first == second:
            return left.get('selection_applicability') == right.get('selection_applicability')
        # A literal native/structured interface already states its project mode.
        # Binding an effect to that same mode cannot create a second, empty
        # operand-order table. Other guards and unresolved prerequisites remain.
        representation = a.get('representation')
        if representation and representation == b.get('representation'):
            def outside_interface(conditions):
                return [condition for condition in conditions if condition !=
                        {'context':'project_mode','equals':representation}]
            if outside_interface(first) == outside_interface(second):
                return True
        return (left.get('selection_applicability') == right.get('selection_applicability') == 'applicable')
    def representation(fact):
        value = fact.get('value') or {}
        result = value.get('representation') or next((c.get('equals') for c in fact.get('conditions', [])
            if isinstance(c, Mapping) and c.get('context') == 'project_mode'), None)
        operands = value.get('operands') or []
        if not result and operands and all(p.get('representation') == 'native_operand' for p in operands):
            result = 'native_ladder_instruction'
        return result
    def can_reference_table(prior, order):
        if same_table_context(prior, {**order, 'status': prior['status']}):
            return True
        a, b = prior.get('value') or {}, order.get('value') or {}
        first, second = representation(prior), representation(order)
        return (prior.get('scope') == order.get('scope')
                and same_relation_interface(prior, order)
                and a.get('variadic_parameters') == b.get('variadic_parameters')
                and 'not_applicable' not in {prior.get('selection_applicability'), order.get('selection_applicability')}
                and not (first and second and first != second)
                and all(condition in prior.get('conditions', []) for condition in order.get('conditions', [])))
    def same_facet_context(source, order):
        return (source.get('scope') == order.get('scope')
                and source.get('status') == order.get('status')
                and (source.get('conditions') or []) == (order.get('conditions') or [])
                and source.get('selection_applicability') == order.get('selection_applicability')
                and _source_conditions(source, project) == _source_conditions(order, project))
    def matches_operand(source_position, source, operand, position, operands):
        symbol = (source.get('value') or {}).get('symbol')
        return (symbol.casefold() == str(operand.get('name', '')).casefold() if symbol else
                source_position == position and not any(p.get('name') == 'EN' for p in operands))
    def same_operand_order(parameters, names):
        return ([str(parameter.get('name', '')).casefold() for parameter in parameters]
                == [str(name).casefold() for name in names])
    for fact in facts:
        match = re.fullmatch(r'parameters\.(\d+)\.(purpose|range|condition|unit|encoding)', fact.get('dimension', ''))
        if match:
            facets.append((int(match[1]), match[2], fact))
    enrichment_fields = {'purpose', 'operation_value_type', 'write_layout', 'result_value_type', 'word_device_layout',
                         'semantic_role', 'semantic_access', 'value_semantics', 'occupied_bits_operand', 'occupied_bit_count', 'range', 'unit', 'encoding', 'components', 'condition', 'numeric_input_gate'}
    signatures = sorted(facts, key=lambda f: (f.get('dimension') != 'parameters.typed_operands',
        -sum(sum(key in p for key in enrichment_fields)
             for p in (f.get('value', {}).get('operands') or []) if isinstance(p, Mapping)),
        -sum(len(p['purpose'].get('uses', [])) for p in (f.get('value', {}).get('operands') or [])
             if isinstance(p, Mapping) and isinstance(p.get('purpose'), Mapping))))
    signature_tables = []
    for fact in signatures:
        value, dim = fact.get('value') or {}, fact.get('dimension')
        if dim == 'parameters.typed_operands':
            operands = copy.deepcopy(value.get('operands') or [])
            same = None
            for prior, table in signature_tables:
                if not same_table_context(prior, fact):
                    continue
                original, enriched = value.get('operands', []), prior.get('value', {}).get('operands', [])
                if (len(original) == len(enriched) and all(
                        all(key in b and item == b[key] for key, item in a.items() if key != 'purpose')
                        and set(b) - set(a) <= enrichment_fields
                        and purpose_covers(b.get('purpose'), a.get('purpose'))
                        for a, b in zip(original, enriched))):
                    same = table
                    break
            if same is not None:
                used[fact['id']] = same['id']
                continue
        elif dim in {'parameters.order', 'parameters.source_order'} and value.get('symbols'):
            names = value['symbols']
            same = next((table for table in tables
                         if same_operand_order(table['parameters'], names)
                         and same_table_context(table_sources[table['id']], fact)), None)
            if same is not None:
                used[fact['id']] = same['id']
                continue
            # An order can repeat facets already embedded, with their own
            # status/conditions, in one compatible table.
            # Its reference does not change either claim's qualifications.
            if set(value) <= {'symbols', 'representation', 'declaration_kind', 'binding', 'basis', 'types_and_directions'}:
                independent_order = (fact.get('status') == 'candidate_evidence'
                                     or fact.get('status') == 'source_verified'
                                     and fact.get('verification') == 'source_checked')
                compatible = [(prior, table) for prior, table in signature_tables
                    if same_operand_order(table['parameters'], names)
                    and prior.get('status') in _STRENGTH and fact.get('status') in _STRENGTH
                    and (_STRENGTH.index(prior['status']) >= _STRENGTH.index(fact['status'])
                         or independent_order and prior.get('status') in {'declared_unverified', 'candidate_evidence'}
                         and prior.get('verification') == 'source_checked')
                    and can_reference_table(prior, fact)]
                if len(compatible) == 1:
                    prior, table = compatible[0]
                    covered = []
                    for position, operand in enumerate(table['parameters'], start=1):
                        claims = [source for source_position, _, source in facets
                            if matches_operand(source_position, source, operand, position, table['parameters'])
                            and fact_context_intersection(source, fact) is not None]
                        if (independent_order and prior['status'] in {'declared_unverified', 'candidate_evidence'}
                                and prior.get('verification') == 'source_checked'):
                            # The order retains its own group qualification;
                            # the table keeps its partial type status.
                            covered.append(all(table['id'] in facet_tables.get(source['id'], ()) for source in claims))
                            continue
                        covered.append(bool(claims) and (prior['status'] == fact['status']
                            or any(same_facet_context(source, fact) for source in claims))
                            and all(table['id'] in facet_tables.get(source['id'], ()) for source in claims))
                    if covered and all(covered):
                        used[fact['id']] = table['id']
                        continue
            operands = [{'name': name} for name in names]
        else:
            continue
        if not isinstance(operands, list):
            continue
        table_id = str((fact.get('scope', {}).get('forms') or ['instruction'])[0]) + '.parameters-' + str(len(tables) + 1)
        for position, operand in enumerate(operands, start=1):
            operand.setdefault('position', position)
            for source_position, facet, source in facets:
                source_value = source.get('value') or {}
                symbol = source_value.get('symbol')
                if symbol:
                    matches = symbol.casefold() == str(operand.get('name', '')).casefold()
                else:
                    matches = source_position == position and not any(p.get('name') == 'EN' for p in operands)
                if not matches:
                    continue
                # A symbol can occur in several language/interface signatures.
                # Never attach a facet from a disjoint interface to this table.
                source_scope, table_scope = source.get('scope', {}), fact.get('scope', {})
                if fact_context_intersection(source, fact) is None:
                    continue
                facet_tables.setdefault(source['id'], set()).add(table_id)
                claim = project(source_value.get('value', source_value))
                qualifiers = {key: project(source[key]) for key in ('conditions', 'selection_applicability') if source.get(key)}
                source_conditions = _source_conditions(source, project)
                if source_conditions:
                    qualifiers['applicability_conditions'] = source_conditions
                if source_scope != table_scope:
                    qualifiers['scope'] = project(source_scope)
                if source.get('status') != fact.get('status'):
                    qualifiers['knowledge_status'] = source.get('status', 'unknown')
                if qualifiers.get('selection_applicability') == 'applicable':
                    qualifiers.pop('selection_applicability')
                # A purpose's source classification and its location are
                # separate columns, with independently retained qualifications.
                if facet == 'purpose' and isinstance(claim, Mapping) and claim.get('semantic_role') == operand.get('purpose'):
                    claim = {key: item for key, item in claim.items() if key != 'semantic_role'}
                    if claim:
                        operand.setdefault('qualifications', []).append({'value': claim, **qualifiers})
                    used[source['id']] = table_id
                    continue
                qualified = {'value': claim, **qualifiers} if qualifiers else claim
                if facet not in operand:
                    operand[facet] = qualified
                elif operand[facet] != qualified:
                    operand.setdefault('facet_alternatives', {}).setdefault(facet, []).append(qualified)
                used[source['id']] = table_id
        table = {key: project(fact[key]) for key in ('scope', 'conditions', 'selection_applicability') if fact.get(key)}
        source_conditions = _source_conditions(fact, project)
        if source_conditions:
            table['applicability_conditions'] = source_conditions
        if table.get('selection_applicability') == 'applicable':
            table.pop('selection_applicability')
        table.update(id=table_id, knowledge_status=fact.get('status', 'unknown'),
                     representation=representation(fact) or 'documented_interface', parameters=project(operands))
        if value.get('declaration_kind'):
            table['declaration_kind'] = value['declaration_kind']
        attributes = {key: project(item) for key, item in value.items()
                      if key not in {'operands', 'operand_count', 'symbols', 'representation', 'binding', 'basis',
                                     'executor_capability', 'admission_effect', 'declaration_kind'}}
        if attributes:
            table['qualifications'] = attributes
        tables.append(table)
        table_sources[table_id] = fact
        if dim == 'parameters.typed_operands':
            signature_tables.append((fact, table))
        used[fact['id']] = table_id
    # Roles and types that repeat a richer table add no programming information.
    direction_role = {'input': 'read', 'output': 'write', 'in_out': 'read_write'}
    for fact in facts:
        dim, value = fact.get('dimension'), fact.get('value') or {}
        for table in tables:
            source = table_sources[table['id']]
            if not same_table_context(source, fact):
                continue
            parameters = table['parameters']
            if dim == 'parameters.arity' and value.get('minimum') == value.get('maximum') == len(parameters):
                used[fact['id']] = table['id']
            if dim == 'parameters.roles' and value.get('roles') == [direction_role.get(p.get('direction'), p.get('role')) for p in parameters]:
                used[fact['id']] = table['id']
    return tables, used


def instruction_card(packet, project):
    raw = packet.get('value') or {}
    selection = packet.get('fact_selection') or {}
    context = packet.get('_knowledge_context') or {}
    delivery_languages = context.get('delivery_languages') or [context.get('language') or selection.get('language') or 'ladder']
    knowledge = documented_knowledge(packet, language=context.get('language') or selection.get('language')) or {}
    facts = raw.get('facts') or []
    model = raw.get('target_model') or context.get('model')
    selected_models = [model] if model else (packet.get('scope') or {}).get('models', [])
    if selected_models:
        from plc.target_identity import target_identity_state
        members = [target_identity_state(item)[0] for item in selected_models]
        if all(members):
            models = {member for group in members for member in group}
            facts = copy.deepcopy(facts)
            for fact in facts:
                states = (fact.get('value') or {}).get('model_states')
                if (fact.get('dimension') == 'constraints.cpu_availability'
                        and isinstance(states, Mapping) and models <= states.keys()):
                    fact['value']['model_states'] = {key: state for key, state in states.items() if key in models}
    family = knowledge.get('family') or raw.get('opcode') or packet.get('target')
    opcode = raw.get('opcode') or packet.get('target')
    form = {key: project(knowledge[key]) for key in
            ('opcode', 'modifiers', 'data_bits', 'execution', 'common_rules', 'conditions', 'languages', 'binding_scopes', 'knowledge_status', 'binding_status') if key in knowledge}
    form.setdefault('opcode', opcode)
    if 'call_available' in raw:
        form['core_call_available'] = raw['call_available']
    value = {'family': family, 'forms': [form]}
    tables, parameter_bindings = _parameter_tables(facts, project)
    if tables:
        for table in tables:
            table_scope = table.get('scope') or {}
            table['forms'] = table_scope.pop('forms', [opcode])
            if table_scope.get('models') == (packet.get('scope') or {}).get('models'):
                table_scope.pop('models')
            language = context.get('language') or selection.get('language')
            if len(delivery_languages) == 1 and language and language in table_scope.get('languages', []) and table.get('selection_applicability') != 'not_applicable':
                table_scope.pop('languages')
            if not table_scope:
                table.pop('scope', None)
        value['parameter_tables'] = tables
    folded = set()
    for fact in facts:
        dimension, claim = fact.get('dimension'), fact.get('value') or {}
        qualifications = {key: project(fact[key]) for key in
                          ('scope', 'conditions', 'selection_applicability') if fact.get(key)}
        source_conditions = _source_conditions(fact, project)
        if source_conditions:
            qualifications['applicability_conditions'] = source_conditions
        if qualifications.get('selection_applicability') == 'applicable':
            qualifications.pop('selection_applicability')
        if dimension in {'identity.form', 'identity.literal_mnemonic'} and claim.get('opcode') == opcode:
            form.setdefault('identity_knowledge', []).append({
                'knowledge_status': fact.get('status', 'unknown'),
                **qualifications})
            folded.add(fact['id'])
        if dimension in {'identity.applicability', 'identity.source_model_mark'}:
            form.setdefault('applicability', []).append({'value': project(claim),
                'knowledge_status': fact.get('status', 'unknown'),
                **qualifications})
            folded.add(fact['id'])
        if fact.get('status') == 'unknown' and dimension in {'parameters.types', 'parameters.devices'}:
            array = claim.get('types') if dimension == 'parameters.types' else claim.get('prefixes')
            if array is not None and all(item in (None, 'any', 'unknown', []) for item in array):
                form.setdefault('core_unresolved_aspects', []).append(dimension)
                folded.add(fact['id'])
    relation_bindings = {}
    by_id = {fact['id']: fact for fact in facts}
    for fact in facts:
        if fact.get('dimension') != 'effects.bound_relation' or not fact['id'].startswith('assembly:relation:'):
            continue
        origin = by_id.get((fact.get('depends_on') or [None])[0])
        if not origin or any(origin.get(key, [] if key == 'conditions' else None) != fact.get(key, [] if key == 'conditions' else None)
                             for key in ('scope', 'conditions', 'status', 'selection_applicability')):
            continue
        if all(key in fact['value'] and (key in {'inputs', 'output'} or fact['value'][key] == item)
               for key, item in origin.get('value', {}).items()):
            relation_bindings.setdefault(origin['id'], fact['id'])
    extra = _groups(facts, project, scope=packet.get('scope'), parameter_bindings=parameter_bindings,
                    relation_bindings=relation_bindings, tables=tables, folded=folded,
                    language=(context.get('language') or selection.get('language')) if len(delivery_languages) == 1 else None)
    if extra:
        value['additional_knowledge'] = extra
    for key in ('call_admission_reason', 'call_admission_details', 'support_status'):
        if key in raw:
            form[key] = project(raw[key])
    if raw.get('gaps'):
        form['core_unresolved_aspects'] = list(dict.fromkeys([*form.get('core_unresolved_aspects', []), *project(raw['gaps'])]))
    if raw.get('conflicts'):
        value['conflicts'] = project(raw['conflicts'])
    # Related instruction bodies remain independent, scoped knowledge; they
    # are not silently merged into this family's behavior.
    for key, item in raw.items():
        if key not in {'facts', 'opcode', 'target_model', 'vendor', 'registry_member', 'call_available',
                       'semantic_completeness', 'gaps', 'conflicts', 'source_materials', 'uninterpreted_content',
                       'call_admission_reason', 'call_admission_details', 'support_status', 'superseded_facts'}:
            value[key] = project(item)
    result = {key: project(packet[key]) for key in ('kind', 'target', 'scope') if key in packet}
    if model:
        result['scope'] = {'models': [model]}
    result['context'] = {key: context.get(key, selection.get(key)) for key in ('language', 'project_mode', 'version', 'aspects')}
    if len(delivery_languages) == 2:
        result['context'].pop('language', None)
        result['context']['languages'] = delivery_languages
    result['value'] = value
    modules = knowledge.get('knowledge_modules') or ([knowledge['knowledge_module']] if knowledge.get('knowledge_module') else [])
    if modules:
        result['knowledge_modules'] = [project(module) for module in modules]
    return result


def _semantic_values(value):
    """Mutable payload locations; qualification and prerequisite edges stay out."""
    for group in value.get('additional_knowledge', []):
        semantics = group.get('semantics', {})
        for dimension, bucket in semantics.items():
            rows = enumerate(bucket) if isinstance(bucket, list) else [(dimension, bucket)]
            for key, row in rows:
                container = bucket if isinstance(bucket, list) else semantics
                if isinstance(row, Mapping) and 'value' in row:
                    container, key = row, 'value'
                yield group, dimension, container, key


def _bind_semantic_roles(value, bindings):
    if isinstance(value, Mapping):
        if (set(value) == {'parameter_role'} and isinstance(value['parameter_role'], str)
                and value['parameter_role'] in bindings):
            return bindings[value['parameter_role']]
        return {key: _bind_semantic_roles(child, bindings) for key, child in value.items()}
    if isinstance(value, list):
        return [_bind_semantic_roles(child, bindings) for child in value]
    return copy.deepcopy(value)


def _semantic_template(payload, group, tables):
    """Use explicit, unique parameter roles; never infer aliases from spelling."""
    contexts = ('scope', 'conditions', 'selection_applicability', 'applicability_conditions')
    candidates = []
    for table in tables:
        if (set(table.get('forms', [])) != set(group.get('forms', []))
                or any(_key(table.get(key)) != _key(group.get(key)) for key in contexts)):
            continue
        parameters = table.get('parameters', [])
        roles = [p.get('semantic_role') for p in parameters if isinstance(p.get('semantic_role'), str)]
        names = [p.get('name') for p in parameters]
        candidates.append({p['name']: p['semantic_role'] for p in parameters
                           if isinstance(p.get('name'), str) and isinstance(p.get('semantic_role'), str)
                           and roles.count(p['semantic_role']) == 1 and names.count(p['name']) == 1})
    names = candidates[0] if candidates and all(row == candidates[0] for row in candidates) else {}
    used = {}
    def visit(item):
        if isinstance(item, str) and item in names:
            used[names[item]] = item
            return {'parameter_role': names[item]}
        if isinstance(item, Mapping):
            return {key: visit(child) for key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        return copy.deepcopy(item)
    template = visit(payload)
    # Existing literal objects must not be mistaken for a role placeholder.
    if _key(_bind_semantic_roles(template, used)) != _key(payload):
        return copy.deepcopy(payload), {}
    return template, used


def expand_semantic_references(unit):
    """Recover full claims for exact delivery reconciliation, including statuses."""
    result = copy.deepcopy(unit)
    value = result.get('value') or {}
    if result.get('kind') != 'instruction' or not isinstance(value, dict):
        return result
    definitions = value.pop('shared_semantics', {})
    for _, _, container, key in _semantic_values(value):
        payload = container[key]
        if (not isinstance(payload, Mapping) or 'semantic_ref' not in payload
                or not set(payload) <= {'semantic_ref', 'parameter_bindings', 'operand_binding'}):
            continue
        definition = definitions.get(payload['semantic_ref'])
        if isinstance(definition, Mapping) and 'value' in definition:
            bindings = payload.get('parameter_bindings')
            restored = _bind_semantic_roles(definition['value'], bindings if isinstance(bindings, Mapping) else {})
            if 'operand_binding' in payload:
                if (not isinstance(payload['operand_binding'], Mapping) or not isinstance(restored, dict)
                        or 'operand_binding' in restored):
                    continue
                restored['operand_binding'] = copy.deepcopy(payload['operand_binding'])
            container[key] = restored
    return result


def _share_semantic_values(unit):
    """Share equal behavior bodies, retaining each qualified claim and its DAG."""
    if not isinstance(unit, Mapping):
        return unit
    value = unit.get('value') or {}
    if unit.get('kind') != 'instruction' or not isinstance(value, Mapping):
        return unit
    groups = {}
    for group, dimension, container, key in _semantic_values(value):
        payload = container[key]
        if not isinstance(payload, (Mapping, list)):
            continue
        body, interface = payload, {}
        if isinstance(payload, Mapping) and isinstance(payload.get('operand_binding'), Mapping):
            body = {name: item for name, item in payload.items() if name != 'operand_binding'}
            interface['operand_binding'] = copy.deepcopy(payload['operand_binding'])
        template, bindings = _semantic_template(body, group, value.get('parameter_tables', []))
        groups.setdefault(_key([dimension, template]), []).append((container, key, payload, template, bindings, interface))
    shared = {}
    for marker, entries in groups.items():
        if len(entries) < 2:
            continue
        identity = 'semantic-' + str(len(shared) + 1)
        definition = {'dimension': json.loads(marker)[0], 'value': entries[0][3]}
        references = [{'semantic_ref': identity, 'parameter_bindings': entry[4], **entry[5]} for entry in entries]
        if len(_key({identity: definition})) + sum(len(_key(ref)) for ref in references) >= sum(len(_key(entry[2])) for entry in entries):
            continue  # Short claims read more clearly inline.
        shared[identity] = definition
        for (container, key, _, _, _, _), reference in zip(entries, references):
            container[key] = reference
    if shared:
        value['shared_semantics'] = shared
    return unit


def merge_instruction_units(units):
    """Deduplicate modules and families for a complete response/message."""
    output, modules, families = [], {}, {}
    for original in units:
        if not isinstance(original, Mapping):
            output.append(original)
            continue
        unit = expand_semantic_references(original)
        if unit.get('kind') in SHARED_INSTRUCTION_KINDS:
            modules.setdefault(_key(unit), unit)
            continue
        for module in unit.pop('knowledge_modules', []):
            modules.setdefault(_key(module), module)
        value = unit.get('value') or {}
        if unit.get('kind') != 'instruction' or not isinstance(value, Mapping) or 'family' not in value:
            output.append(unit)
            continue
        # Facts with different language/task selections never lose their scope.
        marker = _key([value['family'], unit.get('scope'), unit.get('context')])
        prior = families.get(marker)
        if prior is None:
            families[marker] = unit
            output.append(unit)
            continue
        previous = prior['value']
        old_forms = list(dict.fromkeys(form['opcode'] for form in previous.get('forms', [])))
        new_forms = list(dict.fromkeys(form['opcode'] for form in value.get('forms', [])))
        outer_keys = {'kind', 'target', 'targets', 'scope', 'context', 'value'}
        if any(_key(resolve_form_value({k: v for k, v in prior.items() if k not in outer_keys}, form))
               != _key(resolve_form_value({k: v for k, v in unit.items() if k not in outer_keys}, form))
               for form in set(old_forms).intersection(new_forms)):
            # Two qualifications of the same literal form are observations,
            # not different D/P forms. Keep both instead of choosing a status.
            output.append(unit)
            continue
        previous['forms'] = _unique([*previous.get('forms', []), *value.get('forms', [])])
        forms = list(dict.fromkeys(form['opcode'] for form in previous['forms']))
        for key in ('additional_knowledge', 'parameter_tables'):
            if key in value:
                previous[key] = _merge_components([*previous.get(key, []), *value[key]],
                    default_forms=forms, parameter_tables=key == 'parameter_tables')
        component_keys = {'forms', 'additional_knowledge', 'parameter_tables'}
        if not set(old_forms).intersection(new_forms):
            # Preserve absent fields as well as differing values. This applies
            # to selection/unknown states too, not only parameter widths.
            for left, right, excluded in ((previous, value, component_keys), (prior, unit, outer_keys)):
                original = {key: item for key, item in left.items() if key not in excluded}
                incoming = {key: item for key, item in right.items() if key not in excluded}
                entries = [([form], resolve_form_value(original, form)) for form in old_forms]
                entries.extend(([form], resolve_form_value(incoming, form)) for form in new_forms)
                factored = factor_form_values(entries)
                for key in list(left):
                    if key not in excluded:
                        del left[key]
                left.update(factored)
        else:
            for key, item in value.items():
                if key not in previous:
                    previous[key] = item
                elif key not in component_keys and item != previous[key]:
                    alternatives = previous.setdefault('scoped_differences', [])
                    difference = {'forms': new_forms, key: item}
                    if difference not in alternatives:
                        alternatives.append(difference)
        targets = prior.setdefault('targets', [prior['target']])
        if unit.get('target') not in targets:
            targets.append(unit.get('target'))
    return [*modules.values(), *(_share_semantic_values(unit) for unit in output)]


def model_instruction_view(unit):
    """Deliver effective interfaces and unique programming claims, not their DAG."""
    if unit.get('kind') != 'instruction' or 'family' not in (unit.get('value') or {}):
        return copy.deepcopy(unit)
    unit = expand_semantic_references(unit)
    value, context = unit['value'], unit.get('context') or {}
    languages = context.get('languages') or [context.get('language') or 'ladder']
    mode = context.get('project_mode') or ('native_ladder_instruction' if languages == ['ladder'] else None)
    forms = [row['opcode'] for row in value.get('forms', [])]
    metadata = {'basis', 'evidence_kind', 'role_basis', 'family_basis', 'availability_effect',
                'admission_effect', 'knowledge_scope', 'section_variant_count', 'fnc_number',
                'footnotes', 'clause', 'parameter_binding', 'knowledge_kind'}
    def clean(item):
        if isinstance(item, list):
            return [entry for child in item if (entry := clean(child)) not in (None, {}, [])]
        if not isinstance(item, Mapping):
            return copy.deepcopy(item)
        if set(item) == {'value'}:
            return clean(item['value'])
        result = {}
        for key, child in item.items():
            if key in metadata:
                continue
            if key == 'forms' and child == forms:
                continue
            if key == 'word_device_convention' and child == {'applies_to': '16_bit_word_devices',
                    'device_unit_bits': 16, 'high_words_follow': True, 'low_word_offset': 0}:
                continue
            if key in {'minimum_by_model', 'minimum_cpu_versions', 'model_states'} and isinstance(child, Mapping) and unit.get('scope', {}).get('models'):
                child = {model: state for model, state in child.items() if model in unit.get('scope', {}).get('models', [])}
            if key == 'conditions':
                child = [condition for condition in child if not (
                    isinstance(condition, Mapping) and condition.get('context') == 'project_mode'
                    and condition.get('equals') == mode)]
            if key == 'scope' and isinstance(child, Mapping):
                child = dict(child)
                if child.get('languages'):
                    child['languages'] = [language for language in child['languages'] if language in languages]
                child = {k: v for k, v in child.items() if not (
                    (k == 'models' and v == unit.get('scope', {}).get('models')) or
                    (k == 'languages' and set(v) == set(languages)) or
                    (k == 'forms' and v == forms))}
            projected = clean(child)
            if projected not in (None, {}, []):
                result[key] = projected
        return result
    tables = []
    for source in value.get('parameter_tables', []):
        scope = source.get('scope') or {}
        table_languages = scope.get('languages') or [context.get('language') or 'ladder']
        selected = [language for language in languages if language in table_languages]
        if not selected:
            continue
        representation = source.get('representation')
        for language in selected:
            preferred = mode or ('native_ladder_instruction' if language == 'ladder' else None)
            if language == 'ladder' and representation in {'native_ladder_instruction', 'structured_ladder_block'} and representation != preferred:
                continue
            table = clean(source)
            table.pop('id', None)
            table.pop('forms', None)
            table['language'] = language
            for parameter in table.get('parameters', []):
                if 'role' in parameter and parameter.get('role') == parameter.get('purpose'):
                    parameter.pop('role')
            tables.append(table)
    value['parameter_tables'] = _unique(tables)
    parameters = [parameter for table in tables for parameter in table.get('parameters', [])]
    complete_table = bool(parameters) and all(parameter.get('purpose') and parameter.get('type') for parameter in parameters)
    def selected_group(group):
        if any(str(condition.get('context', '')).endswith(('_not_resolved', '_unresolved')) for condition in group.get('conditions', [])):
            return False
        if mode and any(condition.get('context') == 'project_mode' and condition.get('equals') not in (None, mode)
                        for condition in group.get('conditions', [])):
            return False
        return not group.get('scope', {}).get('languages') or bool(set(languages).intersection(group['scope']['languages']))
    has_outputs = any(rule.get('outputs') for group in value.get('additional_knowledge', [])
        if selected_group(group)
        for dimension, claim in group.get('semantics', {}).items() if dimension == 'effects.documented_processing'
        for entry in (claim if isinstance(claim, list) else [claim])
        if isinstance(body := clean(entry), Mapping)
        for rule in body.get('rules', []) if isinstance(rule, Mapping))
    def contains(container, claim):
        if isinstance(claim, Mapping):
            return isinstance(container, Mapping) and all(key in container and contains(container[key], child) for key, child in claim.items())
        return container == claim
    def parameter_covers(name, claim):
        matches = [parameter for parameter in parameters if parameter.get('name') == name]
        return bool(matches) and all(contains(parameter, claim) for parameter in matches)
    def is_table_reference(item):
        if isinstance(item, list):
            return bool(item) and all(is_table_reference(child) for child in item)
        return isinstance(item, Mapping) and bool(item) and (set(item) == {'parameter_table'} or
            set(item) == {'value'} and is_table_reference(item['value']))
    groups = []
    for original in value.get('additional_knowledge', []):
        if not selected_group(original):
            continue
        group = clean({key: child for key, child in original.items() if key != 'semantics'})
        semantics = {}
        for dimension, original_claim in original.get('semantics', {}).items():
            if is_table_reference(original_claim):
                continue
            claim = clean(original_claim)
            if dimension in {'identity.instruction_class', 'parameters.variant_family'}:
                continue
            if complete_table and dimension in {'parameters.connection_role', 'parameters.operand_roles'}:
                continue
            if complete_table and dimension in {'parameters.fbd_signature', 'parameters.st_signature', 'parameters.ladder_signature'}:
                if isinstance(claim, Mapping) and any(len(claim.get('parameters', [])) == len(table.get('parameters', []))
                        and all(contains(actual, expected) for actual, expected in zip(table['parameters'], claim['parameters'])) for table in tables):
                    claim = {key: child for key, child in claim.items() if key != 'parameters'}
                    for key in ('call_form', 'declaration_kind', 'representation'):
                        if any(claim.get(key) == table.get(key, (table.get('qualifications') or {}).get(key)) for table in tables):
                            claim.pop(key, None)
                    if claim.get('available') is True:
                        claim.pop('available')
                    if 'enable' in claim and any(parameter.get('enable_pin') for parameter in parameters):
                        claim.pop('enable')
                    if claim.get('returns') == {'role': 'outputs_via_pins'} and any(parameter.get('direction') == 'output' for parameter in parameters):
                        claim.pop('returns')
            if dimension == 'parameters.roles' and isinstance(claim, Mapping) and any(claim.get('roles') == [
                    {'input': 'read', 'output': 'write', 'in_out': 'read_write'}.get(parameter.get('direction'), parameter.get('role'))
                    for parameter in table.get('parameters', [])] for table in tables):
                continue
            if complete_table and dimension in {'effects.source_read', 'effects.destination_write'} and isinstance(original_claim, Mapping) and original_claim.get('basis') == 'documented_operand_role':
                continue
            if dimension == 'parameters.usage' and isinstance(claim, (Mapping, list)):
                retained = []
                for usage in claim if isinstance(claim, list) else [claim]:
                    usage = copy.deepcopy(usage)
                    usage['parameter_usage'] = [entry for entry in usage.get('parameter_usage', [])
                        if not parameter_covers(entry.get('parameter'), {key: child for key, child in entry.items() if key != 'parameter'})]
                    if usage['parameter_usage']:
                        retained.append(usage)
                claim = retained
            if dimension in {'execution.form', 'execution.source_form'} and complete_table:
                continue
            if dimension == 'parameters.source_width' and isinstance(claim, Mapping) and any(
                    isinstance(parameter.get('type'), Mapping) and parameter['type'].get('kind') == 'int' for parameter in parameters) and all(
                    parameter.get('type', {}).get('bits') == claim.get('bits') for parameter in parameters
                    if isinstance(parameter.get('type'), Mapping) and parameter['type'].get('kind') == 'int'):
                continue
            if dimension == 'execution.trigger' and not group.get('conditions') and value.get('forms') and all(
                    contains(form.get('execution', {}), resolve_form_value(claim, form['opcode'])) for form in value['forms']):
                continue
            if dimension == 'effects.documented_operation' and has_outputs:
                continue
            if dimension == 'constraints.cpu_availability' and isinstance(claim, Mapping) and claim.get('available') is True and value.get('forms') and all(any(
                    clean(entry).get('value', clean(entry)).get('available') is True for entry in form.get('applicability', []))
                    for form in value.get('forms', [])):
                continue
            if dimension == 'identity.applicability' and all(any(
                    clean(entry).get('value', clean(entry)) == claim and clean(entry).get('conditions', []) == group.get('conditions', [])
                    for entry in form.get('applicability', [])) for form in value.get('forms', [])):
                continue
            if dimension == 'effects.documented_processing' and isinstance(claim, Mapping):
                claim['expression_parameters'] = [parameter for parameter in claim.get('expression_parameters', [])
                    if not parameter_covers(parameter.get('name'), {'type': parameter.get('type')})
                    and not parameter_covers(parameter.get('name'), {'operation_value_type': parameter.get('type')})]
                if not claim['expression_parameters']:
                    claim.pop('expression_parameters')
            if dimension == 'memory.result_layout' and isinstance(claim, Mapping) and not group.get('conditions') and not group.get('scope'):
                if parameter_covers(claim.get('head_operand'), {'occupied_bit_count': claim.get('bit_count')}) and set(claim) <= {'head_operand', 'bit_count', 'exclusive_use'}:
                    if 'exclusive_use' in claim:
                        for parameter in parameters:
                            if parameter.get('name') == claim.get('head_operand'):
                                parameter['exclusive_use'] = claim['exclusive_use']
                    continue
            if dimension == 'parameters.source_qualifications' and isinstance(claim, Mapping):
                claim = [{key: child for key, child in row.items() if key in {'allowed_models', 'code', 'device_kinds', 'rule'}}
                         for row in claim.get('qualifications', [])]
                dimension = 'constraints.device_models'
            if claim not in (None, {}, []):
                semantics[dimension] = claim
        if semantics:
            group['semantics'] = semantics
            groups.append(group)
    # Equal qualified claims become one entry even when they came from separate
    # source facts or source tables. Conditions and true language differences stay.
    merged = {}
    for group in groups:
        qualifications = {key: child for key, child in group.items() if key != 'semantics'}
        bucket = merged.setdefault(_key(qualifications), {**qualifications, 'semantics': {}})['semantics']
        for dimension, claim in group['semantics'].items():
            entries = claim if isinstance(claim, list) else [claim]
            bucket.setdefault(dimension, []).extend(entries)
    for group in merged.values():
        group['semantics'] = {dimension: unique[0] if len(unique := _unique(claims)) == 1 else unique
                              for dimension, claims in group['semantics'].items()}
    value['additional_knowledge'] = list(merged.values())
    value.pop('shared_semantics', None)
    compact_forms = []
    for form in value.get('forms', []):
        fields = ('opcode', 'modifiers', 'execution', 'applicability') if complete_table else ('opcode', 'modifiers', 'data_bits', 'execution', 'applicability')
        compact = clean({key: form[key] for key in fields if key in form})
        execution = compact.get('execution', {})
        for key, generic in {'evaluation_frequency': 'caller_control_flow', 'when_condition_false': 'not_executed',
                             'rearm': 'execution_condition_false_then_true'}.items():
            if execution.get(key) == generic:
                execution.pop(key)
        if form.get('modifiers', {}).get('pulse') and execution.get('executions_per_edge') == 1:
            execution.pop('executions_per_edge')
        for applicable in compact.get('applicability', []):
            claim = applicable.get('value', {})
            minimum = claim.get('minimum_cpu_versions')
            if minimum and any(condition.get('minimum_by_model') == minimum for condition in applicable.get('conditions', [])):
                claim.pop('minimum_cpu_versions')
        compact_forms.append(compact)
    value['forms'] = _unique(compact_forms)
    return unit


def instruction_form_units(unit):
    """Recover literal form views for lossless delivery reconciliation."""
    unit = expand_semantic_references(unit)
    value = unit.get('value') or {}
    if (unit.get('kind') != 'instruction' or not isinstance(value, Mapping)
            or 'family' not in value or not value.get('forms')):
        return [copy.deepcopy(unit)]
    result = []
    for form in dict.fromkeys(row['opcode'] for row in value.get('forms', [])):
        item = resolve_form_value(unit, form)
        item.pop('targets', None)
        item['target'] = form
        item['value']['forms'] = [row for row in item['value'].get('forms', []) if row['opcode'] == form]
        for key in ('additional_knowledge', 'parameter_tables'):
            components = []
            for component in value.get(key, []):
                forms = component.get('forms') or component.get('scope', {}).get('forms') or [form]
                if form not in forms:
                    continue
                concrete = resolve_form_value(component, form)
                concrete['forms'] = [form]
                components.append(concrete)
            if key in item['value']:
                item['value'][key] = components
        result.append(item)
    return result
