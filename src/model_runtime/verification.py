"""One explicitly authorized synthetic request. No scans, retries or fallback."""
from __future__ import annotations
import copy
import json
import math
from model_runtime.contract import CapabilityContract
from model_runtime.request_policy import resolve_request
from model_runtime.provider import ModelRequest, UserMessage, TextDelta, ReasoningDelta, ToolCallEnd, Usage
from model_runtime.observations import request_observer
from model_runtime.probes import VERIFIERS


def verify_one(provider, contract, target, *, kind='parameter', value=None, consent=False, store=None):
    if consent is not True:
        raise ValueError('Verification requires explicit paid-request consent')
    if kind not in {'parameter','capability','chat'} or kind == 'chat' and target != 'chat':
        raise ValueError('Unknown verification kind')
    contract = CapabilityContract.from_dict(contract)
    profile = copy.deepcopy(provider.profile)
    profile['capabilityContract'] = contract.to_dict()
    profile['userModelSettings'] = {'scope':dict(contract.scope),'parameters':{}}
    # Keep current explicit settings and constraints; verify a single changed
    # scalar, never strip or silently retry unsupported options.
    previous = provider.profile.get('userModelSettings') or {}
    if previous.get('scope') == contract.scope:
        profile['userModelSettings'] = copy.deepcopy(previous)
    if kind=='parameter':
        if target not in contract.parameters:
            raise ValueError('Unknown parameter')
        contract.parameters[target].validate(value)
        profile['userModelSettings']['parameters'][target] = {'mode':'value','value':value}
    elif kind=='capability' and target not in VERIFIERS:
        raise ValueError('No single-request verifier for this capability')
    tools = ()
    prompt = 'Reply with OK.'
    protocol = {'stream': True, 'tools': None, 'response_format': None, 'tool_choice': None}
    if kind == 'capability':
        spec = VERIFIERS[target]
        if target == 'structured_output' and value not in {'json_object', None}:
            raise ValueError('Only json_object verification is implemented; no JSON-schema guarantee')
        prompt, tools = spec.prompt, copy.deepcopy(spec.tools)
        protocol.update(tools=list(tools) if tools else None,
            response_format=copy.deepcopy(spec.response_format), tool_choice='auto' if tools else None)
    # Use the catalog-declared output-limit path, otherwise the compatible
    # protocol default. This is a budget, not a sampled control value.
    options = dict(resolve_request(profile,{},protocol=protocol,api_key=provider.api_key).options)
    from model_runtime.contract import path_remove
    limit_names = {'max_tokens', 'max_completion_tokens'}
    if kind == 'parameter' and (target in limit_names or contract.parameters[target].wire_path[-1] in limit_names):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 1 <= value <= 64:
            raise ValueError('Single verification permits only an output budget from 1 to 64; use higher limits in normal requests')
    if kind == 'parameter' and (target == 'n' or contract.parameters[target].wire_path[-1] == 'n') and value != 1:
        raise ValueError('Single verification cannot generate multiple choices')
    for name in limit_names | {'timeout', 'max_retries', 'n'}:
        path_remove(options, (name,))
        path_remove(options, ('extra_body', name))
    limit = contract.parameters.get('max_completion_tokens')
    output_limit = int(value) if kind == 'parameter' and target in limit_names else 64
    if limit:
        if not (kind == 'parameter' and target == 'max_completion_tokens'):
            if limit.values:
                allowed = [v for v in limit.values if not isinstance(v, bool) and isinstance(v, (int,float)) and 1 <= v <= 64]
                if not allowed:
                    raise ValueError('Declared output domain cannot fit a 64-token verification budget')
                output_limit = max(allowed)
            else:
                if limit.maximum is not None:
                    output_limit = min(output_limit, math.floor(limit.maximum))
                if limit.exclusive_maximum is not None:
                    output_limit = min(output_limit, math.ceil(limit.exclusive_maximum)-1)
                if limit.step:
                    anchor = 0 if limit.zero_anchored else limit.minimum or 0
                    output_limit = anchor + math.floor((output_limit-anchor)/limit.step)*limit.step
        limit.validate(output_limit)
        if not 1 <= output_limit <= 64:
            raise ValueError('Declared output domain cannot fit a 64-token verification budget')
        limit.write(options, int(output_limit))
    else:
        options['max_completion_tokens'] = output_limit
    # A provider may ignore an output limit. This bounds what we ask for, not
    # the bill of an arbitrary gateway; no claim of an absolute monetary cap.
    if kind == 'parameter':
        from model_runtime.request_policy import parameter_value
        from model_runtime.contract import equal
        if not equal(parameter_value(options, target, contract.parameters[target]), value):
            raise ValueError('Verification budget or protocol would change the requested value')
    clone = copy.copy(provider)
    clone.profile = profile
    owner = copy.copy(provider)
    owner.profile = profile
    clone.observation_sink = request_observer(owner, store, source='probe') if store else None
    # _request_params must not re-apply a profile output-budget override. The
    # verification options are already resolved and validated above.
    clone.profile = {**profile,'generationDefaults':{},'requestOverrides':{},'capabilityContract':{},'userModelSettings':{},'parameterSupport':{},'capabilities':{}}
    request = ModelRequest((UserMessage(prompt),),model=profile['model'],tools=tools,options=options,
        stream=True,timeout=8.0,max_retries=0,enforce_response_language=False,_synthetic_probe=True)
    events = list(clone.stream(request))
    ok = any(isinstance(e,(TextDelta,ReasoningDelta,ToolCallEnd)) for e in events)
    if kind=='capability' and target=='tools':
        calls=[e.tool_call for e in events if isinstance(e,ToolCallEnd)]
        try:
            ok=len(calls)==1 and calls[0].name=='capability_probe' and (json.loads(calls[0].arguments) if isinstance(calls[0].arguments,str) else calls[0].arguments)=={'value':'ok'}
        except (ValueError,TypeError):ok=False
    elif kind=='capability':
        try:ok=json.loads(''.join(e.text for e in events if isinstance(e,TextDelta)))=={'probe':True}
        except (ValueError,TypeError):ok=False
    usage=next((e for e in reversed(events) if isinstance(e,Usage)),None)
    return {'outcome':'observed' if ok and kind!='parameter' else 'accepted' if ok else 'inconclusive',
        'generation_requests':1,'input_tokens':usage.input_tokens if usage else None,
        'output_tokens':usage.output_tokens if usage else None,
        'note':'单次验证已完成；接口接受不代表参数一定生效，JSON 样例不代表严格 schema 保证。'}
