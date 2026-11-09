"""Source-scoped FBD values and constraints, independent of GXW storage.

Native objects are projected into ordinary values by gxw. This module owns
literal admission, type domains and call/net constraints, not file layouts.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
import re

from .instruction_definition import ValueType


class FBDSemanticError(ValueError):
    def __init__(self, code, message, *, severity='error', **details):
        super().__init__(message)
        self.finding = dict(code=code, severity=severity, message=message, **details)


def language_rules(target, plc_model, *, registry=None):
    """Read complete rules for every member of the selected native CPU menu."""
    from .instructions import DEFAULT_INSTRUCTION_REGISTRY
    from .instruction_constraints import _cpu_members
    registry = registry or DEFAULT_INSTRUCTION_REGISTRY
    if not registry.fact_bundle_current():
        raise FBDSemanticError('fbd_rule_resource', 'FBD fact resources changed; reload the runtime', severity='unverified')
    feature = registry.language_feature(target, language='fbd')
    members = _cpu_members(str(plc_model).upper())
    if not feature:
        raise FBDSemanticError('fbd_rule_coverage', 'Missing FBD language rule: ' + target,
                               severity='unverified', rule=target)
    results = []
    for member in members:
        values = {}
        for fact in feature.get('facts', ()):
            scope = fact.get('scope', {})
            if member not in scope.get('models', ()) or scope.get('languages') != ['fbd']:
                continue
            if fact.get('status') != 'source_verified' or fact.get('conditions'):
                continue
            for key, value in fact.get('value', {}).items():
                if key in values and values[key] != value:
                    raise FBDSemanticError('fbd_rule_conflict', 'Conflicting FBD rule: ' + target,
                                           severity='unverified', rule=target, plc_model=member)
                values[key] = value
        if not values:
            raise FBDSemanticError('fbd_rule_coverage', 'FBD rule has no applicable definition: ' + target,
                                   severity='unverified', rule=target, plc_model=member)
        results.append(values)
    if not results or any(value != results[0] for value in results[1:]):
        raise FBDSemanticError('fbd_rule_coverage', 'FBD rule differs across selected CPU members: ' + target,
                               severity='unverified', rule=target)
    return results[0]


def type_domain(data_type, rules):
    """A generic is a finite set, never a wildcard that suppresses checking."""
    if not isinstance(data_type, str) or not data_type:
        return None
    value = data_type.strip().upper()
    if value in rules.get('generic_types', {}):
        return frozenset(rules['generic_types'][value])
    if value.startswith('ANY'):
        return None
    return frozenset((value,))


def available_types(plc_model, rules):
    from .instruction_constraints import _cpu_members
    profiles = rules.get('supported_types_by_model', {})
    values = [set(profiles[model]) for model in _cpu_members(str(plc_model).upper()) if model in profiles]
    return set.intersection(*values) if values else set(rules.get('scalar_types', ()))


def _integer_domain(value, syntax, rules):
    result = set()
    for name in rules['integer_types']:
        kind = ValueType.from_iec(name)
        low, high = kind.bounds
        if syntax in {'binary', 'octal', 'hexadecimal'}:
            high = (1 << kind.bits) - 1
        if low <= value <= high:
            result.add(name)
    if syntax == 'decimal' and value in (0, 1):
        result.add('BOOL')
    return sorted(result)


def parse_literal(text, plc_model, *, rules=None):
    """Return a contextual literal domain, None for symbols, or a precise error.

