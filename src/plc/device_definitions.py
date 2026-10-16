"""Source-bound special-device facts, independent of address-range support.

Missing definitions remain unknown. This reviewed subset does not grant native
or runtime support, infer a CPU from its family, or turn source occurrences into
definitions. Knowledge and both language inspectors consume the same facts.
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from collections.abc import Mapping

from plc.device_identity import canonical_reference_device
from plc.target_support import normalize_target_cpu
from shared.paths import resource_path


@lru_cache(maxsize=1)
def _definitions():
    path = resource_path('instructions/mitsubishi/device_definitions.json')
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported device-definition schema')
    rows = data['definitions']
    ids = set()
    for row in rows:
        if (row['id'] in ids or not row['devices'] or not row['models'] or not row['sources']
                or row['status'] not in {'source_verified', 'not_applicable'}
                or row['access'] not in {'user', 'system', 'user_system', 'unknown'}
                or row.get('value_type') not in {None, 'bit', 'word'}
                or type(row.get('word_bit_view', False)) is not bool):
            raise ValueError('Invalid or duplicated source-bound device definition')
        ids.add(row['id'])
        for source in row['sources']:
            if not all(source.get(k) for k in ('manual_id','manual_number','revision','pdf_pages')):
                raise ValueError('A device definition needs source revision and page evidence')
        row['canonical_devices'] = [canonical_reference_device(d) for d in row['devices']]
    return rows


def device_definitions():
    return copy.deepcopy(_definitions())


def _at_least(actual, minimum):
    if isinstance(minimum, int):
        if not re.fullmatch(r'\d+', str(actual)):
            return None
        return int(actual) >= minimum
    if not re.fullmatch(r'\d+(?:\.\d+)*', str(actual)):
        return None
    left, right = [int(x) for x in str(actual).split('.')], [int(x) for x in str(minimum).split('.')]
    width = max(len(left), len(right))
    return tuple(left + [0]*(width-len(left))) >= tuple(right + [0]*(width-len(right)))


def _condition_result(condition, context):
    actual = context.get(condition['field'])
    if actual is None or actual == '':
        return None
    op, expected = condition['operator'], condition['value']
    if op == 'equals':
        return str(actual).upper() == str(expected).upper()
    if op == 'one_of':
        return str(actual).upper() in {str(item).upper() for item in expected}
    if op == 'not_suffix':
        return not str(actual).upper().endswith(str(expected).upper())
    if op == 'at_least':
        try:
            return _at_least(actual, expected)
        except (TypeError, ValueError):
            return None
    raise ValueError(f'Unknown device applicability condition {op!r}')


def device_fact_context(context):
    """Project confirmed hardware fields into fact names without parsing prose.

    Conflicting aliases and ambiguous firmware strings remain unresolved. The
    caller owns confirmation; model candidate content must never be passed here.
    """
    result = copy.deepcopy(dict(context)) if isinstance(context, Mapping) else {}
    for field, alias in (('hardware_variant','cpu_full_model'), ('cpu_version','firmware')):
        actual = result.pop(alias, None)
        if actual is None or actual == '':
            continue
        if field == 'cpu_version' and not re.fullmatch(r'\d+(?:\.\d+)*', str(actual)):
            continue
        if result.get(field) not in (None, '', actual):
            result.pop(field, None)
        else:
            result[field] = actual
    return result


def resolve_device_definition(device, plc_model, *, context=None):
    """Resolve all concrete members of a selected menu option without fallback."""
    cpu = normalize_target_cpu(plc_model)
    address = canonical_reference_device(str(device).upper())
    base = address.split('.')[0]
    context = device_fact_context(context)
    members, ids = [], set()
    for member in cpu.removesuffix('(RemoteI/O)').split('/'):
        rows = [r for r in _definitions() if member in r['models']
                and (address in r['canonical_devices'] or base in r['canonical_devices'])]
        if not rows:
            members.append({'cpu':member,'status':'unknown','definition_ids':[]})
            continue
        identities = {(r['description'],r['status'],r['access'],json.dumps(r['conditions'],sort_keys=True)) for r in rows}
        if len(identities) != 1:
            members.append({'cpu':member,'status':'conflict','definition_ids':[r['id'] for r in rows]})
        else:
            row = rows[0]
            outcomes = [_condition_result(q,context) for q in row['conditions']]
            status = ('not_applicable' if row['status']=='not_applicable' or False in outcomes else
                      'conditions_missing' if None in outcomes else 'source_verified')
            members.append({'cpu':member,'status':status,'definition_ids':[r['id'] for r in rows]})
        ids.update(r['id'] for r in rows)
    statuses = {m['status'] for m in members}
    selected = [copy.deepcopy(r) for r in _definitions() if r['id'] in ids]
    # A merged option with different meanings/permissions cannot borrow one member.
    meanings = {(r['description'],r['access']) for r in selected}
    status = (next(iter(statuses)) if len(statuses)==1 and len(meanings)<=1 else
              'unknown' if 'unknown' in statuses else 'conflict')
    types = {r.get('value_type') for r in selected}
    value_type = next(iter(types)) if status == 'source_verified' and len(types) == 1 else None
    if '.' in address:
        bit = address.split('.', 1)[1]
        value_type = ('bit' if value_type == 'word' and all(r.get('word_bit_view') for r in selected)
                      and re.fullmatch(r'[0-9A-F]+', bit) and int(bit, 16) <= 15 else None)
    return {'device':address,'cpu':cpu,'status':status,'members':members,'definitions':selected,
            'value_type':value_type}


def device_definition_targets(query, plc_model):
    """Source-authored feature aliases; no inferred CPU or fuzzy address guess."""
    cpu = normalize_target_cpu(plc_model)
    members = set(cpu.removesuffix('(RemoteI/O)').split('/'))
    text = re.sub(r'\s+', ' ', str(query or '')).casefold()
    devices = []
    for row in _definitions():
        if not members.intersection(row['models']):
            continue
        for term in row['search_terms']:
            token = re.sub(r'\s+', ' ', term).casefold()
            pattern = re.escape(token)
            if token.isascii():
                pattern = r'(?<![a-z0-9_])' + pattern + r'(?![a-z0-9_])'
            if re.search(pattern, text):
                devices.extend(row['canonical_devices'])
                break
    return list(dict.fromkeys(devices))


def device_access_finding(device, plc_model, *, write=False, context=None):
    """One diagnostic for known definitions; missing facts never grant access."""
    result = resolve_device_definition(device,plc_model,context=context)
    if not result['definitions']:
        return None
    status = result['status']
    code = ('DEVICE_NOT_APPLICABLE' if status=='not_applicable' else
            'DEVICE_DEFINITION_CONFLICT' if status=='conflict' else
            'DEVICE_CONDITIONS_MISSING' if status=='conditions_missing' else
            'DEVICE_DEFINITION_UNKNOWN' if status=='unknown' else None)
    if code is None and write:
        accesses = {r['access'] for r in result['definitions']}
        if accesses == {'system'}:
            code = 'DEVICE_SYSTEM_WRITE'
        elif 'unknown' in accesses:
            code = 'DEVICE_ACCESS_UNKNOWN'
    if code is None:
        return None
    severity = 'error' if code in {'DEVICE_NOT_APPLICABLE','DEVICE_SYSTEM_WRITE'} else 'unverified'
    explanation = {
        'DEVICE_NOT_APPLICABLE': 'the cited definition does not apply to this CPU or hardware condition',
        'DEVICE_DEFINITION_CONFLICT': 'the selected CPU members have different definitions or permissions',
        'DEVICE_CONDITIONS_MISSING': 'hardware/version conditions required by the source are unverified',
        'DEVICE_DEFINITION_UNKNOWN': 'the source does not cover every selected CPU member',
        'DEVICE_SYSTEM_WRITE': 'the source assigns control to the system; a user write is not permitted',
        'DEVICE_ACCESS_UNKNOWN': 'the source does not establish user write permission',
    }[code]
    return {'code':code,'severity':severity,'device':result['device'],'cpu':result['cpu'],
            'message':f"{result['cpu']} {result['device']}: {code}: {explanation}",
            'definition_status':status,'write':write,'definitions':result['definitions']}


def ladder_device_findings(ladder, plc_model, *, context=None):
    """Inspect source-known accesses even when a complete CPU profile is absent."""
    from plc.ir import analyze_rung_access
    if not isinstance(ladder, Mapping):
        return []
    findings, seen = [], set()
    for rung in ladder.get('rungs', ()):
        if not isinstance(rung, Mapping):
            continue
        try:
            reads, writes = analyze_rung_access(rung,plc_model=plc_model)
        except (TypeError, AttributeError, ValueError):
            # Shape/unknown-instruction handling remains the owning validator's
            # responsibility; this pass does not certify unanalysable accesses.
            continue
        for write, devices in ((False,reads),(True,writes)):
            for device in devices:
                finding = device_access_finding(device,plc_model,write=write,context=context)
                key = (device,write)
                if finding and key not in seen:
                    seen.add(key)
                    findings.append({**finding,'rung_id':rung.get('rung_id')})
    return findings
