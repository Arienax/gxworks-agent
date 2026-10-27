# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Check the installed PWM/DPWM runtime contract without native tools.

This explicit distribution check recompiles the reviewed facts embedded in the
single installed artifact and compares its compiled contract, and both
instruction forms are bound for every reviewed CPU. Registry admission of a live
PWM owner is a separate concern and is reported elsewhere.
"""
from __future__ import annotations

from pathlib import Path


def check_pwm_runtime():
    from plc import pwm_fact_source
    from plc.pwm_contract import MODELS, bind_pwm_values, pwm_formal_facts, source_binding
    source, generated = pwm_fact_source.load_contract()
    if pwm_fact_source.canonical(pwm_fact_source.compile_source(source)) != pwm_fact_source.canonical(generated):
        raise AssertionError('generated PWM contract differs from the reviewed facts')
    context = {'program_context': 'ladder',
               'hardware': {'module_type': 'CPU_module', 'output_type': 'transistor'},
               'pwm': {'execution_mode': 'core_software_model', 'source_binding': source_binding(),
                       'initially_idle': True, 'pulse_count': 0, 'channel': 1, 'output_device': 'Y0',
                       'configured_for_PWM': True, 'unused_channel_available': True,
                       'resource_owner': None, 'time_unit': 'us'}}
    predicates = {}
    for opcode in ('PWM', 'DPWM'):
        for model in MODELS:
            profile = generated['profiles'].get(opcode + ':' + model)
            if profile is None:
                raise AssertionError('missing PWM profile: ' + opcode + ':' + model)
            facts = pwm_formal_facts(opcode, model)
            if {fact['id'].rsplit(':', 1)[-1] for fact in facts} != set(profile['predicates']):
                raise AssertionError('formal facts differ from the profile: ' + opcode + ':' + model)
            bound = bind_pwm_values(opcode, model,
                {'S1': 10, 'S2': 20, '$OPERANDS': ['K10', 'K20', 'Y0']}, context)
            if (bound.get('PWM_WIDTH_VALID') is not True or bound.get('PWM_DOMAIN_VALID') is not True
                    or bound.get('$PWM_OUTPUT') != 'Y0' or bound.get('$PWM_CHANNEL') != 1):
                raise AssertionError('explicit PWM values were not bound: ' + opcode + ':' + model)
            predicates[opcode + ':' + model] = len(facts)
    return {'status': 'passed', 'directory': str(pwm_fact_source.DIRECTORY),
            'generated_path': str(Path(pwm_fact_source.GENERATED_PATH)),
            'contract_matches_reviewed_facts': True, 'source_sha256': generated['source_sha256'],
            'source_binding': generated['binding'], 'forms': ['DPWM', 'PWM'],
            'models': len(MODELS), 'profiles': predicates,
            'hardware_timing_rows': len(generated['profiles']['PWM:FX5U']['AB_timing']),
            'native_compile': 'not_performed'}
