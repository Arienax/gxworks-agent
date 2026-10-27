# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Compile reviewed PWM relations, never manual text, into a versioned profile.

The JSON input is the source of semantic values. This module defines only the
supported relation vocabulary and the software consumer's projection. Rebuilds
are explicit; changing a source without rebuilding invalidates the snapshot.
"""
import copy
import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path

from shared.paths import resource_path

GENERATED_PATH = resource_path('instructions/mitsubishi/pwm_contract.generated.json')
DIRECTORY = GENERATED_PATH.parent
SOURCE_PATH = DIRECTORY / 'pwm_reviewed_facts.json'  # Offline compiler input, never read by load_contract.
COMPILER_VERSION = 1


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _one(values, name):
    unique = {canonical(v): v for v in values}
    if len(unique) != 1:
        raise ValueError('PWM source is incomplete or ambiguous: ' + name)
    return next(iter(unique.values()))


def compile_source(source):
    if type(source.get('schema_version')) is not int or source['schema_version'] != 1:
        raise ValueError('PWM source schema unsupported')
    profiles, catalog = {}, {}
    supplemental = source['supplemental_facts']
    ag = _one([r['rows'] for f in supplemental if f['status'] == 'source_verified'
        and f['verification'] == 'source_checked' and f.get('sources')
        for r in f['value'].get('rules', []) if r['kind'] == 'PWM_parameter_time_ranges'], 'AG timing')
    for supplemental_fact in supplemental:
        if (supplemental_fact.get('conditions') or supplemental_fact.get('depends_on')
                or supplemental_fact.get('scope', {}).get('versions')
                or any(s.get('conditions') or s.get('applicability') for s in supplemental_fact.get('sources', []))):
            raise ValueError('Conditional supplemental PWM source needs a bound consumer')
    for owner in source['owners']:
        model, opcode, facts = owner['model'], owner['opcode'], owner['facts']
        if any(model not in f.get('scope', {}).get('models', [])
               or opcode not in f.get('scope', {}).get('forms', [])
               or not {'ladder', 'st', 'fbd'} <= set(f.get('scope', {}).get('languages', [])) for f in supplemental):
            raise ValueError('Supplemental PWM source scope does not cover profile')
        by_id = {f['id']: f for f in facts}
        if len(by_id) != len(facts):
            raise ValueError('Duplicate PWM source identity')
        for f in facts:
            if (f['status'] != 'source_verified' or f['verification'] != 'source_checked'
                    or not f.get('sources') or any(d not in by_id for d in f.get('depends_on', []))):
                raise ValueError('PWM reviewed source closure is incomplete')
        rules = [(f, r) for f in facts for r in f['value'].get('rules', [])]
        for _, r in rules:
            catalog[canonical(r)] = r
        on_max = _one([r['maximum'] for f, r in rules if f['dimension'] == 'parameters.storage_domain'
            and r['kind'] == 'allowed_interval' and r.get('subject') == {'operand': 's1'}], 'ON storage')
        period_max = _one([r['maximum'] for f, r in rules if f['dimension'] == 'parameters.storage_domain'
            and r['kind'] == 'allowed_interval' and r.get('subject') == {'operand': 's2'}], 'period storage')
        off = _one([r['minimum_period_minus_ON_time_us'] for _, r in rules
            if r['kind'] == 'PWM_minimum_OFF_time'], 'OFF time')
        ab = _one([r['rows'] for _, r in rules if r['kind'] == 'PWM_hardware_minimum_times'], 'AB timing')
        boolean = {'kind': 'bool'}
        integer = {'kind': 'int', 'bits': 64, 'signed': True}
        param = lambda name: {'op': 'parameter', 'name': name, 'type': integer}
        names = {'operand_width': 'PWM_WIDTH_VALID', 'operand_domain': 'PWM_DOMAIN_VALID',
            'operand_relation': 'PWM_RELATION_VALID', 'destination': 'PWM_DESTINATION_VALID',
            'channel_configuration': 'PWM_CONFIGURED', 'resource': 'PWM_RESOURCE_FREE',
            'unused_channel': 'PWM_UNUSED_CHANNEL', 'time_unit': 'PWM_UNIT_KNOWN',
            'hardware_timing': 'PWM_TIMING_VALID', 'off_time': 'PWM_OFF_TIME_VALID',
            'program_context': 'PWM_PROGRAM_VALID'}
        predicates = {name: {'op': 'parameter', 'name': parameter, 'type': boolean}
                      for name, parameter in names.items()}
        relation = _one([r for _, r in rules if r['kind'] == 'operand_relation'], 'operand relation')
        if relation != {'kind': 'operand_relation', 'left': {'operand': 's1'},
                'operator': '>', 'right': {'operand': 's2'}, 'error': True}:
            raise ValueError('Unsupported PWM relation, consumer update required')
        predicates['operand_relation'] = {'op': 'le', 'type': boolean,
            'args': [param('PWM_ON_POSITIVE'), param('PWM_PERIOD_POSITIVE')]}
        predicates['off_time'] = {'op': 'ge', 'type': boolean, 'args': [
            {'op': 'sub', 'type': integer, 'overflow': 'reject',
             'args': [param('PWM_PERIOD_US_PULSING'), param('PWM_ON_US_PULSING')]},
            {'op': 'constant', 'type': integer, 'value': off}]}
        domain_minimum = _one([r['value'] for _, r in rules
            if r['kind'] == 'any_value_less_than' and r.get('error') is True
            and r.get('logical_connective') == 'OR'], 'numeric minimum')
        channel_ranges = {hardware: _one([[r['minimum'], r['maximum']] for _, r in rules
            if r['kind'] == 'allowed_interval' and r.get('quantity') == 'channel_number'
            and r.get('when', {}).get('hardware') == hardware], hardware + ' channels')
            for hardware in ('CPU_module', 'high_speed_pulse_IO_module')}
        outputs = _one([r['allowed_addresses'] for _, r in rules
            if r['kind'] == 'allowed_device_classes' and r.get('hardware') == 'CPU_module'], 'CPU outputs')
        profiles[opcode + ':' + model] = {'maximum_ON': on_max, 'maximum_period': period_max, 'predicates': predicates,
            'minimum_OFF_us': off, 'AB_timing': ab, 'AG_timing': ag,
            'domain_minimum': domain_minimum, 'channel_ranges': channel_ranges, 'CPU_outputs': outputs,
            'source_fact_ids': [f['id'] for f in facts],
            'supplemental_fact_ids': [f['id'] for f in supplemental],
            'fact_digests': {f['id']: digest(f) for f in facts + supplemental}}
    return {'schema_version': 2, 'compiler_version': COMPILER_VERSION, 'reviewed_source': copy.deepcopy(source),
        'source_sha256': digest(source), 'binding': 'PWM-reviewed-v1:' + digest(source),
        'profiles': profiles, 'rule_catalog': [catalog[k] for k in sorted(catalog)],
        'coverage': 'named_bounded_software_subset_not_full_instruction_admission'}


@lru_cache(maxsize=2)
def _loaded(generated_text):
    generated = json.loads(generated_text)
    required = {'schema_version', 'compiler_version', 'reviewed_source', 'source_sha256',
                'binding', 'profiles', 'rule_catalog', 'coverage'}
    if (not isinstance(generated, dict) or set(generated) != required
            or generated['schema_version'] != 2 or generated['compiler_version'] != COMPILER_VERSION
            or not isinstance(generated['reviewed_source'], dict)
            or not isinstance(generated['profiles'], dict)
            or not isinstance(generated['rule_catalog'], list)):
        raise ValueError('Unsupported PWM runtime contract')
    source = generated['reviewed_source']
    if source.get('schema_version') != 1 or not isinstance(source.get('owners'), list):
        raise ValueError('Missing PWM reviewed facts')
    for owner in source['owners']:
        profile = generated['profiles'][owner['opcode'] + ':' + owner['model']]
        if profile['source_fact_ids'] != [fact['id'] for fact in owner['facts']]:
            raise ValueError('Incomplete PWM owner facts')
    return source, generated


@lru_cache(maxsize=2)
def _path_loaded(generated_path, generated_stat):
    return _loaded(generated_path.read_text(encoding='utf-8'))


def load_contract():
    from plc.instruction_definition import UnknownInstructionSemantics
    try:
        return _path_loaded(GENERATED_PATH, (GENERATED_PATH.stat().st_mtime_ns, GENERATED_PATH.stat().st_size))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise UnknownInstructionSemantics('pwm_reviewed_source_missing_or_drifted') from exc


def profile(opcode, model):
    _, config = load_contract()
    return copy.deepcopy(config['profiles'][opcode + ':' + model])


def snapshot_facts(opcode, model):
    source, _ = load_contract()
    owner = next(o for o in source['owners'] if o['opcode'] == opcode and o['model'] == model)
    return copy.deepcopy(owner['facts'])


def source_binding():
    return load_contract()[1]['binding']


@lru_cache(maxsize=2)
def _catalog(binding):
    return {canonical(r) for r in load_contract()[1]['rule_catalog']}


def rule_supported(rule):
    # JSON canonicalization distinguishes true from 1 as well as unknown keys.
    try:
        return canonical(rule) in _catalog(source_binding())
    except (TypeError, ValueError):
        return False


def source_identity_matches(group, opcode, model):
    """A live identity cannot reuse a reviewed ID with different provenance.

    Scope/conditions/dependencies are checked separately, at their live values;
    changing the actual source reference never silently inherits the snapshot.
    """
    known = {f['id']: f for f in snapshot_facts(opcode, model)}
    expected = known.get(group.id)
    if expected is None:
        return False
    normalize = lambda sources: [{k: v for k, v in s.items() if k != 'source_kind'} for s in sources]
    if canonical(normalize(group.sources)) != canonical(normalize(expected['sources'])):
        return False
    if not set(expected.get('depends_on', [])) <= set(group.depends_on):
        return False
    for condition in expected.get('conditions', []):
        if canonical(condition) not in {canonical(c) for c in group.conditions}:
            return False
    for axis, allowed in expected.get('scope', {}).items():
        actual = (group.scope or {}).get(axis)
        if not actual or not set(actual) <= set(allowed):
            return False
    if 'rules' in expected['value']:
        actual_rules = {canonical(r) for r in group.value.get('rules', [])}
        if any(canonical(r) not in actual_rules for r in expected['value']['rules']):
            return False
    return True
