# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Exercise the bundled ST extension and shared executor without native tools."""
from __future__ import annotations


def check_st_runtime():
    from plc.st_validation import inspect_st_program, bounded_st_instructions
    from simulator.bounded import ScanMachine
    source = '''FUNCTION_BLOCK Accumulate
VAR_INPUT Amount:INT; END_VAR VAR_OUTPUT Sum:INT:=0; END_VAR
Sum:=Sum+Amount;
END_FUNCTION_BLOCK
PROGRAM MAIN
VAR i:INT; values:ARRAY[0..2] OF INT:=[3(0)]; instance:Accumulate; END_VAR
FOR i:=0 TO 2 DO values[i]:=i+1; END_FOR;
instance(Amount:=values[2]);
CASE instance.Sum OF 3: D0:=3; ELSE D0:=instance.Sum; END_CASE;
WHILE FALSE DO EXIT; END_WHILE;
REPEAT M0:=2.0**3.0**2.0=64.0; UNTIL TRUE END_REPEAT;
RETURN;
END_PROGRAM'''
    report = inspect_st_program(source, plc_model='FX3U')
    assert report['syntax'] == report['semantic'] == 'passed', report
    instructions = bounded_st_instructions(source, plc_model='FX3U')
    machine = ScanMachine({'rungs': [{}]}, plc_model='FX3U', initial={},
                          execution_context={}, instructions=[instructions])
    first, second = machine.scan({}), machine.scan({})
    assert not machine.gaps, machine.gaps
    assert first['after']['D0'] == 3 and second['after']['D0'] == 6
    assert first['after']['M0'] is True
    assert inspect_st_program('IF TRUE THEN D0:=1;', plc_model='FX3U')['syntax'] == 'failed'
    return dict(frontend=report['frontend'], syntax='passed', bounded_behavior='passed',
                native_compile='unverified', private_materials_required=False)
