"""Local capability catalog. Endpoint matching is exact; no network or SDK here.

Inheritance is single-parent, bounded and cycle checked. Whole descriptors are
replaced, not recursively spliced (which can combine incompatible wire paths).
Unknown endpoints never inherit a same-named official model's capabilities.
"""
from __future__ import annotations
from shared.paths import source_root
import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from model_runtime.contract import (CapabilityContract, CapabilityDescriptor, ParameterDescriptor,
    ConstraintDescriptor, bounded_map, contract_scope, metadata_contract_parts,
    scoped_contract, validate_parameter_paths)


def endpoint_id(value):
    p = urlsplit(str(value).strip())
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError("Expected a credential-free HTTP(S) endpoint")
    host = p.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    port = p.port
    if port and not (p.scheme == 'https' and port == 443 or p.scheme == 'http' and port == 80):
        host += ':' + str(port)
    return urlunsplit((p.scheme, host, p.path.rstrip('/'), '', ''))


def catalog_directory():
    import sys
    return Path(getattr(sys, '_MEIPASS', source_root().parent)) / 'resources' / 'model_catalog'


class ModelCatalog:
    def __init__(self, directory=None):
        root = Path(directory) if directory is not None else catalog_directory()
        self.entries, self.warnings = {}, []
        digest = hashlib.sha256()
        files = sorted(root.rglob('*.json')) if root.is_dir() else []
        if not files:
            raise ValueError("Bundled model catalog is missing")
        if len(files) > 256:
            raise ValueError("Catalog contains too many files")
        for path in files:
            if path.stat().st_size > 64000:
                raise ValueError("Catalog entry too large")
            data = path.read_bytes()
            digest.update(data)
            item = json.loads(data)
            if not isinstance(item, dict) or set(item) - {'id', 'schema_version', 'extends', 'match', 'parameters', 'capabilities', 'constraints', 'references', 'reviewed_at'}:
                raise ValueError("Unexpected catalog fields")
            name = item.get('id')
            if not isinstance(name, str) or name in self.entries or len(name) > 128 or item.get('schema_version') != 1:
                raise ValueError("Invalid or duplicate catalog entry")
            match = item.get('match', {})
            if not isinstance(match, dict) or set(match) - {'endpoints', 'models'}:
                raise ValueError("Invalid catalog match")
            for field in ('endpoints','models'):
                values = match.get(field, [])
                if not isinstance(values, list) or len(values)>128 or not all(isinstance(v,str) and 0<len(v)<=2048 for v in values):
                    raise ValueError("Catalog match must be a bounded exact list")
            for endpoint in match.get('endpoints', []):
                endpoint_id(endpoint)
            if 'extends' in item and not isinstance(item['extends'],str):
                raise ValueError("Catalog supports one explicit parent")
            self.entries[name] = item
        self.revision = digest.hexdigest()[:16]
        for name in self.entries:
            self.expand(name)

    def expand(self, name, seen=()):
        if name in seen or len(seen) >= 8 or name not in self.entries:
            raise ValueError("Invalid catalog inheritance")
        entry = self.entries[name]
        parent = entry.get('extends')
        result = self.expand(parent, (*seen, name)) if parent else {'parameters': {}, 'capabilities': {}, 'constraints': {}}
        result = copy.deepcopy(result)
        for group in result:
            for key, value in bounded_map(entry.get(group, {})).items():
                if value is None:
                    result[group].pop(key, None)
                else:
                    result[group][key] = copy.deepcopy(value)
        p = {k: ParameterDescriptor.from_dict(k, v) for k, v in result['parameters'].items()}
        validate_parameter_paths(p)
        for v in result['capabilities'].values():
            CapabilityDescriptor.from_dict(v)
        return result

    def select(self, endpoint, model):
        endpoint = endpoint_id(endpoint)
        matches = []
        for name, item in self.entries.items():
            match = item.get('match', {})
            if endpoint in {endpoint_id(url) for url in match.get('endpoints', [])} and model in match.get('models', []):
                matches.append(name)
        if len(matches) > 1:
            raise ValueError("Ambiguous catalog model match")
        selected = matches[0] if matches else 'generic-openai-compatible'
        return selected, self.expand(selected)


