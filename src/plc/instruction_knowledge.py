"""Documented instruction families, composed by the existing form registry.

These are programming knowledge, not executable effects or call promotions.
Names and modifier combinations come from the registry or explicit documented
family bindings. Neither route changes call admission.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Mapping


def programming_system_prompt():
    """Use the maintained skill rules once in the built-in system prompt."""
    from shared.paths import resource_path
    text = resource_path('skills/gxworks-mcp-router/SKILL.md').read_text(encoding='utf-8')
    return text.split('<!-- mitsubishi-programming-system:start -->', 1)[1].split(
        '<!-- mitsubishi-programming-system:end -->', 1)[0].strip()

def validate_knowledge_profiles(profiles, *, vendor):
    if not isinstance(profiles, list):
        raise ValueError('knowledge_profiles must be an array')
    result = []
    for profile in profiles:
        if (not isinstance(profile, Mapping) or not profile.get('id')
                or profile.get('knowledge_status') != 'source_verified'
                or not profile.get('models') or not profile.get('sources')
                or not isinstance(profile.get('modifiers', {}), Mapping)):
            raise ValueError('Instruction knowledge needs explicit scope and sources')
        for rule in profile.get('execution_rules', []):
            if (not isinstance(rule, Mapping) or not rule.get('id') or not rule.get('models')
                    or not rule.get('sources') or rule.get('knowledge_status') != 'source_verified'
                    or not isinstance(rule.get('applies_to'), Mapping) or not rule['applies_to']
                    or not isinstance(rule.get('rules'), Mapping) or not rule['rules']
                    or not set(rule['models']) <= set(profile['models'])):
                raise ValueError('Shared execution knowledge needs explicit scope, interface and sources')
        item = copy.deepcopy(dict(profile))
        item['vendor'] = vendor
        result.append(item)
    return result


def validate_family_knowledge(definitions, *, vendor):
    """Use ordinary InstructionFactGroup records for shared family data."""
    from plc.instruction_definition import InstructionFactGroup
    if not isinstance(definitions, list):
        raise ValueError('instruction_families must be an array')
    definitions = copy.deepcopy(definitions)
    families = {d.get('base_opcode'): d for d in definitions if isinstance(d, Mapping)}
    if len(families) != len(definitions):
        raise ValueError('Instruction families need unique bases')
    if any(not isinstance(d.get('shared_values', {}), Mapping) for d in definitions):
        raise ValueError('Family shared values must be an object')
    shared_sources = {name: copy.deepcopy(d.get('shared_values', {})) for name, d in families.items()}
    for definition in definitions:
        for name, value in list(definition.get('shared_values', {}).items()):
            if not isinstance(value, Mapping) or 'shared_ref' not in value:
                continue
            ref = value['shared_ref']
            if (set(value) != {'shared_ref'} or not isinstance(ref, Mapping)
                    or set(ref) != {'family', 'name'} or not all(isinstance(v, str) for v in ref.values())):
                raise ValueError('Shared relation reference needs an explicit family and name')
            original = shared_sources.get(ref['family'], {}).get(ref['name'])
            if not isinstance(original, Mapping) or 'shared_ref' in original:
                raise ValueError('Shared relation reference needs a concrete existing body')
            definition['shared_values'][name] = copy.deepcopy(original)
    result = []
    for definition in definitions:
        if (not isinstance(definition, Mapping) or not definition.get('base_opcode')
                or not isinstance(definition.get('facts'), list)):
            raise ValueError('Instruction family needs a base and fact groups')
        shared = definition.get('shared_values', {})
        if not isinstance(shared, Mapping):
            raise ValueError('Family shared values must be an object')
        for value in shared.values():
            if (not isinstance(value, Mapping) or set(value) - {'parameters', 'value', 'conditions'}
                    or not {'parameters', 'value'} <= set(value) or not isinstance(value.get('conditions', []), list)
                    or not isinstance(value['value'], Mapping) or 'definition_ref' in value['value']):
                raise ValueError('Shared values must contain one concrete relation body')
        ids = set()
        for fact in definition['facts']:
            parsed = InstructionFactGroup.from_mapping(fact)
            if parsed.value.get('parameter_aliases') and 'signature' not in parsed.value:
                raise ValueError('Shared aliases require a source-bound interface')
            if 'definition_ref' in parsed.value:
                _definition_value(definition, parsed.value)
                if 'signature' in parsed.value:
                    _bind_shared_definition(definition, fact, ())
            if parsed.id in ids:
                raise ValueError('Duplicate family fact identity')
            ids.add(parsed.id)
            if not parsed.scope.get('models') or not parsed.scope.get('forms'):
                raise ValueError('Family facts need explicit model and form scopes')
            bindings = parsed.value.get('variant_bindings')
            if bindings is not None:
                if (parsed.dimension != 'parameters.variant_family' or not isinstance(bindings, Mapping)
                        or set(bindings) != set(parsed.value.get('section_variants', []))
                        or parsed.value.get('base_opcode') != definition['base_opcode']):
                    raise ValueError('Documented variants need explicit family and literal bindings')
                for binding in bindings.values():
                    if (not isinstance(binding, Mapping) or set(binding) != {'double', 'pulse', 'data_bits'}
                            or type(binding['double']) is not bool or type(binding['pulse']) is not bool
                            or type(binding['data_bits']) is not int or binding['data_bits'] not in {8, 16, 32, 64}):
                        raise ValueError('Invalid documented instruction modifier binding')
        if any(set(fact.get('depends_on', [])) - ids for fact in definition['facts']):
            raise ValueError('Missing family fact dependency')
        result.append(dict(copy.deepcopy(definition), vendor=vendor))
    return result



def _definition_value(definition, value, *, component='value'):
    """Expand a shared relation body using explicit operand bindings."""
    reference = value.get('definition_ref')
    if reference is None:
        return copy.deepcopy(value)
    if set(value) - {'definition_ref', 'parameter_bindings', 'signature', 'output_layout', 'detail_refs',
                     'rule_detail_refs', 'parameter_aliases'}:
        raise ValueError('Unreviewed shared definition override')
    shared = definition.get('shared_values', {}).get(reference)
    if (not isinstance(shared, Mapping) or set(shared) - {'parameters', 'value', 'conditions'}
            or not {'parameters', 'value'} <= set(shared) or not isinstance(shared.get('conditions', []), list)):
        raise ValueError('Missing shared family definition')
    names = shared['parameters']
    bindings = value.get('parameter_bindings', {name: name for name in names})
    if (not isinstance(names, list) or len(names) != len(set(names))
            or not isinstance(bindings, Mapping) or set(bindings) != set(names)
            or any(not isinstance(name, str) or not name for name in bindings.values())):
        raise ValueError('Shared definition needs an exact operand map')
    aliases = value.get('parameter_aliases', [])
    if (not isinstance(aliases, list) or any(not isinstance(group, list) or len(group) < 2
            or any(not isinstance(name, str) or name not in bindings for name in group)
            or len(set(group)) != len(group) for group in aliases)):
        raise ValueError('Shared aliases need explicit groups of definition parameters')
    duplicate_groups = sorted(sorted(name for name in names if bindings[name] == actual)
        for actual in set(bindings.values()) if list(bindings.values()).count(actual) > 1)
    if sorted(sorted(group) for group in aliases) != duplicate_groups:
        raise ValueError('Shared alias groups must match every repeated operand binding')
    reference_fields = {'parameter', 'operand', 'head', 'source', 'destination', 'count_operand',
                        'lower_limit', 'upper_limit', 'tested_value', 'subject'}
    def bind(item):
        if isinstance(item, Mapping):
            if 'parameter_ref' in item:
                if set(item) != {'parameter_ref'} or not isinstance(item['parameter_ref'], str) or item['parameter_ref'] not in bindings:
                    raise ValueError('Shared parameter reference needs a declared operand')
                return bindings[item['parameter_ref']]
            result = {}
            for key, child in item.items():
                if ((key in reference_fields or key == 'name' and item.get('op') == 'parameter')
                        and isinstance(child, str) and child in bindings):
                    result[key] = bindings[child]
                else:
                    result[key] = bind(child)
            return result
        if isinstance(item, list):
            return [bind(child) for child in item]
        return copy.deepcopy(item)
    result = bind(shared['value'] if component == 'value' else shared.get('conditions', []))
    details = value.get('detail_refs', [])
    if (not isinstance(details, list) or any(not isinstance(name, str) or not name for name in details)
            or len(details) != len(set(details))):
        raise ValueError('Shared definition details need distinct named references')
    for name in details:
        detail = _definition_value(definition, {'definition_ref': name, 'parameter_bindings': bindings,
            'parameter_aliases': copy.deepcopy(aliases)}, component=component)
        if component == 'value':
            if result.keys() & detail.keys():
                raise ValueError('Shared definition details cannot override the base relation')
            result.update(detail)
        else:
            result.extend(condition for condition in detail if condition not in result)
    rule_details = value.get('rule_detail_refs', {})
    if (not isinstance(rule_details, Mapping) or any(not isinstance(kind, str) or not kind
            or not isinstance(name, str) or not name for kind, name in rule_details.items())):
        raise ValueError('Shared rule details need exact rule kinds and named references')
    for kind, name in rule_details.items():
        detail = _definition_value(definition, {'definition_ref': name, 'parameter_bindings': bindings,
            'parameter_aliases': copy.deepcopy(aliases)}, component=component)
        if component == 'value':
            rules = [rule for rule in result.get('rules', []) if isinstance(rule, dict) and rule.get('kind') == kind]
            if len(rules) != 1 or rules[0].keys() & detail.keys():
                raise ValueError('Shared rule details need one matching rule and cannot override its relation')
            rules[0].update(detail)
        else:
            result.extend(condition for condition in detail if condition not in result)
    if component == 'value' and 'output_layout' in value:
        result['output_layout'] = copy.deepcopy(value['output_layout'])
        if result.get('output_device_forms'):
            result['output_layout']['device_forms'] = copy.deepcopy(result['output_device_forms'])
    return result


def _bind_shared_definition(definition, original, existing):
    """A new interface needs its own source-checked declaration and scope."""
    from plc.instruction_relations import (DERIVED_PREFIXES, _source_related, fact_context_intersection,
                                           _STRENGTH, same_relation_interface, _gate_declaration, _operands, _GATES)
    descriptor = original['value'].get('signature')
    fact = copy.deepcopy(original)
    fact['value'] = _definition_value(definition, original['value'])
    if 'definition_ref' in original['value']:
        for condition in _definition_value(definition, original['value'], component='conditions'):
            if condition not in fact.get('conditions', []):
                fact.setdefault('conditions', []).append(condition)
    if original.get('dimension') == 'parameters.usage' and isinstance(fact['value'].get('operands'), list):
        usages = []
        for parameter in fact['value']['operands']:
            usage = {'parameter': parameter['name'], 'purpose': copy.deepcopy(parameter.get('purpose'))}
            access = {'input': 'read', 'output': 'write', 'in_out': 'read_write'}.get(parameter.get('direction'))
            if access:
                usage['semantic_access'] = access
            for key in ('word_device_layout', 'value_semantics', 'occupied_bits_operand', 'occupied_bit_count', 'range', 'unit', 'encoding', 'write_layout',
                        'components', 'condition', 'numeric_input_gate'):
                if key in parameter:
                    usage[key] = copy.deepcopy(parameter[key])
            layout = usage.get('word_device_layout')
            convention = descriptor.get('word_device_convention', {}) if isinstance(descriptor, Mapping) else {}
            if layout and convention:
                if any(layout[key] != convention[key] for key in set(layout) & set(convention)):
                    raise ValueError('Shared operand layout contradicts the interface word convention')
                usage['word_device_layout'] = {**copy.deepcopy(convention), **layout}
            if (parameter.get('type') and parameter.get('value_semantics') != 'device_reference'
                    and parameter.get('type_completeness') != 'partial'):
                if access in {'read', 'read_write'}:
                    usage['operation_value_type'] = copy.deepcopy(parameter['type'])
                if access in {'write', 'read_write'}:
                    usage['result_value_type'] = copy.deepcopy(parameter['type'])
            usages.append(usage)
        fact['value'] = {'parameter_usage': usages}
    if descriptor is None:
        if original['value'].get('parameter_aliases'):
            raise ValueError('Shared aliases require a source-bound interface')
        return [fact]
    if (not isinstance(descriptor, Mapping) or set(descriptor) - {'parameter_types', 'parameter_directions', 'word_device_convention'}
            or not {'parameter_types', 'parameter_directions'} <= set(descriptor)
            or not isinstance(descriptor['parameter_types'], Mapping)
            or set(descriptor['parameter_types']) != set(descriptor['parameter_directions'])):
        raise ValueError('Shared interface needs explicit parameter types and directions')
    shared = definition.get('shared_values', {}).get(original['value'].get('definition_ref'), {})
    names = shared.get('parameters', [])
    bindings = original['value'].get('parameter_bindings', {name: name for name in names})
    if set(descriptor['parameter_types']) != set(bindings.values()):
        raise ValueError('Shared interface must bind every definition parameter')
    if any(descriptor['parameter_directions'][bindings[group[0]]] != 'in_out'
           for group in original['value'].get('parameter_aliases', [])):
        raise ValueError('Shared aliases require the declared read/write operand')
    result = []
    for signature in existing:
        if (signature.get('dimension') != 'parameters.typed_operands' or signature['id'].startswith(DERIVED_PREFIXES)
                or signature.get('verification') != 'source_checked'
                or not _source_related(original, signature)):
            continue
        signature = _gate_declaration(signature, existing)
        declared = _operands(signature)
        if (declared is None or {p['name'] for p in [*declared[0], *declared[1]]} != set(bindings.values())
                or not same_relation_interface(fact, signature)):
            continue
        parameters = {p.get('name'): p for p in signature.get('value', {}).get('operands', [])}
        if not all(name in parameters and parameters[name].get('type', parameters[name].get('data_type')) == kind
                   and parameters[name].get('direction') == descriptor['parameter_directions'][name]
                   for name, kind in descriptor['parameter_types'].items()):
            continue
        context = fact_context_intersection(fact, signature)
        if context is None:
            continue
        bound = copy.deepcopy(fact)
        if descriptor.get('word_device_convention'):
            bound['value']['word_device_convention'] = copy.deepcopy(descriptor['word_device_convention'])
        binding = bound['value'].get('operand_binding')
        gates = [p for p in signature['value']['operands'] if p.get('enable_pin') or p.get('role') in _GATES]
        if isinstance(binding, dict) and gates:
            binding.setdefault('execution_arguments', [
                {'parameter': p['name'], 'role': 'execution_condition' if p['direction'] == 'input' else 'execution_status'}
                for p in gates])
        bound['id'] = 'assembly:definition:' + original['id'] + ':' + signature['id']
        bound['scope'], conditions = context
        if conditions:
            bound['conditions'] = conditions
        else:
            bound.pop('conditions', None)
        bound['status'] = next((status for status in _STRENGTH
            if status in {original.get('status'), signature.get('status')}), 'unknown')
        bound['depends_on'] = list(dict.fromkeys([*original.get('depends_on', []), signature['id'],
                                                *signature.get('_binding_dependencies', [])]))
        bound['sources'].extend(source for source in copy.deepcopy(signature.get('sources', []))
                                if source not in bound['sources'])
        result.append(bound)
    combined = {}
    for bound in result:
        key = json.dumps({k: v for k, v in bound.items() if k not in {'id', 'sources', 'depends_on'}}, sort_keys=True)
        previous = combined.get(key)
        if previous is None:
            combined[key] = bound
        else:
            for field in ('sources', 'depends_on'):
                previous[field].extend(item for item in bound.get(field, []) if item not in previous[field])
    return list(combined.values())


def registered_family_bindings(registry, *, vendor='mitsubishi', models=None):
    """Snapshot declared D/P names for source-section membership binding."""
    result = {}
    declared_widths = {}
    for (registered_vendor, name), form in registry._ensure_variant_index().items():
        if registered_vendor != vendor or not (form.base_spec.supports_pulse or form.base_spec.double_mnemonic):
            continue
        result[name] = {'base_opcode': form.base_mnemonic, 'double': form.double,
                        'pulse': form.pulse, 'data_bits': 32 if form.double else 16}
    for definition in registry._family_knowledge:
        if definition['vendor'] != vendor:
            continue
        for fact in definition['facts']:
            if models and not set(models).intersection(fact.get('scope', {}).get('models', [])):
                continue
            for name, binding in fact.get('value', {}).get('variant_bindings', {}).items():
                value = {'base_opcode': definition['base_opcode'], **copy.deepcopy(binding)}
                if name in result and any(result[name][key] != value[key]
                                          for key in ('base_opcode', 'double', 'pulse')):
                    raise ValueError('Conflicting declared instruction family: ' + name)
                widths = declared_widths.setdefault(name, {})
                for model in fact.get('scope', {}).get('models', []):
                    if models and model not in models:
                        continue
                    if model in widths and widths[model] != binding['data_bits']:
                        raise ValueError('Conflicting declared instruction width: ' + name + '/' + model)
                    widths[model] = binding['data_bits']
                actual = set(widths.values())
                if len(actual) == 1:
                    result[name] = dict(value, data_bits=next(iter(actual)))
                elif actual:
                    result[name] = {key: value[key] for key in ('base_opcode', 'double', 'pulse')}
                    result[name]['data_bits_by_model'] = copy.deepcopy(widths)
    return result


def _resolve_parameters(value, replacements):
    if isinstance(value, Mapping):
        return {key: _resolve_parameters(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_parameters(item, replacements) for item in value]
    return copy.deepcopy(replacements.get(value, value)) if isinstance(value, str) else value


def _knowledge_form(registry, mnemonic, *, cpu, language=None, facts=None, context=None):
    """Resolve identities separately from source-proven interface semantics."""
    from plc.instruction_definition import DefinitionError, select_fact_dependencies
    from plc.instruction_constraints import _condition
    token = str(mnemonic or '').strip().upper()
    catalogue = registry.resolve_catalog_form(token, cpu=cpu)
    # A registered spelling may return a contract for a different CPU. Keep
    # that catalogue identity, but do not use it to interpret this CPU's source.
    proof_catalogue = catalogue if catalogue is not None and catalogue.spec.supports_cpu(cpu) else None
    candidates = [(definition['base_opcode'], definition['vendor'], fact, definition['facts'])
                  for definition in registry._family_knowledge for fact in definition['facts']]
    if facts:
        candidates.extend((fact.get('value', {}).get('base_opcode') or
                           (proof_catalogue.base_mnemonic if proof_catalogue else token), 'mitsubishi', fact, facts)
                          for fact in facts if fact.get('dimension') == 'parameters.variant_family')
    documented = []
    for base, vendor, fact, source_groups in candidates:
        scope, value = fact.get('scope', {}), fact.get('value', {})
        if (fact.get('dimension') != 'parameters.variant_family' or cpu not in scope.get('models', [])
                or token not in scope.get('forms', []) or token not in value.get('section_variants', [])
                or language and scope.get('languages') and language not in scope['languages']
                or fact.get('status') != 'source_verified' or fact.get('verification') != 'source_checked'):
            continue
        bindings = value.get('variant_bindings', {})
        binding = bindings.get(token)
        width_proven = binding is not None
        if binding is None:
            if not proof_catalogue or base != proof_catalogue.base_mnemonic or base not in value.get('section_variants', []):
                continue
            if (not proof_catalogue.pulse and proof_catalogue.spec.execution_form == 'pulse'
                    and proof_catalogue.spec.contract_coverage().get('execution_form') == 'source_verified'):
                # A reviewed standalone pulse contract identifies its literal,
                # not an inverse (non-pulse) family binding. An unbound source
                # list cannot turn that missing modifier relation into proof.
                continue
            registered = [registry.resolve_catalog_form(name) for name in value['section_variants']]
            registered = [form for form in registered if form is not None and form.base_mnemonic == base]
            if token not in {form.opcode for form in registered}:
                continue
            # A generic section header proves its literal forms, not an
            # integer width. Reuse an already source-proven width for this
            # exact model/family/form before applying the catalogue default.
            proven_widths = {item['data_bits'] for item in documented if item['family'] == base
                             and item['double'] == catalogue.double and item['pulse'] == catalogue.pulse
                             and item.get('data_width_proven')}
            width_proven = len(proven_widths) == 1
            binding = {'double': catalogue.double, 'pulse': catalogue.pulse,
                       'data_bits': next(iter(proven_widths)) if len(proven_widths) == 1
                       else 32 if catalogue.double else 16}
            supports_double = any(form.double for form in registered)
            supports_pulse = any(form.pulse for form in registered)
        else:
            if (set(binding) != {'double', 'pulse', 'data_bits'} or type(binding['double']) is not bool
                    or type(binding['pulse']) is not bool or type(binding['data_bits']) is not int
                    or binding['data_bits'] not in {8, 16, 32, 64}):
                continue
            supports_double = any(b.get('double') is True for b in bindings.values())
            supports_pulse = any(b.get('pulse') is True for b in bindings.values())
        proof_languages = [language] if language else scope.get('languages') or [None]
        for proof_language in proof_languages:
            try:
                closure = select_fact_dependencies(source_groups, [fact['id']], opcode=token, model=cpu,
                                                   language=proof_language)
            except (DefinitionError, TypeError, ValueError, KeyError):
                continue
            if closure['gaps'] or not closure['source_verification_complete']:
                continue
            conditions = []
            for parent in closure['groups']:
                for condition in parent.get('conditions', []):
                    if condition not in conditions:
                        conditions.append(copy.deepcopy(condition))
            if any(source.get('conditions') or source.get('applicability')
                   for parent in closure['groups'] for source in parent.get('sources', [])):
                continue
            rejected = False
            for condition in conditions:
                try:
                    if not _condition(condition, {}, {**(context or {}), '$PLC_MODEL': cpu, '$LANGUAGE': proof_language}):
                        rejected = True
                except (DefinitionError, TypeError, ValueError, KeyError):
                    pass  # Missing context remains an explicit condition.
            if rejected:
                continue
            item = {'opcode': token, 'family': base, 'vendor': vendor, **binding,
                    'data_width_proven': width_proven,
                    'supports_double': supports_double, 'supports_pulse': supports_pulse,
                    'conditions': conditions, 'languages': [proof_language] if proof_language else [],
                    'programming_binding': True, '_proof_id': fact['id']}
            if item not in documented:
                documented.append(item)
    if documented:
        keys = ('family', 'vendor', 'double', 'pulse', 'data_bits')
        if any(any(item[k] != documented[0][k] for k in keys) for item in documented[1:]):
            return {'opcode': token, 'family': token, 'vendor': documented[0]['vendor'],
                    'programming_binding': False, 'binding_status': 'unresolved_source_bindings'}
        result = copy.deepcopy(documented[0])
        result.pop('_proof_id', None)
        result['_proof_ids'] = list(dict.fromkeys(item['_proof_id'] for item in documented))
        result['supports_double'] = any(item['supports_double'] for item in documented)
        result['supports_pulse'] = any(item['supports_pulse'] for item in documented)
        result['data_width_proven'] = any(item['data_width_proven'] for item in documented)
        result['languages'] = list(dict.fromkeys(lang for item in documented for lang in item['languages']))
        if any(item['conditions'] != result['conditions'] for item in documented[1:]):
            result.pop('conditions')
            result['binding_scopes'] = [{'languages': item['languages'], 'conditions': item['conditions']}
                                        for item in documented]
        return result
    if catalogue is None:
        return None
    # A catalogue relationship identifies a legal spelling. It is not proof
    # that an identically named standard function uses 16-bit scan semantics.
    return {'opcode': catalogue.opcode, 'family': catalogue.base_mnemonic, 'vendor': catalogue.base_spec.vendor,
            'double': catalogue.double, 'pulse': catalogue.pulse, 'data_bits': 32 if catalogue.double else 16,
            'supports_double': bool(catalogue.base_spec.double_mnemonic),
            'supports_pulse': catalogue.base_spec.supports_pulse, 'programming_binding': False}


def _execution_knowledge(registry, mnemonic, *, cpu, language, facts, context):
    """Bind shared source rules to qualified interfaces, independently of D/P.

    These are defaults with instruction-specific exceptions. They do not create
    an execution fact, certify a complete disabled state, or change admission.
    """
    from plc.instruction_definition import DefinitionError, select_fact_dependencies
    from plc.instruction_constraints import _condition
    from plc.instruction_relations import fact_context_intersection
    modules, bindings = {}, []
    token = str(mnemonic or '').strip().upper()
    for profile in registry._knowledge_profiles:
        for rule in profile.get('execution_rules', []):
            if cpu not in rule['models'] or language and rule.get('languages') and language not in rule['languages']:
                continue
            for fact in facts or []:
                value, scope = fact.get('value', {}), fact.get('scope', {})
                if (fact.get('dimension') != 'identity.instruction_class'
                        or any(value.get(key) != item for key,item in rule['applies_to'].items())
                        or cpu not in scope.get('models', []) or token not in scope.get('forms', [])
                        or fact.get('verification') != 'source_checked'
                        or fact.get('selection_applicability') == 'not_applicable'):
                    continue
                languages = [language] if language else scope.get('languages') or [None]
                for actual_language in languages:
                    if actual_language and rule.get('languages') and actual_language not in rule['languages']:
                        continue
                    try:
                        closure = select_fact_dependencies(facts, [fact['id']], opcode=token, model=cpu,
                                                           language=actual_language, version=(context or {}).get('version'),
                                                           allow_unknown_scope=True)
                    except (DefinitionError, TypeError, ValueError, KeyError):
                        continue
                    if (any(gap['reason'] != 'scope_context_missing' for gap in closure['gaps']) or any(parent.get('verification') != 'source_checked' or
                            not parent.get('sources') or any(source.get('conditions') or source.get('applicability')
                                for source in parent.get('sources', [])) for parent in closure['groups'])):
                        continue
                    qualification = {'scope':{}, 'conditions':[]}
                    for parent in closure['groups']:
                        merged = fact_context_intersection(qualification, parent)
                        if merged is None:
                            qualification = None
                            break
                        qualification = {'scope':merged[0], 'conditions':merged[1]}
                    if qualification is None:
                        continue
                    conditions = qualification['conditions']
                    applicable = 'unverified' if closure['gaps'] else 'applicable'
                    for condition in conditions:
                        try:
                            if not _condition(condition, {}, {**(context or {}), '$PLC_MODEL':cpu, '$LANGUAGE':actual_language}):
                                applicable = 'not_applicable'
                                break
                        except (DefinitionError, TypeError, ValueError, KeyError):
                            applicable = 'unverified'
                    if applicable == 'not_applicable':
                        continue
                    module = copy.deepcopy(rule)
                    module.update(kind='instruction_execution', vendor=profile['vendor'])
                    modules.setdefault(module['id'], module)
                    binding = {'knowledge_module_id':rule['id'], 'rules':list(rule['rules']),
                               'interface':{key:copy.deepcopy(value[key]) for key in dict.fromkeys(
                                   ('instruction_class','declaration_kind','instruction_variant',*rule['applies_to'])) if key in value},
                               'scope':{**qualification['scope'], **({'languages':[actual_language]} if actual_language else {})},
                               'status':'source_verified' if closure['source_verification_complete'] else 'declared_unverified',
                               'selection_applicability':applicable}
                    if conditions:
                        binding['conditions'] = conditions
                    if binding not in bindings:
                        bindings.append(binding)
    # An unspecified tool language can expose the same qualified default in
    # several source languages. Keep one binding with that explicit scope.
    combined = {}
    for binding in bindings:
        scope = binding['scope']
        marker = json.dumps({**binding, 'scope':{key:item for key,item in scope.items() if key != 'languages'},
                             'language_scoped':bool(scope.get('languages'))}, sort_keys=True)
        previous = combined.get(marker)
        if previous is None:
            combined[marker] = binding
        elif scope.get('languages'):
            previous['scope']['languages'] = list(dict.fromkeys([*previous['scope']['languages'],*scope['languages']]))
    return list(modules.values()), list(combined.values())


def compose_instruction_knowledge(registry, mnemonic, *, cpu, language=None, facts=None, context=None):
    result = _modifier_knowledge(registry, mnemonic, cpu=cpu, language=language, facts=facts, context=context)
    modules, rules = _execution_knowledge(registry, mnemonic, cpu=cpu, language=language, facts=facts, context=context)
    if modules:
        result = result or {'family':mnemonic, 'opcode':mnemonic, 'knowledge_status':'declared_unverified'}
        result['knowledge_modules'] = [*modules, *([result['knowledge_module']] if result.get('knowledge_module') else [])]
        result['common_rules'] = rules
    return result


def _modifier_knowledge(registry, mnemonic, *, cpu, language=None, facts=None, context=None):
    form = _knowledge_form(registry, mnemonic, cpu=cpu, language=language, facts=facts, context=context)
    if form is None:
        return None
    if not form.get('programming_binding'):
        return {'family': form['opcode'], 'opcode': form['opcode'], 'knowledge_status': 'declared_unverified',
                'binding_status': form.get('binding_status', 'catalogue_identity_only')}
    result = {'family': form['family'], 'opcode': form['opcode'],
              'modifiers': {'double': form['double'], 'pulse': form['pulse']}}
    for key in ('conditions', 'languages', 'binding_scopes'):
        if form.get(key):
            result[key] = copy.deepcopy(form[key])
    if form['supports_double'] or form.get('data_width_proven'):
        result['data_bits'] = form['data_bits']
    if form['supports_pulse']:
        result['execution'] = {'trigger': ('execution_condition_rising_edge' if form['pulse']
                                           else 'each_evaluation_when_execution_condition_true'),
                               'when_condition_false': 'not_executed', 'evaluation_frequency': 'caller_control_flow'}
        if form['pulse']:
            result['execution'].update(executions_per_edge=1,
                                       rearm='execution_condition_false_then_true')
    profiles = [p for p in registry._knowledge_profiles
                if p['vendor'] == form['vendor'] and cpu in p['models']
                and (not language or not p.get('languages') or language in p['languages'])]
    # The registry identifies a modifier; only a source-scoped profile explains
    # its documented operation. An uncovered CPU remains a catalogue claim.
    result['knowledge_status'] = 'declared_unverified'
    if not profiles:
        return result
    profile = profiles[0]
    if form['supports_pulse'] or form['supports_double']:
        result['knowledge_module'] = {key: copy.deepcopy(profile[key]) for key in
            ('id', 'vendor', 'models', 'languages', 'modifiers', 'sources', 'knowledge_status') if key in profile}
        result['knowledge_module']['kind'] = 'instruction_modifiers'
        result['knowledge_module']['applies_to'] = 'explicit_catalogue_or_documented_family_forms'
    return result


def _bind_owner_cpu_source(fact, existing, *, opcode, cpu):
    """Link a repeated family CPU claim to its exact, same-source owner claim."""
    if (fact.get('dimension') != 'constraints.cpu_availability'
            or fact.get('status') != 'source_verified'
            or fact.get('verification') != 'source_checked'):
        return fact

    value = fact.get('value', {})
    native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
    if value.get('model_states', {}).get(cpu) == 'conditional' and 'available' not in value:
        guards = [condition for condition in fact.get('conditions', [])
                  if condition.get('context') == 'cpu_version']
        if (native not in fact.get('conditions', []) or len(guards) != 1
                or set(guards[0]) != {'context', 'minimum'}):
            return fact
        minimum = guards[0]['minimum']
        candidates = [own for own in existing
            if own.get('dimension') == 'constraints.cpu_version'
            and own.get('status') == 'source_verified' and own.get('verification') == 'source_checked'
            and own.get('scope') == fact.get('scope')
            and native in own.get('conditions', [])
            and own.get('value', {}).get('minimum_series_version') == 'Ver.' + str(minimum)
            and any(source.get('opcode') == opcode and source.get('target_model') == cpu
                    and source.get('manual_id') and source.get('revision')
                    and source['manual_id'] == other.get('manual_id')
                    and source['revision'] == other.get('revision')
                    and set(source.get('pdf_pages', [])).intersection(other.get('pdf_pages', []))
                    for source in own.get('sources', []) for other in fact.get('sources', []))]
        if len(candidates) == 1:
            fact['depends_on'] = list(dict.fromkeys([*fact.get('depends_on', []), candidates[0]['id']]))
        return fact

    def claim(item):
        value = item.get('value', {})
        if set(value) - {'available', 'model_states', 'rule', 'required_context'}:
            return None
        if type(value.get('available')) is not bool or cpu not in value.get('model_states', {}):
            return None
        return value['available'], value['model_states'][cpu], value.get('rule')

    def conditions(item):
        return [{key: value for key, value in condition.items() if key != 'required_context'}
                for condition in item.get('conditions', [])]

    expected = claim(fact)
    if expected is None:
        return fact
    for own in existing:
        scope = own.get('scope', {})
        if (own['id'] == fact['id'] or own['id'].startswith('family:')
                or own.get('dimension') != fact['dimension']
                or own.get('status') != 'source_verified' or own.get('verification') != 'source_checked'
                or scope.get('models') != [cpu] or scope.get('forms') != [opcode]
                or not set(fact['scope'].get('languages', [])).issubset(scope.get('languages', []))
                or claim(own) != expected or conditions(own) != conditions(fact)):
            continue
        sources = [source for source in own.get('sources', [])
                   if source.get('opcode') == opcode and source.get('target_model') == cpu
                   and source.get('manual_id') and source.get('revision') and source.get('source_ref')
                   and source.get('pdf_pages') and any(
                       source['manual_id'] == other.get('manual_id')
                       and source['revision'] == other.get('revision')
                       and set(source['pdf_pages']).intersection(other.get('pdf_pages', []))
                       for other in fact.get('sources', []))]
        if sources:
            fact.setdefault('depends_on', [])
            if own['id'] not in fact['depends_on']:
                fact['depends_on'].append(own['id'])
            fact['sources'].extend(copy.deepcopy(source) for source in sources if source not in fact['sources'])
    return fact


def _retain_native_parameter_source_qualifications(fact, existing):
    """Keep the exact parameter source's version and operand restrictions."""
    native = {'context': 'project_mode', 'equals': 'native_ladder_instruction'}
    if (fact.get('dimension') != 'parameters.typed_operands'
            or native not in fact.get('conditions', [])
            or fact.get('status') != 'source_verified'
            or fact.get('verification') != 'source_checked'):
        return fact
    replacements = set(fact.get('replaces', []))
    originals = [row for row in existing if row['id'] in replacements]
    # A second compose has already moved the authored extraction to history.
    # Reuse its bound family row only when no new authored replacement exists.
    if not originals:
        originals = [row for row in existing if row['id'] == fact['id']]
    matched = [row for row in originals
        if row.get('dimension') == fact['dimension'] and row.get('scope') == fact['scope']
        and row.get('status') == 'source_verified' and row.get('verification') == 'source_checked'
        and native in row.get('conditions', [])
        and any(left.get('manual_id') and left.get('revision')
                and left['manual_id'] == right.get('manual_id')
                and left['revision'] == right.get('revision')
                and set(left.get('pdf_pages', [])).intersection(right.get('pdf_pages', []))
                for left in fact.get('sources', []) for right in row.get('sources', []))]
    if len(matched) == 1:
        own = matched[0]
        for condition in own.get('conditions', []):
            if condition.get('context') == 'cpu_version' and condition not in fact['conditions']:
                fact['conditions'].append(copy.deepcopy(condition))
        parameters = fact.get('value', {}).get('operands')
        originals = own.get('value', {}).get('operands')
        if (isinstance(parameters, list) and isinstance(originals, list)
                and [p.get('name') for p in parameters] == [p.get('name') for p in originals]):
            for parameter, original in zip(parameters, originals):
                # Shared value types may describe BCD encoding more precisely;
                # they cannot authorize devices/constants absent from the row.
                if isinstance(original.get('range'), Mapping):
                    parameter['range'] = {**parameter.get('range', {}),
                                          **copy.deepcopy(original['range'])}
                if 'word_device_layout' in original:
                    parameter['word_device_layout'] = copy.deepcopy(original['word_device_layout'])
    return fact


