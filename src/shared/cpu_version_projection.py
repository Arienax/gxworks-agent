"""CPU firmware qualifications are opt-in in model-facing knowledge only.

The source library and deterministic validators retain their original facts.
Never strip a guard and turn its guarded exclusion into an unconditional rule.
"""
from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping

from shared.engineering_documents import MODEL_ENGINEERING_FIELDS


def cpu_version_requested(request='', specification=None):
    text = str(request or '')
    if re.search(r'(?:不检查|不校验|不核对|不用检查|无需检查|忽略).{0,12}(?:CPU|固件|版本)|'
                 r'(?:do not|don.t|without)\s+(?:check\w*\s+)?(?:cpu|firmware)', text, re.I):
        return False
    if re.search(r'cpu[_\s-]*(?:firmware[_\s-]*)?(?:version|版本)|固件版本|'
                 r'\bfirmware\b|\b(?:FX\w*|Q\w*CPU|L\w*CPU)\s*(?:CPU\s*)?(?:版本|Ver\.?\s*\d)', text, re.I):
        return True
    spec = specification if isinstance(specification, Mapping) else {}
    # Only the settled hardware context, never a model-selected approach or
    # its assumptions, can enable version delivery for subsequent requests.
    return any(isinstance(source, Mapping) and (
        source.get('check_cpu_version') is True or
        any(source.get(key) not in (None, '') for key in ('cpu_version', 'firmware')))
        for source in (spec, spec.get('hardware_profile'), spec.get('hardware_context'), spec.get('project_context')))


def _version_guard(value):
    if isinstance(value, Mapping):
        # An unresolved extraction context carries no firmware predicate. It
        # must not discard the operand table or behavior attached to it.
        if value.get('context') == 'cpu_version' or value.get('path') == 'cpu_version':
            return True
        return any(_version_guard(item) for item in value.values())
    return isinstance(value, (list, tuple)) and any(_version_guard(item) for item in value)


def without_cpu_versions(value):
    """Project a knowledge packet; remove complete version-qualified branches."""
    if isinstance(value, (list, tuple)):
        version_ids = {item.get('id') for item in value if isinstance(item, Mapping)
                       and item.get('dimension') == 'constraints.cpu_version'}
        return [clean for item in value
                if not (isinstance(item, Mapping) and item.get('dimension') == 'constraints.cpu_availability'
                        and version_ids.intersection(item.get('depends_on', [])))
                if (clean := without_cpu_versions(item)) is not None or item is None]
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    if (value.get('dimension') == 'constraints.cpu_version'
            or _version_guard(value.get('conditions', []))
            or value.get('context') in ('cpu_version', 'cpu_version_not_resolved')
            or value.get('path') == 'cpu_version'):
        return None
    result = {}
    for key, item in value.items():
        if key in {'constraints.cpu_version', 'cpu_version', 'minimum_cpu_versions',
                   'minimum_cpu_version', 'minimum_series_version', 'cpu_version_check'}:
            continue
        cleaned = without_cpu_versions(item)
        if cleaned is not None or item is None:
            result[key] = cleaned
    return result


def project_cpu_version_knowledge(value, *, requested=False):
    """Filter knowledge fields without touching requirements, schemas or code."""
    if requested:
        return copy.deepcopy(value)
    if isinstance(value, (list, tuple)):
        return [clean for item in value
                if (clean := project_cpu_version_knowledge(item)) is not None or item is None]
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    if value.get('kind') in {'instruction', 'instruction_form', 'device', 'device_family',
                             'instruction_capability', 'example', 'language', 'positioning',
                             'error', 'process'}:
        return without_cpu_versions(value)
    result = {}
    for key, item in value.items():
        if key in MODEL_ENGINEERING_FIELDS or key.endswith('_schema'):
            cleaned = copy.deepcopy(item)
        elif key in {'knowledge', 'fact_packet', 'additional_knowledge'}:
            cleaned = without_cpu_versions(item)
        else:
            cleaned = project_cpu_version_knowledge(item)
        if cleaned is not None or item is None:
            result[key] = cleaned
    return result


def project_cpu_version_text(text, *, requested=False):
    from shared.model_context import _KNOWLEDGE_BODY

    text = str(text)
    if requested:
        return text
    if not _KNOWLEDGE_BODY.search(text):
        try:
            value = json.loads(text)
        except ValueError:
            return text
        clean = without_cpu_versions(value)
        return json.dumps(clean, ensure_ascii=False, separators=(',', ':')) if clean is not None else ''

    def project(match):
        try:
            clean = without_cpu_versions(json.loads(match[1]))
        except ValueError:
            return match[0]
        if clean is None or clean == []:
            return ''
        return '[PLC KNOWLEDGE]\n' + json.dumps(clean, ensure_ascii=False,
                                                separators=(',', ':')) + '\n[/PLC KNOWLEDGE]'
    return _KNOWLEDGE_BODY.sub(project, text).strip()
