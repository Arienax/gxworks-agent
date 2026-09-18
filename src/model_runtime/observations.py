"""Bounded, local, best-effort transport observations. Never initiates requests.

No messages, response text, tool arguments, raw errors, URLs or credentials are
stored. A 2xx response proves acceptance only, not that sampling took effect.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from model_runtime.contract import CapabilityContract, scoped_contract
from model_runtime.request_policy import parameter_value
from model_runtime.contract import MISSING

MAX_ROWS = 4096
MAX_AGE = 30 * 86400


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def safe_scalar(value):
    # Free-form strings are not safe telemetry. Only numeric/boolean or a short
    # identifier (enum) is retained, not prompts/pathnames accidentally supplied.
    from model_runtime.domain import finite
    return isinstance(value, bool) or finite(value) or isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value))


class ObservationStore:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def record(self, scope, target, outcome, value=None, *, context='', source='observation', code=''):
        if (outcome not in {'accepted', 'rejected', 'observed'} or not safe_scalar(value)
                or source not in {'probe','observation'} or not re.fullmatch(r'[a-f0-9]{64}', context)
                or code not in {'','unsupported_parameter','invalid_parameter','invalid_value','unknown_parameter'}):
            return
        from model_runtime.contract import identifier
        try:
            identifier(target)
            payload = json.dumps(value, allow_nan=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path, timeout=.1) as db:
                db.execute('CREATE TABLE IF NOT EXISTS observations (scope TEXT, target TEXT, outcome TEXT, value TEXT, context TEXT, source TEXT, code TEXT, at REAL, PRIMARY KEY(scope,target,outcome,value,context,source))')
                now = time.time()
                db.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?,?,?)',
                    (digest(scope), target, outcome, payload, context, source, code, now))
                db.execute('DELETE FROM observations WHERE at < ?', (now-MAX_AGE,))
                db.execute('DELETE FROM observations WHERE rowid IN (SELECT rowid FROM observations ORDER BY at DESC LIMIT -1 OFFSET ?)', (MAX_ROWS,))
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
            # Evidence failure must never abort or retry a business request.
            return

    def decorate(self, contract):
        if not self.path.is_file():
            return contract
        try:
            # A read never creates a database or migrates a user's profile.
            with sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=.1) as db:
                rows = db.execute('SELECT target,outcome,value,context,source,code,at FROM observations WHERE scope=? AND at>? ORDER BY at DESC LIMIT 128',
                    (digest(contract.scope), time.time()-MAX_AGE)).fetchall()
        except (OSError, sqlite3.Error, ValueError):
            return contract
        try:
            from model_runtime.domain import evidence
            parameters = dict(contract.parameters)
            for name, desc in list(parameters.items()):
                entries = [{'outcome':o, 'value':json.loads(v), 'context':c, 'source':s, 'code':code, 'at':at}
                    for target,o,v,c,s,code,at in rows if target == name][:16]
                if entries:
                    parameters[name] = replace(desc, evidence=evidence({**desc.evidence, 'observations': entries}))
            # Capability observations are shown separately, not promoted to a
            # schema-enforcement guarantee after one successful JSON response.
            capabilities = dict(contract.capabilities)
            for name, desc in list(capabilities.items()):
                target = 'json_output' if name == 'structured_output' else name
                entries = [{'outcome':o, 'value':json.loads(v), 'context':c, 'source':s, 'code':code, 'at':at}
                    for t,o,v,c,s,code,at in rows if t == target and o == 'observed'][:8]
                if entries:
                    capabilities[name] = replace(desc, evidence=evidence({**desc.evidence, 'observations':entries}))
            return replace(contract, parameters=parameters, capabilities=capabilities)
        except (ValueError, TypeError, OverflowError):
            return contract

    def capabilities(self, scope):
        if not self.path.is_file():
            return []
        try:
            with sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=.1) as db:
                return [{'target':t, 'value':json.loads(v), 'context':c, 'at':at} for t,v,c,at in db.execute(
                    "SELECT target,value,context,at FROM observations WHERE scope=? AND outcome='observed' AND at>? ORDER BY at DESC LIMIT 16",
                    (digest(scope),time.time()-MAX_AGE))]
        except (OSError, sqlite3.Error, ValueError):
            return []

    def clear(self, scope):
        if self.path.is_file():
            try:
                with sqlite3.connect(self.path, timeout=.1) as db:
                    db.execute('DELETE FROM observations WHERE scope=?', (digest(scope),))
            except (OSError, sqlite3.Error, ValueError):
                return


def request_observer(provider, store, *, source='observation'):
    """Callback installed by the application, never by PLC Core."""
    def observe(params, events, error=None):
        contract = scoped_contract(provider.profile, params.get('model'), provider.api_key)
        if contract is None:
            return
        values = {}
        for name, desc in contract.parameters.items():
            v = parameter_value(params, name, desc)
            if v is not MISSING and v is not None and safe_scalar(v):
                if isinstance(v, str):
                    from model_runtime.contract import member
                    # Do not record arbitrary enum/text identifiers: a user may
                    # accidentally paste a key, file name, or private label.
                    if v == provider.api_key or not member(v, desc.values or desc.ui_hint.get('suggestions', ())):
                        continue
                values[name] = v
        context = digest({'parameters':values, 'stream':bool(params.get('stream')),
            'tools':bool(params.get('tools')), 'response_format':(params.get('response_format') or {}).get('type')})
        if error is not None:
            # Do not mine arbitrary provider prose or guess which of several
            # fields caused an error. Only named, structured schema rejections.
            if getattr(error, 'status_code', None) not in {400,422}:
                return
            body = getattr(error, 'body', None)
            if not isinstance(body, dict):
                return
            body = body.get('error', body)
            if not isinstance(body, dict):
                return
            code, target = body.get('code'), body.get('param')
            if code not in {'unsupported_parameter','invalid_parameter','invalid_value','unknown_parameter'}:
                return
            for name, desc in contract.parameters.items():
                if name in values and target in {name,'.'.join(desc.wire_path)}:
                    store.record(contract.scope,name,'rejected',values[name],context=context,source=source,code=code)
            return
        for name,v in values.items():
            store.record(contract.scope,name,'accepted',v,context=context,source=source)
        from model_runtime.provider import ToolCallEnd, TextDelta
        allowed = {t.get('function',{}).get('name') for t in params.get('tools',[])}
        calls = [e.tool_call for e in events if isinstance(e,ToolCallEnd)]
        if calls and all(c.name in allowed for c in calls):
            try:
                if all(isinstance(json.loads(c.arguments) if isinstance(c.arguments,str) else c.arguments,dict) for c in calls):
                    store.record(contract.scope,'tools','observed',True,context=context,source=source)
            except (ValueError,TypeError):
                pass
        fmt = (params.get('response_format') or {}).get('type')
        if fmt in {'json_object','json_schema'}:
            try:
                text = ''.join(e.text for e in events if isinstance(e,TextDelta))
                if text and isinstance(json.loads(text), (dict,list)):
                    store.record(contract.scope,'json_output','observed',fmt,context=context,source=source)
            except (ValueError,TypeError):
                pass
    return observe