def family_fact_groups(registry, mnemonic, *, cpu, existing=()):
    """Bind reviewed family facts to an existing literal form and CPU.

    The same adapter is used by compilation and retrieval. It never admits a
    missing form or changes a source fact's status or declared model scope.
    """
    from plc.instruction_definition import InstructionFactGroup
    form = _knowledge_form(registry, mnemonic, cpu=cpu)
    if form is None:
        return []
    bits = form['data_bits']
    replacements = {'$opcode': form['opcode'], '$data_bits': bits, '$data_words': bits // 16,
                    '$bcd_digits': bits // 4, '$bcd_max': 10 ** (bits // 4) - 1,
                    '$result_bits': bits * 2, '$result_words': bits // 8,
                    '$integer_type': 'DINT' if bits == 32 else 'INT',
                    '$storage_type': 'ANY32' if bits == 32 else 'ANY16',
                    '$signed_max': (1 << (bits - 1)) - 1,
                    '$highest_bit': bits - 1,
                    '$trigger': ('execution_condition_rising_edge' if form['pulse']
                                 else 'each_evaluation_when_execution_condition_true'),
                    '$executions_per_edge': 1 if form['pulse'] else None}
    result, interfaces = [], []
    for definition in registry._family_knowledge:
        if definition['vendor'] != form['vendor'] or definition['base_opcode'] != form['family']:
            continue
        definition = dict(definition, facts=[fact for fact in definition['facts']
            if cpu in fact['scope']['models']
            and (form['opcode'] in fact['scope']['forms'] or '$opcode' in fact['scope']['forms'])])
        resolved_definition = _resolve_parameters(definition, replacements)
        for original in resolved_definition['facts']:
            scope = original['scope']
            if cpu not in scope['models'] or form['opcode'] not in scope['forms']:
                continue
            fact = copy.deepcopy(original)
            fact['scope']['models'], fact['scope']['forms'] = [cpu], [form['opcode']]
            fact = _retain_native_parameter_source_qualifications(fact, existing)
            fact = _bind_owner_cpu_source(fact, existing, opcode=form['opcode'], cpu=cpu)
            # A declaration correction must not discard the author's explicit
            # CPU prerequisites for the same exact owner and source section.
            if fact['dimension'] == 'parameters.st_signature':
                prior = next((row for row in existing if row['id'] == fact['id']), None)
                if (prior and prior.get('scope') == fact['scope']
                        and prior.get('status') == fact.get('status') == 'source_verified'
                        and prior.get('verification') == fact.get('verification') == 'source_checked'):
                    for dependency in existing:
                        if (dependency['id'] not in prior.get('depends_on', [])
                                or dependency.get('dimension') != 'constraints.cpu_availability'
                                or dependency.get('scope') != fact['scope']
                                or dependency.get('status') != 'source_verified'
                                or dependency.get('verification') != 'source_checked'):
                            continue
                        if any(left.get('manual_id') and left.get('revision')
                               and left['manual_id'] == right.get('manual_id')
                               and left['revision'] == right.get('revision')
                               and set(left.get('pdf_pages', [])).intersection(right.get('pdf_pages', []))
                               for left in fact.get('sources', []) for right in dependency.get('sources', [])):
                            fact.setdefault('depends_on', [])
                            if dependency['id'] not in fact['depends_on']:
                                fact['depends_on'].append(dependency['id'])
            if 'signature' in fact['value']:
                interfaces.append((resolved_definition, fact))
                continue
            for bound in _bind_shared_definition(resolved_definition, fact, ()):
                bound = _retain_native_parameter_source_qualifications(bound, existing)
                InstructionFactGroup.from_mapping(bound)
                result.append(bound)
    if interfaces:
        # Apply reviewed declaration corrections before deriving relations from
        # those declarations. Compilation and retrieval each need one pass.
        current, _ = merge_family_facts(existing, result) if result else (existing, [])
        for definition, fact in interfaces:
            for bound in _bind_shared_definition(definition, fact, current):
                InstructionFactGroup.from_mapping(bound)
                result.append(bound)
    return result


