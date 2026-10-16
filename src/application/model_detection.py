"""Zero-generation model discovery and catalog resolution.

Legacy quick calls are now local resolves. Legacy deep scans are rejected before
any I/O: paying for a verification requires an explicit single-target command.
"""
from __future__ import annotations
import copy
import json
import threading
import time
from collections import OrderedDict
from model_runtime.catalog import endpoint_id, resolve_capabilities
from model_runtime.contract import credential_fingerprint
from model_runtime.provider import ModelProviderError

_cache = OrderedDict()
_lock = threading.Lock()
CACHE_SECONDS = 300


def _key(profile, api_key):
    return endpoint_id(profile.get('baseUrl', '')), credential_fingerprint(api_key)


def cached_metadata(profile, api_key):
    with _lock:
        record = _cache.get(_key(profile,api_key))
        if not record or time.monotonic()-record[0] > CACHE_SECONDS:
            return []
        return copy.deepcopy(record[1])


def list_metadata(provider):
    if callable(getattr(provider, 'list_model_metadata', None)):
        entries = list(provider.list_model_metadata(timeout=5.0))
    else:
        entries = [{'id':m} for m in provider.list_models(timeout=5.0)]
    bounded = []
    total = 0
    allowed = {"id", "parameters", "parameter_schema", "capabilities", "constraints", "context_window"}
    for entry in entries[:2048]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or len(entry["id"]) > 256:
            continue
        item = {k:v for k,v in entry.items() if k in allowed}
        try:
            size = len(json.dumps(item, allow_nan=False))
        except (ValueError, TypeError):
            item, size = {"id":entry["id"]}, len(entry["id"])+20
        if size > 64000 or total + size > 1048576:
            item, size = {"id":entry["id"]}, len(entry["id"])+20
        bounded.append(item)
        total += size
    entries = bounded
    with _lock:
        key = _key(provider.profile, getattr(provider, "api_key", ""))
        _cache[key] = time.monotonic(), copy.deepcopy(entries)
        _cache.move_to_end(key)
        while len(_cache)>32:
            _cache.popitem(last=False)
    return entries


def resolve_profile_contract(profile, api_key='', *, observations=None):
    started = time.monotonic()
    entries = cached_metadata(profile,api_key)
    metadata = next((e for e in entries if e['id']==profile.get('model')), {})
    result = resolve_capabilities(profile,api_key=api_key,metadata=metadata,observations=observations)
    result.update(mode='resolve',elapsed_ms=round((time.monotonic()-started)*1000),
        models=[e['id'] for e in entries],model_listing_available=bool(entries),recommended_model=profile['model'])
    if observations:
        result['capability_observations'] = observations.capabilities(result['contract']['scope'])
    return result


def inspect_openai_compatible(provider, model, configured_capabilities=None, *, probes=None, mode='quick', refresh=False):
    if mode not in {'list','quick','resolve','deep'} or not isinstance(refresh,bool):
        raise ValueError('Invalid discovery mode')
    if mode=='deep':
        raise ValueError('Batch scanning was removed. Use explicit single-target verification.')
    profile = copy.deepcopy(provider.profile)
    profile['model'] = str(model or '').strip()
    if mode=='list' or not profile['model'] or profile['model']=='__discover__':
        entries = list_metadata(provider)
        models = sorted({e['id'] for e in entries})
        if not models:
            raise ModelProviderError('模型列表为空或不可用，请手动填写模型 ID。',code='invalid_request')
        return {'mode':'list','models':models,'recommended_model':None,'generation_requests':0,
                'selected_model_available':profile['model'] in models, 'model_listing_available':True,
                'note':f'已获取 {len(models)} 个模型；没有发送生成请求。请选择模型后加载能力配置。'}
    return resolve_profile_contract(profile,getattr(provider, "api_key", ""))