Only GX Works2 forms in the selected FBD facts are admitted. This is not a
claim that IEC type prefixes are illegal in another language or product.
"""
    text = str(text).strip()
    if not text:
        return None
    supplied_rules = rules is not None
    rules = rules if supplied_rules else language_rules('FBD.LITERALS', plc_model)
    token = text.upper()
    value, syntax, types = None, None, []
    if token in {'TRUE', 'FALSE'}:
        value, syntax, types = token == 'TRUE', 'boolean', ['BOOL']
    elif re.fullmatch(r'(?:K)?[+-]?\d+', token):
        value, syntax = int(token.removeprefix('K')), 'decimal'
        types = _integer_domain(value, syntax, rules)
    elif match := re.fullmatch(r'(2|8|16)#([0-9A-F]+)', token):
        syntax = {'2': 'binary', '8': 'octal', '16': 'hexadecimal'}[match[1]]
        try:
            value = int(match[2], int(match[1]))
        except ValueError as error:
            raise FBDSemanticError('fbd_literal_syntax', 'Digit is invalid for the constant radix', expression=text) from error
        types = _integer_domain(value, syntax, rules)
    elif re.fullmatch(r'H[0-9A-F]+', token):
        value, syntax = int(token[1:], 16), 'hexadecimal'
        types = _integer_domain(value, syntax, rules)
    elif token.startswith('T#'):
        syntax = 'time'
        duration = token[2:]
        sign = -1 if duration.startswith('-') else 1
        duration = duration[1:] if duration.startswith(('+', '-')) else duration
        parts = list(re.finditer(r'(\d+(?:\.\d+)?)(MS|D|H|M|S)', duration))
        units = rules['time_units_ms']
        if not parts or ''.join(p[0] for p in parts) != duration:
            raise FBDSemanticError('fbd_literal_syntax', 'Invalid GX Works2 time constant', expression=text,
                                   suggestion='Use a T# duration such as T#100ms or T#1s')
        if len({p[2] for p in parts}) != len(parts) or any(
                units[a[2]] <= units[b[2]] for a, b in zip(parts, parts[1:])):
            raise FBDSemanticError('fbd_literal_syntax', 'Time units must occur once in descending order', expression=text)
        value = sign * sum(Fraction(p[1]) * units[p[2]] for p in parts)
        if value.denominator != 1 or not rules['time_range_ms'][0] <= value <= rules['time_range_ms'][1]:
            raise FBDSemanticError('fbd_literal_range', 'Time constant exceeds the supported millisecond range', expression=text)
        value, types = int(value), ['TIME']
    elif text[0] in {'\"', "'"}:
        syntax = 'string'
        if len(text) < 2 or text[-1] != text[0]:
            raise FBDSemanticError('fbd_literal_syntax', 'Unterminated FBD string constant', expression=text)
        value = text[1:-1]
        if len(value) > rules['string_max_characters']:
            raise FBDSemanticError('fbd_literal_range', 'String constant exceeds the target character limit', expression=text)
        if text[0] in value or '\n' in value or '\r' in value:
            raise FBDSemanticError('fbd_literal_coverage', 'Embedded string delimiter/line break needs a target rule',
                                   severity='unverified', expression=text)
        types = ['STRING']
    elif re.fullmatch(r'E?[+-]?(?:\d+\.\d*|\d*\.\d+|\d+[Ee][+-]?\d+)(?:[Ee][+-]?\d+)?', token):
        syntax = 'real'
        try:
            decimal = Decimal(token.removeprefix('E'))
        except InvalidOperation as error:
            raise FBDSemanticError('fbd_literal_syntax', 'Invalid real constant', expression=text) from error
        value = str(decimal)
        types = [name for name, bounds in rules['real_ranges'].items()
                 if decimal == 0 or Decimal(bounds[0]) <= decimal.copy_abs() <= Decimal(bounds[1])]
    elif ('#' in text or text[0].isdigit() or text[0] in '+-'
          or re.match(r'^[KH][+-]?\d', token)):
        raise FBDSemanticError('fbd_literal_syntax', 'Unsupported or malformed GX Works2 FBD constant', expression=text,
                               suggestion='Use decimal 0 / K0, radix 16#FF / HFF, or a T# duration as appropriate')
    else:
        return None
    if syntax not in rules['forms']:
        raise FBDSemanticError('fbd_literal_coverage', 'Constant form is not covered for this target',
                               severity='unverified', expression=text)
    if not supplied_rules:
        types = sorted(set(types) & available_types(plc_model, language_rules('FBD.TYPES', plc_model)))
    if not types:
        raise FBDSemanticError('fbd_literal_range', 'Constant is outside supported value ranges', expression=text)
    return dict(text=text, kind='literal', syntax=syntax, value=value, types=types,
                data_type=types[0] if len(types) == 1 else None,
                constant=True, readable=True, writable=False, storage=None, shape=[])


def solve_type_domains(endpoints, groups):
    """Intersect net and call-dependent type domains to a finite fixed point.