def merge_family_facts(existing, incoming):
    """Replace explicitly reviewed extractions; retain originals for debug."""
    replaced_ids = {identity for fact in incoming for identity in fact.get('replaces', [])}
    # Retain dependencies of surviving claims. Dependencies wholly inside a
    # replaced extraction must not keep that entire stale extraction alive.
    old = {fact['id']: fact for fact in existing}
    pending = [identity for fact in [*[f for f in existing if f['id'] not in replaced_ids], *incoming]
               for identity in fact.get('depends_on', [])]
    retained = set()
    while pending:
        identity = pending.pop()
        if identity in retained:
            continue
        retained.add(identity)
        if identity in old:
            pending.extend(old[identity].get('depends_on', []))
    replaced_ids -= retained
    replaced = [copy.deepcopy(fact) for fact in existing if fact['id'] in replaced_ids]
    merged = {fact['id']: copy.deepcopy(fact) for fact in existing if fact['id'] not in replaced_ids}
    for fact in incoming:
        fact = copy.deepcopy(fact)
        fact.pop('replaces', None)
        previous = merged.get(fact['id'])
        if previous is not None:
            before = {key: value for key, value in previous.items() if key != 'sources'}
            after = {key: value for key, value in fact.items() if key != 'sources'}
            if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
                replaced.append(copy.deepcopy(previous))
            else:
                sources = copy.deepcopy(previous.get('sources', []))
                sources.extend(source for source in fact.get('sources', []) if source not in sources)
                fact['sources'] = sources
        merged[fact['id']] = fact
    return list(merged.values()), replaced


