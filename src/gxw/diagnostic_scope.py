"""Recorded native diagnostic scopes and identity normalization."""
from __future__ import annotations


# Existing adapter plan/inventory allocation capacity, not a CPU program limit.
NATIVE_SOURCE_CAPACITY = 8192


NATIVE_COMPILATION_OPERATIONS = ('Compiler.Build', 'Compiler.ChangeSFCProgram')


# Original public 100-byte FBD Build reports, native version 1.635.0.1.
# Each actual menu choice has a cold rejected-Build control with identical
# node bounds in networks 1 and 3 and an empty middle source network.
# See tests/fixtures/gxw_compile_positions_native.json. This scope says nothing
# about ST, legacy raw reports, expanded FB instances or PLC execution.
PUBLIC_FBD_COMPILE_POSITION_CPUS = frozenset((
    'FX3U/FX3UC', 'Q04UDV', 'FX1N/FX1NC', 'L02', 'FX0N', 'FX1S',
    'FX2/FX2C', 'FX2N/FX2NC', 'FX3S', 'FX3G', 'Q00J', 'Q00UJ', 'Q00', 'Q00U',
    'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U', 'Q03UD', 'Q03UDE', 'Q03UDV',
    'Q04UDH', 'Q04UDEH', 'Q04UDPV', 'Q06H', 'Q06PH', 'Q06UDH', 'Q06UDEH',
    'Q06UDV', 'Q06UDPV', 'Q10UDH', 'Q10UDEH', 'Q12H', 'Q12PH', 'Q12PRH',
    'Q13UDH', 'Q13UDEH', 'Q13UDV', 'Q13UDPV', 'Q20UDH', 'Q20UDEH', 'Q25H',
    'Q25PH', 'Q25PRH', 'Q26UDH', 'Q26UDEH', 'Q26UDV', 'Q26UDPV', 'Q50UDEH',
    'Q100UDEH', 'L02S', 'L06', 'L26', 'L26-BT',
))


# Controlled line changes, original 1.635.0.1 public reports and declaration
# getters. These scopes describe positions, not instruction availability.
PUBLIC_DECLARATION_COMPILE_POSITION_CPUS = frozenset((
    'Q00J', 'Q00', 'Q01', 'Q02/Q02H', 'Q02PH', 'Q06H', 'Q06PH',
    'Q12H', 'Q12PH', 'Q12PRH', 'Q25H', 'Q25PH', 'Q25PRH',
    'FX3U/FX3UC',
))


PUBLIC_LIBRARY_ST_COMPILE_POSITION_CPUS = frozenset((
    'Q00UJ', 'Q00U', 'Q01U', 'Q02U', 'Q03UD', 'Q04UDH', 'Q06UDH',
    'Q10UDH', 'Q13UDH', 'Q20UDH', 'Q26UDH', 'Q50UDEH', 'Q100UDEH',
))


PUBLIC_GLOBAL_DECLARATION_COMPILE_POSITION_CPUS = frozenset((
    'FX3U/FX3UC', 'FX3G', 'FX3S', 'FX2N/FX2NC',
))


PUBLIC_LIBRARY_STRUCTURE_COMPILE_POSITION_CPUS = frozenset((
    'FX1N/FX1NC', 'FX1S', 'FX0N', 'FX1', 'FX0S/FX0', 'FXU/FX2C',
))


# Original SFC public reports, narrow/wide prefixes in actions and conditions.
# Each concrete menu is frozen in gxw-sfc-position-menus-20261014; this scope
# does not come from graph-reader or ordinary-ladder instruction support.
SFC_CHILD_INSTRUCTION_POSITION_CPUS = frozenset((
    'FX0S/FX0', 'FX0N', 'FX1', 'FX1S', 'FX1N/FX1NC', 'FXU/FX2C',
    'FX2N/FX2NC', 'FX3S', 'FX3G', 'FX3U/FX3UC',
    'Q00J', 'Q00UJ', 'Q00', 'Q00U', 'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U',
    'Q03UD', 'Q03UDE', 'Q03UDV', 'Q04UDH', 'Q04UDEH', 'Q04UDV', 'Q04UDPV',
    'Q06H', 'Q06PH', 'Q06UDH', 'Q06UDEH', 'Q06UDV', 'Q06UDPV', 'Q10UDH',
    'Q10UDEH', 'Q12H', 'Q12PH', 'Q12PRH', 'Q13UDH', 'Q13UDEH', 'Q13UDV',
    'Q13UDPV', 'Q20UDH', 'Q20UDEH', 'Q25H', 'Q25PH', 'Q25PRH', 'Q26UDH',
    'Q26UDEH', 'Q26UDV', 'Q26UDPV', 'Q50UDEH', 'Q100UDEH',
    'L02S', 'L02', 'L06', 'L26', 'L26-BT',
))


def _id(value):
    return tuple(value) if isinstance(value, list) and len(value) == 12 else None


PUBLIC_CHECK_REPORT_CONVERSION_CPUS = frozenset((
    'Q00', 'Q00J', 'Q00U', 'Q00UJ', 'Q01', 'Q01U', 'Q02/Q02H', 'Q02PH', 'Q02U',
    'Q03UD', 'Q03UDE', 'Q03UDV', 'Q04UDEH', 'Q04UDH', 'Q04UDPV', 'Q04UDV',
    'Q06H', 'Q06PH', 'Q06UDEH', 'Q06UDH', 'Q06UDPV', 'Q06UDV', 'Q100UDEH',
    'Q10UDEH', 'Q10UDH', 'Q12H', 'Q12PH', 'Q12PRH', 'Q13UDEH', 'Q13UDH',
    'Q13UDPV', 'Q13UDV', 'Q20UDEH', 'Q20UDH', 'Q25H', 'Q25PH', 'Q25PRH',
    'Q26UDEH', 'Q26UDH', 'Q26UDPV', 'Q26UDV', 'Q50UDEH',
))