def resolve_capabilities(profile, *, api_key=None, metadata=None, catalog=None, observations=None):
    """Return a new scoped v3 contract without touching defaults or generating."""
    profile = copy.deepcopy(profile)
    catalog = catalog or ModelCatalog()
    model = str(profile.get('model') or '').strip()
    if not model or model == '__discover__':
        raise ValueError("Select a model before resolving capabilities")
    selected, raw = catalog.select(profile.get('baseUrl', ''), model)
    params = {k: ParameterDescriptor.from_dict(k, v) for k, v in raw['parameters'].items()}
    caps = {k: CapabilityDescriptor.from_dict(v) for k, v in raw['capabilities'].items()}
    constraints = {k: ConstraintDescriptor.from_dict(v) for k, v in raw['constraints'].items()}
    # Persisted declared metadata/manual domains remain useful offline. A new
    # catalog revision replaces old catalog knowledge, never user observations.
    try:
        cached = scoped_contract(profile, model, api_key)
    except ValueError:
        cached = None
    if cached:
        for name, desc in cached.parameters.items():
            if desc.source in {"metadata", "manual", "legacy"} or name not in params:
                try:
                    validate_parameter_paths({**params, name: desc})
                    params[name] = desc
                except ValueError:
                    continue
        for name, desc in cached.capabilities.items():
            if desc.source in {"metadata", "manual", "legacy"} or name not in caps:
                caps[name] = desc
        for name, constraint in cached.constraints.items():
            owner = cached.parameters.get(name) or cached.capabilities.get(name)
            if owner and owner.source in {"metadata", "manual", "legacy"}:
                constraints[name] = constraint

    declared_p, declared_c, declared_constraints = metadata_contract_parts(metadata or {})
    # Adopt each descriptor atomically; malformed metadata cannot break catalog.
    for name, desc in declared_p.items():
        try:
            validate_parameter_paths({**params, name: desc})
            params[name] = desc
            constraints.pop(name, None)
        except ValueError:
            continue
    for name, desc in declared_c.items():
        caps[name] = desc
        constraints.pop(name, None)
    constraints.update(declared_constraints)
    # Invalid metadata references are uncertain, not a guessed dependency.
    from dataclasses import replace
    refs = set(params) | set(caps) | {"stream", "tools", "response_format"}
    for group in (params, caps):
        for name, desc in list(group.items()):
            condition = desc.constraints
            if (set(condition.requires) | set(condition.conflicts_with)) - refs:
                group[name] = replace(desc, status="unknown", constraints=ConstraintDescriptor())
    constraints = {name:c for name,c in constraints.items() if name in refs and
        not (set(c.requires) | set(c.conflicts_with)) - refs}

    manual = profile.get('capabilityOverrides') or {}
    if not isinstance(manual, dict) or set(manual) - {'parameters', 'capabilities', 'constraints'}:
        raise ValueError("Manual overrides contain unsupported fields")
    for name, value in bounded_map(manual.get('parameters', {})).items():
        if not isinstance(value, dict):
            raise ValueError("Manual parameter must be a complete descriptor object")
        value = copy.deepcopy(value)
        value['source'] = 'manual'
        if 'domain' in value:
            if not isinstance(value['domain'], dict):
                raise ValueError("Manual domain must be an object")
            value['domain']['source'] = 'manual'
        params[name] = ParameterDescriptor.from_dict(name, value)
        constraints.pop(name, None)
    for name, value in bounded_map(manual.get('capabilities', {})).items():
        if not isinstance(value, dict):
            raise ValueError("Manual capability must be a descriptor object")
        caps[name] = CapabilityDescriptor.from_dict({**value, 'source': 'manual'})
        constraints.pop(name, None)
    constraints.update({k: ConstraintDescriptor.from_dict(v) for k, v in bounded_map(manual.get('constraints', {})).items()})
    validate_parameter_paths(params)
    if cached:
        for name, desc in cached.parameters.items():
            if (name in params and params[name].wire_path == desc.wire_path
                    and params[name].type == desc.type and desc.evidence):
                params[name] = replace(params[name], evidence=desc.evidence)
    scope = contract_scope(profile, params, model, api_key)
    contract = CapabilityContract.from_dict(CapabilityContract(scope, caps, params, constraints).to_dict())
    if observations is not None:
        contract = observations.decorate(contract)
    return {'contract': contract.to_dict(), 'catalog_id': selected, 'catalog_revision': catalog.revision,
        'matched': selected != 'generic-openai-compatible', 'generation_requests': 0,
        'note': f'已从本地预设 {selected}（{catalog.revision}）及已缓存元数据解析。未调用模型生成；未验证参数可以手动设置。'}