def parameter_usage_facts(facts):
    """Bind source-qualified relations and operand details without guessing names.

    A dataflow or typed expression already states how an operand is used. Its
    source scope, conditions and dependency graph qualify the resulting table.
    An operation label without an input/output binding cannot produce one.
    """
    from plc.instruction_definition import DefinitionError, Expression, ValueType
    generic = {'read', 'write', 'read_write', 'input', 'output', 'in_out', 'source', 'destination'}
    def described(parameter):
        purpose = parameter.get('purpose')
        return bool(purpose) and (not isinstance(purpose, str) or purpose not in generic)
    def uses(value):
        result, type_claims = {}, {}
        convention = value.get('word_device_convention', {}) if isinstance(value, Mapping) else {}
        def detail(name, key, claim):
            bucket = type_claims.setdefault(name.upper(), {}).setdefault(key, [])
            if claim not in bucket:
                bucket.append(copy.deepcopy(claim))
        def storage_layout(name, bits):
            unit = convention.get('device_unit_bits')
            if (type(bits) is int and type(unit) is int and unit > 0 and bits >= unit and bits % unit == 0):
                detail(name, 'word_device_layout', {**convention, 'value_bits': bits, 'word_count': bits // unit})
        def value_type(name, key, kind):
            detail(name, key, kind)
            storage_layout(name, kind.get('bits'))
        def add(name, use):
            if isinstance(name, str) and name:
                bucket = result.setdefault(name.upper(), [])
                if use not in bucket:
                    bucket.append(use)
        def relation(item):
            if not isinstance(item, Mapping) or item.get('knowledge_scope') == 'operation_summary':
                return
            binding = item.get('operand_binding')
            for argument in binding.get('execution_arguments', []) if isinstance(binding, Mapping) else []:
                add(argument.get('parameter'), {'execution_role': argument.get('role')})
            inputs, output, operation = item.get('inputs'), item.get('output'), item.get('operation')
            if (isinstance(inputs, list) and inputs and isinstance(output, str) and output
                    and isinstance(operation, str) and operation not in {'unknown', 'not_established'}):
                for index, name in enumerate(inputs, start=1):
                    add(name, {'operation': operation, 'input_position': index, 'result': output})
                add(output, {'result_of': operation, 'inputs': inputs})
            if item.get('kind') in {'range_copy', 'range_fill'}:
                source, destination, count = (item.get(key) for key in ('source', 'destination', 'count_operand'))
                bits = item.get('element_bits')
                if all(isinstance(name, str) and name for name in (source, destination, count)) and type(bits) is int:
                    cases = [(source, destination, {})]
                    selector = item.get('direction_selector')
                    if isinstance(selector, Mapping) and selector.get('device'):
                        cases = [(branch.get('source'), branch.get('destination'),
                                  {'when_device_state': {'device': selector['device'], 'equals': state}})
                                 for state, branch in ((False, selector.get('false', {})), (True, selector.get('true', {})))
                                 if isinstance(branch, Mapping)]
                    for source_name, target_name, qualifications in cases:
                        if not all(isinstance(name, str) and name for name in (source_name, target_name)):
                            continue
                        layout = {'head': target_name, 'count_operand': count, 'element_bits': bits, **qualifications}
                        for key in ('destination_step_words', 'overlap', 'out_of_range'):
                            if key in item:
                                layout[key] = copy.deepcopy(item[key])
                        detail(target_name, 'write_layout', layout)
                        storage_layout(source_name, bits); storage_layout(target_name, bits)
                        add(source_name, {'source_of': item['kind'], 'destination': target_name,
                                          'count_operand': count, **qualifications})
                        add(target_name, {'result_of': item['kind'], 'source': source_name,
                                          'count_operand': count, **qualifications})
                        add(count, {'point_count_for': item['kind'], 'destination': target_name,
                                    'element_bits': bits, **qualifications})
            for output in item.get('outputs', []) if isinstance(item.get('outputs'), list) else []:
                target = output.get('target') if isinstance(output, Mapping) else None
                if not isinstance(target, Mapping) or not isinstance(target.get('parameter'), str):
                    continue
                try:
                    expression = Expression.from_mapping(output.get('expression')).as_mapping()
                except (DefinitionError, TypeError, ValueError, KeyError):
                    continue
                parameters = []
                def read(node):
                    if node['op'] == 'parameter':
                        value_type(node['name'], 'operation_value_type', node['type'])
                        if node['name'] not in parameters:
                            parameters.append(node['name'])
                    for arg in node.get('args', []):
                        read(arg)
                read(expression)
                siblings = [o.get('target', {}) for o in item.get('outputs', []) if isinstance(o, Mapping)]
                same_target = [t for t in siblings if isinstance(t, Mapping)
                               and t.get('parameter') == target['parameter']]
                # A component type does not describe the entire packed result
                # span. Its explicit parameter contract supplies that layout.
                if len(same_target) == 1 and target.get('offset', 0) == 0:
                    value_type(target['parameter'], 'result_value_type', expression['type'])
                destination = {'operand': target['parameter'], 'offset': target.get('offset', 0), 'kind': target.get('kind')}
                for name in parameters:
                    add(name, {'used_in': expression['op'], 'result': destination})
                add(target['parameter'], {'result_of': expression['op'], 'inputs': parameters,
                                          'offset': target.get('offset', 0)})
            for rule in item.get('rules', []) if isinstance(item.get('rules'), list) else []:
                relation(rule)
        relation(value)
        # An interface port direction describes the call. These declarations
        # describe the accessed storage, which can also be read by an output
        # port (increment, exchange, shift, and similar operations).
        for usage in value.get('parameter_usage', []) if isinstance(value, Mapping) else []:
            if not isinstance(usage, Mapping) or not isinstance(usage.get('parameter'), str):
                continue
            name = usage['parameter']
            if usage.get('purpose'):
                add(name, {'semantic_role': copy.deepcopy(usage['purpose'])})
                detail(name, 'semantic_role', usage['purpose'])
            for key in ('semantic_access', 'operation_value_type', 'result_value_type', 'word_device_layout',
                        'value_semantics', 'occupied_bits_operand', 'occupied_bit_count', 'range', 'unit', 'encoding', 'write_layout',
                        'components', 'condition', 'numeric_input_gate'):
                if key in usage:
                    claim = usage[key]
                    if key in {'operation_value_type', 'result_value_type'} and isinstance(claim, Mapping):
                        claim = ValueType.from_mapping(claim).as_mapping()
                    detail(name, key, claim)
        layout = value.get('output_layout') if isinstance(value, Mapping) else None
        binding = value.get('operand_binding', {}) if isinstance(value, Mapping) else {}
        destination = binding.get('output', {}) if isinstance(binding, Mapping) else {}
        # A bound output has already mapped the source layout to the actual
        # interface name; its original source label is not another parameter.
        bound_layout = isinstance(destination, Mapping) and isinstance(destination.get('write_layout'), Mapping)
        if isinstance(layout, Mapping) and isinstance(layout.get('head'), str) and not bound_layout:
            detail(layout['head'], 'write_layout', layout)
        details = {name: {key: values[0] for key, values in claims.items() if len(values) == 1}
                   for name, claims in type_claims.items()}
        return result, details
    effects = [(fact, *uses(fact.get('value'))) for fact in facts
               if str(fact.get('dimension', '')).startswith('effects.') or fact.get('dimension') == 'parameters.usage']
    output, identities = [], {fact['id'] for fact in facts}
    from plc.instruction_relations import _source_related, same_relation_interface, fact_context_intersection
    signature_ids = {fact['id'] for fact in facts if fact.get('dimension') == 'parameters.typed_operands'}
    bound_pairs = {(dependency, signature_id) for effect, _, _ in effects
                   if effect.get('dimension') == 'effects.bound_relation'
                   for signature_id in effect.get('depends_on', []) if signature_id in signature_ids
                   for dependency in effect.get('depends_on', [])}
    for signature in facts:
        if signature.get('dimension') != 'parameters.typed_operands' or signature['id'].startswith('assembly:'):
            continue
        operands = signature.get('value', {}).get('operands')
        if not isinstance(operands, list) or not operands:
            continue
        names = {str(p.get('name', '')).upper() for p in operands}
        for effect, usages, expression_details in effects:
            referenced = set(usages) | set(expression_details)
            if not referenced.intersection(names):
                continue
            linked = signature['id'] in effect.get('depends_on', [])
            if (effect.get('dimension') == 'effects.bound_relation' or effect['id'].startswith('assembly:definition:')) and not linked:
                continue
            if not same_relation_interface(effect, signature) or not (linked or _source_related(effect, signature)):
                continue
            if fact_context_intersection(signature, effect) is None:
                continue
            if effect.get('dimension') != 'effects.bound_relation' and (effect['id'], signature['id']) in bound_pairs:
                continue
            allowed = set(names)
            binding = effect.get('value', {}).get('operand_binding')
            destination = binding.get('output', {}) if isinstance(binding, Mapping) else {}
            if linked and destination.get('kind') in {'return_value', 'implicit_state'} and destination.get('parameter'):
                allowed.add(destination['parameter'].upper())
            if not referenced <= allowed:
                continue
            scope, compatible = {}, True
            for key in set(signature.get('scope', {})) | set(effect.get('scope', {})):
                a, b = signature.get('scope', {}).get(key), effect.get('scope', {}).get(key)
                if a and b:
                    if key not in {'forms', 'models', 'languages', 'versions'}:
                        if a != b: compatible = False
                        scope[key] = copy.deepcopy(a)
                    else:
                        scope[key] = [item for item in a if item in b]
                        if not scope[key]: compatible = False
                else:
                    scope[key] = copy.deepcopy(a or b)
            if not compatible:
                continue
            value = copy.deepcopy(signature['value'])
            changed = False
            binding = effect.get('value', {}).get('operand_binding')
            details = copy.deepcopy(expression_details)
            if effect.get('dimension') == 'effects.bound_relation' and isinstance(binding, Mapping):
                for parameter in binding.get('inputs', []):
                    if parameter.get('value_type'):
                        details.setdefault(parameter['parameter'].upper(), {})['operation_value_type'] = parameter['value_type']
                destination = binding.get('output', {})
                for key in ('write_layout', 'result_value_type'):
                    if key in destination:
                        details.setdefault(destination['parameter'].upper(), {})[key] = destination[key]
            for parameter in value['operands']:
                usage = usages.get(str(parameter.get('name', '')).upper())
                if usage and not described(parameter):
                    parameter['purpose'] = {'uses': copy.deepcopy(usage)}
                    changed = True
                for key, detail in details.get(str(parameter.get('name', '')).upper(), {}).items():
                    if key not in parameter and not (key in {'operation_value_type', 'result_value_type'} and detail == parameter.get('type')):
                        parameter[key] = copy.deepcopy(detail)
                        changed = True
            if not changed:
                continue
            identity = 'assembly:parameters:' + signature['id'] + ':' + effect['id']
            if identity in identities:
                continue
            conditions = copy.deepcopy(signature.get('conditions', []))
            for condition in effect.get('conditions', []):
                # This condition is proven by the original literal signature;
                # the original effect and its conditions remain dependencies.
                if (isinstance(condition, Mapping) and condition.get('kind') == 'operand_form'
                        and set(condition) == {'kind', 'required_operands'}
                        and {str(name).upper() for name in condition['required_operands']} <= names):
                    continue
                if condition not in conditions:
                    conditions.append(copy.deepcopy(condition))
            assignments = {}
            for condition in conditions:
                if not isinstance(condition, Mapping) or set(condition) != {'context', 'equals'}:
                    continue
                path, expected = condition['context'], condition['equals']
                if path in assignments and (type(assignments[path]) is not type(expected)
                                             or assignments[path] != expected):
                    compatible = False
                assignments[path] = expected
            if not compatible:
                continue
            statuses = {signature.get('status'), effect.get('status')}
            strength = ('conflict', 'unknown', 'not_applicable', 'declared_unverified', 'candidate_evidence', 'source_verified')
            status = next((s for s in strength if s in statuses), 'unknown')
            fact = {'id': identity, 'dimension': 'parameters.typed_operands', 'value': value,
                    'scope': scope, 'status': status,
                    'verification': 'source_checked' if all(f.get('verification') == 'source_checked' for f in (signature, effect)) else 'not_performed',
                    'sources': copy.deepcopy([*signature.get('sources', []), *effect.get('sources', [])]),
                    'depends_on': [signature['id'], effect['id']]}
            if conditions:
                fact['conditions'] = conditions
            output.append(fact)
            identities.add(identity)
    # Different source relations often enrich the same call: one supplies a
    # semantic purpose, another a result expression. Deliver one compatible
    # table, retaining every prerequisite; differing guards/statuses stay apart.
    def join_value(left, right):
        if json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True):
            return copy.deepcopy(left)
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            if set(left) == set(right) == {'uses'} and isinstance(left['uses'], list) and isinstance(right['uses'], list):
                uses = copy.deepcopy(left['uses'])
                markers = {json.dumps(item, sort_keys=True) for item in uses}
                uses.extend(copy.deepcopy(item) for item in right['uses'] if json.dumps(item, sort_keys=True) not in markers)
                return {'uses': uses}
            joined = copy.deepcopy(left)
            for key, value in right.items():
                joined[key] = join_value(left[key], value) if key in left else copy.deepcopy(value)
            return joined
        raise ValueError('Different parameter claims')
    combined = []
    for fact in sorted(output, key=lambda item: item['id']):
        for prior in combined:
            if (fact['depends_on'][0] != prior['depends_on'][0] or any(
                    json.dumps(fact.get(key), sort_keys=True) != json.dumps(prior.get(key), sort_keys=True)
                    for key in ('scope', 'conditions', 'status', 'verification'))):
                continue
            a, b = prior['value'], fact['value']
            if {k: v for k, v in a.items() if k != 'operands'} != {k: v for k, v in b.items() if k != 'operands'}:
                continue
            if len(a['operands']) != len(b['operands']):
                continue
            try:
                operands = [join_value(left, right) for left, right in zip(a['operands'], b['operands'])]
            except ValueError:
                continue
            prior['value']['operands'] = operands
            for key in ('sources', 'depends_on'):
                prior[key].extend(copy.deepcopy(item) for item in fact[key] if item not in prior[key])
            break
        else:
            combined.append(fact)
    return combined


def normalize_rotation_claims(facts):
    """Refine an old fixed-word count summary using its complete source rule."""
    from plc.instruction_relations import _source_related, same_relation_interface, fact_context_intersection
    rules = [f for f in facts if f.get('dimension') == 'effects.rotation'
             and f.get('status') == 'source_verified' and f.get('verification') == 'source_checked'
             and f.get('value', {}).get('operation') == 'rotate'
             and f['value'].get('count_policy') == 'modulo_effective_operand_bits'
             and f['value'].get('operand_width_policy') == 'word_width_or_digit_specified_bits']
    result, originals = [], []
    for original in facts:
        value = original.get('value', {})
        corrected = original
        if (value.get('operation') == 'rotate_bits' and value.get('count') == 'n_modulo_32'
                and value.get('width_bits') == 32 and type(value.get('carry_in_rotation')) is bool):
            for rule in rules:
                relation = rule['value']
                if (relation.get('width_bits') != 32 or relation.get('direction') != value.get('direction')
                        or (relation.get('carry_flag') == 'included') != value['carry_in_rotation']
                        or not _source_related(original, rule) or not same_relation_interface(original, rule)):
                    continue
                context = fact_context_intersection(original, rule)
                if context is None or context[0] != original.get('scope', {}) or context[1] != original.get('conditions', []):
                    continue
                corrected = copy.deepcopy(original)
                corrected['value'].update(count='n_modulo_effective_operand_bits',
                    operand_width_policy=relation['operand_width_policy'], knowledge_scope='operation_summary')
                corrected['depends_on'] = list(dict.fromkeys([*corrected.get('depends_on', []), rule['id']]))
                corrected['sources'].extend(copy.deepcopy(s) for s in rule['sources'] if s not in corrected['sources'])
                originals.append(copy.deepcopy(original))
                break
        result.append(corrected)
    return result, originals


def compose_programming_facts(registry, mnemonic, *, cpu, existing):
    """Compile ordinary family facts and source-bound modifier knowledge."""
    from plc.instruction_relations import (DERIVED_PREFIXES, bind_relation_facts, interface_relation_facts,
                                           normalize_precision_claims, normalize_declared_form_labels, normalize_native_interface_facts, normalize_interface_parameters, call_boundary_facts)
    externally_used = {identity for fact in existing if not fact['id'].startswith(DERIVED_PREFIXES)
                       for identity in fact.get('depends_on', [])}
    # Derived tables are recomputed from their source bindings. Retaining a
    # stale table here otherwise keeps an explicitly corrected extraction alive
    # merely because that derived table references it.
    stale = [fact for fact in existing if fact['id'].startswith(DERIVED_PREFIXES)
             and fact['id'] not in externally_used]
    stale_ids = {fact['id'] for fact in stale}
    current = [fact for fact in existing if fact['id'] not in stale_ids]
    # A template can remain declared while its reviewed model/form scope is
    # narrowed. Such old owner copies must leave the active graph too; merging
    # only newly applicable templates would retain the superseded scope.
    form = _knowledge_form(registry, mnemonic, cpu=cpu)
    templates = {fact['id']: fact for definition in registry._family_knowledge
        if form and definition['vendor'] == form['vendor'] and definition['base_opcode'] == form['family']
        for fact in definition['facts']}
    def has_exact_owner_source(fact):
        """Keep an exact owner fact that shares an ID with a base-family template.

        Authored Q/L facts can reuse a stable family ID while carrying a
        form-specific scope and source binding (for example ``*`` versus
        ``D*P``). A base template that only covers ``*`` must not retire that
        independently scoped ``D*P`` fact merely because the IDs match.
        Family-derived copies remain retireable: their source still names the
        base opcode rather than this exact owner form.
        """
        scope = fact.get('scope', {})
        if scope.get('models') != [cpu] or scope.get('forms') != [form['opcode']]:
            return False
        return any(
            source.get('opcode') == form['opcode']
            and source.get('target_model') == cpu
            and isinstance(source.get('manual_id'), str) and source['manual_id']
            and isinstance(source.get('revision'), str) and source['revision']
            and isinstance(source.get('pdf_pages'), list) and source['pdf_pages']
            for source in fact.get('sources', [])
        )

    retired = [fact for fact in current if (
        (fact['id'] in templates
         and (cpu not in templates[fact['id']]['scope']['models']
              or not {form['opcode'], '$opcode'}.intersection(templates[fact['id']]['scope']['forms'])))
        or (form and fact['id'].startswith('family:' + form['family'] + ':')
            and fact['id'] not in templates))
        and not has_exact_owner_source(fact)]
    retired_ids = {fact['id'] for fact in retired}
    current = [fact for fact in current if fact['id'] not in retired_ids]
    incoming = family_fact_groups(registry, mnemonic, cpu=cpu, existing=current)
    dangling = {identity for fact in [*current, *incoming] for identity in fact.get('depends_on', [])
                if identity in retired_ids}
    if dangling:
        raise ValueError('Surviving fact depends on a retired family scope: ' + ','.join(sorted(dangling)))
    facts, replaced = merge_family_facts(current, incoming)
    replaced.extend(copy.deepcopy(retired))
    facts, identity_originals = normalize_declared_form_labels(facts)
    replaced.extend(identity_originals)
    facts, registry_originals = normalize_native_interface_facts(facts)
    replaced.extend(registry_originals)
    facts, interface_originals = normalize_interface_parameters(facts)
    replaced.extend(interface_originals)
    facts, precision_originals = normalize_precision_claims(facts)
    replaced.extend(precision_originals)
    facts, rotation_originals = normalize_rotation_claims(facts)
    replaced.extend(rotation_originals)
    facts.extend(interface_relation_facts(facts))
    facts.extend(bind_relation_facts(facts))
    facts.extend(call_boundary_facts(facts))
    facts.extend(parameter_usage_facts(facts))
    def finish(current, previous):
        regenerated = {fact['id']: fact for fact in current}
        previous.extend(copy.deepcopy(fact) for fact in stale if fact != regenerated.get(fact['id']))
        sources = [fact for fact in current if not fact['id'].startswith(DERIVED_PREFIXES)]
        assembled = sorted((fact for fact in current if fact['id'].startswith(DERIVED_PREFIXES)), key=lambda fact: fact['id'])
        return [*sources, *assembled], previous
    knowledge = _modifier_knowledge(registry, mnemonic, cpu=cpu, facts=facts)
    mnemonic = (knowledge or {}).get('opcode', mnemonic)
    module = (knowledge or {}).get('knowledge_module')
    if module is None or 'execution' not in knowledge:
        return finish(facts, replaced)
    from plc.instruction_definition import DefinitionError, select_fact_dependencies
    proof_conditions = {}
    source_form = _knowledge_form(registry, mnemonic, cpu=cpu, facts=facts)
    identity_proofs = set((source_form or {}).get('_proof_ids', []))
    def verified_proof(fact):
        # A section's variant list is not an independent trigger proof unless
        # it actually established this qualified identity. Otherwise an
        # unqualified structured row can erase the Native proof's guards.
        if fact['id'] not in identity_proofs:
            return False
        conditions = []
        languages = fact.get('scope', {}).get('languages') or [None]
        try:
            for language in languages:
                closure = select_fact_dependencies(facts, [fact['id']], opcode=mnemonic,
                                                   model=cpu, language=language)
                if (closure['gaps'] or not closure['source_verification_complete']
                        or any(f.get('verification') != 'source_checked'
                               or any(s.get('conditions') or s.get('applicability') for s in f.get('sources', []))
                               for f in closure['groups'])):
                    return False
                for group in closure['groups']:
                    for condition in group.get('conditions', []):
                        if condition not in conditions:
                            conditions.append(copy.deepcopy(condition))
        except (DefinitionError, TypeError, ValueError, KeyError):
            return False
        proof_conditions[fact['id']] = conditions
        return True
    proofs = [fact for fact in facts if fact.get('dimension') == 'parameters.variant_family'
              and fact.get('status') == 'source_verified' and fact.get('verification') == 'source_checked'
              and mnemonic in fact.get('value', {}).get('section_variants', [])
              and verified_proof(fact)]
    if not proofs:
        return finish(facts, replaced)
    pulse = knowledge['modifiers']['pulse']
    if pulse:
        for fact in facts:
            if (fact.get('dimension') in {'execution.form', 'execution.source_form'}
                    and fact.get('value', {}).get('form') == 'continuous'
                    and fact['id'].startswith('authored:applied:')
                    and any(s.get('manual_id') == 'fx3_programming_r' for s in fact.get('sources', []))):
                # Correct the old extractor's PP-suffix error using the literal
                # registered form and its source proof. Keep the old review
                # status and conditions, and retain the old value for debug.
                replaced.append(copy.deepcopy(fact))
                fact['value']['form'] = 'pulse'
                fact['sources'].extend(copy.deepcopy(module['sources']))
    derived = []
    for index, proof in enumerate(proofs):
        item = {'id': 'modifier:execution' + (':' + proof['id'] if index else ''),
            'dimension': 'execution.trigger',
            'value': {'trigger': knowledge['execution']['trigger'],
                      'executions_per_edge': 1 if pulse else None, 'when_condition_false': 'not_executed',
                      'evaluation_frequency': 'caller_control_flow'},
            'status': 'source_verified', 'verification': 'source_checked',
            'scope': copy.deepcopy(proof['scope']), 'conditions': proof_conditions[proof['id']],
            'depends_on': [proof['id']], 'sources': copy.deepcopy([*proof['sources'], *module['sources']]),
            'replaces': ['authored:applied:execution_form@fx3_programming_r']}
        derived.append(item)
    facts, superseded = merge_family_facts(facts, derived)
    return finish(facts, [*replaced, *superseded])