An unresolved endpoint is kept unresolved. Independent known conflicts still
surface; ambiguity is not resolved by inserting casts or choosing a width.
"""
    domains = {key: None if row.get('types') is None else set(row['types']) for key, row in endpoints.items()}
    changed = True
    while changed:
        changed = False
        for group in groups:
            members = [key for key in group['members'] if key in domains]
            known = [domains[key] for key in members if domains[key] is not None]
            if not known:
                continue
            intersection = set.intersection(*known)
            for key in members:
                if domains[key] is not None and domains[key] != intersection:
                    domains[key] = intersection
                    changed = True
    conflicts = [group for group in groups if any(domains.get(key) == set() for key in group['members'])]
    return domains, conflicts


def call_type_groups(name, ports, rules):
    """Return only relationships explicitly owned by this call contract."""
    name = re.sub(r'-\d+$', '', name.upper().lstrip('@'))
    contract = rules.get('operators', {}).get(name)
    if contract is None:
        return [], {}
    inputs = [key for key, row in ports.items() if row.get('side') == 'in' and row.get('formal_name') != 'EN']
    outputs = [key for key, row in ports.items() if row.get('side') == 'out' and row.get('formal_name') != 'ENO']
    overrides = {key: contract['inputs'] for key in inputs}
    overrides.update({key: contract['output'] for key in outputs})
    related = inputs + (outputs if contract.get('same_result_type') else [])
    return ([related] if contract.get('same_input_type') and related else []), overrides


def check_timer_resolution(name, actual, rules):
    """No implicit rounding: valid TIME syntax does not prove block precision."""
    resolution = rules.get('resolution_ms', {}).get(name.upper())
    if resolution and actual.get('constant') and actual.get('types') == ['TIME']:
        if type(actual.get('value')) is not int:
            return dict(code='fbd_timer_resolution_coverage', severity='unverified', expression=actual['text'],
                        message='Timer precision requires the bound constant value', resolution_ms=resolution)
        if actual['value'] % resolution:
            return dict(code='fbd_timer_resolution', severity='error', expression=actual['text'],
                        message=f'{name} requires a multiple of {resolution} ms; the requested time would lose precision',
                        resolution_ms=resolution)
    return None


def storage_keys(actual, *, plc_model=None, view_bits=None):
    """Conservative alias keys for ordering; they never grant write permission."""
    if actual.get('constant'):
        return set()
    storage = actual.get('storage') or {}
    device = storage.get('device') or storage.get('first')
    if device and plc_model:
        from .validation import parse_device_address, is_bit_device, ValidationProfileUnavailableError
        try:
            parsed = parse_device_address(str(device), plc_model)
        except ValidationProfileUnavailableError:
            # Binding already reports the missing address coverage. Keep an
            # exact alias here without blocking unrelated edits or inventing
            # this CPU's radix/word span.
            parsed = None
        if parsed is not None:
            prefix, start = parsed
            stop = start
            if storage.get('last'):
                last = parse_device_address(storage['last'], plc_model)
                if last and last[0] == prefix:
                    stop = last[1]
            elif not is_bit_device(str(device), plc_model):
                bits = view_bits or storage.get('bits') or 16
                if actual.get('kind') == 'label':
                    try:
                        bits = ValueType.from_iec(actual.get('data_type')).bits or bits
                    except (ValueError, TypeError, AttributeError):
                        pass
                stop = start + max(1, (bits + 15) // 16) - 1
            return {'device:' + prefix + ':' + str(index) for index in range(start, stop + 1)}
    if device:
        return {'device:' + str(device).upper()}
    if actual.get('source_table'):
        return {'label:' + actual['source_table'].casefold() + ':' + actual['declaration'].casefold()}
    return {'unresolved:' + actual.get('text', '').casefold()}
