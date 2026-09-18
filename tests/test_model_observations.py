"""Passive observation cannot change domains, spend tokens, or retain content."""
import copy
import sqlite3
import time
import pytest
from model_observations import ObservationStore, request_observer, digest
from model_contract import CapabilityContract
from model_provider import ModelRequest, UserMessage
from test_model_verification import scoped_provider, WireEndpoint
from test_model_capabilities import Rejection


def test_normal_request_records_actual_fraction_but_not_effect_or_domain(tmp_path):
    p=scoped_provider();store=ObservationStore(tmp_path/'obs.sqlite')
    p.observation_sink=request_observer(p,store)
    before=copy.deepcopy(p.profile)
    list(p.stream(ModelRequest((UserMessage('private project contents not for telemetry'),),options={'temperature':.733},stream=True)))
    assert len(p._client.calls)==1 and p.profile==before
    d=store.decorate(CapabilityContract.from_dict(p.profile['capabilityContract'])).parameters['temperature']
    assert d.status=='unknown' and d.values is None
    assert d.evidence['observations'][0]['value']==.733 and d.evidence['observations'][0]['outcome']=='accepted'
    raw=store.path.read_bytes()
    assert b'private project' not in raw and b'synthetic-key' not in raw and b'gateway.invalid' not in raw
    d.validate(.731)


def test_reading_or_resolving_never_creates_observation_files(tmp_path):
    store=ObservationStore(tmp_path/'absent'/'obs.sqlite');c=CapabilityContract.from_dict(scoped_provider().profile['capabilityContract'])
    assert store.decorate(c)==c and store.capabilities(c.scope)==[]
    assert not store.path.exists() and not store.path.parent.exists()


def test_observation_is_bound_to_credential_model_endpoint_and_context(tmp_path):
    from dataclasses import replace
    p=scoped_provider();c=CapabilityContract.from_dict(p.profile['capabilityContract']);store=ObservationStore(tmp_path/'obs.sqlite')
    store.record(c.scope,'temperature','accepted',.73,context='a'*64)
    assert store.decorate(c).parameters['temperature'].evidence
    for name,value in [('binding','b'*64),('context','b'*64),('model','other'),('endpoint','https://other.invalid/v1')]:
        changed=replace(c,scope={**c.scope,name:value})
        assert not store.decorate(changed).parameters['temperature'].evidence


@pytest.mark.parametrize('status,body',[(500,{'error':{'param':'temperature','code':'invalid_value'}}),
    (429,{'error':{'param':'temperature','code':'invalid_value'}}),(400,{'error':{'message':'temperature unsupported'}}),
    (400,{'error':{'param':'other','code':'invalid_value'}})])
def test_ambiguous_or_transient_error_never_invents_negative_evidence(tmp_path,status,body):
    p=scoped_provider();store=ObservationStore(tmp_path/'obs.sqlite')
    error=Rejection('temperature',status=status);error.body=body
    request_observer(p,store)({'model':'tenant-alias','temperature':.73},[],error)
    assert not store.path.exists()


def test_cancelled_stream_never_marks_request_accepted(tmp_path):
    p=scoped_provider();store=ObservationStore(tmp_path/'obs.sqlite');p.observation_sink=request_observer(p,store)
    stream=p.stream(ModelRequest((UserMessage('test'),),options={'temperature':.73},stream=True))
    next(stream);stream.close()
    assert not store.path.exists() and len(p._client.calls)==1


def test_failed_cache_cannot_abort_or_retry_business_request(tmp_path):
    p=scoped_provider();parent=tmp_path/'file';parent.write_text('not a directory')
    store=ObservationStore(parent/'obs.sqlite');p.observation_sink=request_observer(p,store)
    events=list(p.stream(ModelRequest((UserMessage('test'),),options={'temperature':.73},stream=True)))
    assert events and len(p._client.calls)==1


def test_corrupt_cache_and_expired_samples_are_ignored(tmp_path):
    p=scoped_provider();c=CapabilityContract.from_dict(p.profile['capabilityContract']);store=ObservationStore(tmp_path/'obs.sqlite')
    store.path.write_text('corrupt database')
    assert store.decorate(c)==c
    store.path.unlink();store.record(c.scope,'temperature','accepted',.73,context='a'*64)
    with sqlite3.connect(store.path) as db:db.execute('UPDATE observations SET at=?',(time.time()-40*86400,))
    assert store.decorate(c)==c
    with sqlite3.connect(store.path) as db:db.execute('UPDATE observations SET at=?, value=?',(time.time(),'invalid JSON'))
    assert store.decorate(c)==c


def test_cache_bound_and_clear_do_not_mutate_profile(tmp_path,monkeypatch):
    import model_observations
    monkeypatch.setattr(model_observations,'MAX_ROWS',8)
    p=scoped_provider();c=CapabilityContract.from_dict(p.profile['capabilityContract']);store=ObservationStore(tmp_path/'obs.sqlite')
    for i in range(24):store.record(c.scope,'temperature','accepted',i/100,context=digest(i))
    with sqlite3.connect(store.path) as db:assert db.execute('SELECT count(*) FROM observations').fetchone()[0]==8
    store.clear(c.scope)
    assert store.decorate(c)==c


def test_capability_outputs_are_evidence_not_a_schema_guarantee(tmp_path):
    p=scoped_provider();store=ObservationStore(tmp_path/'obs.sqlite');p.observation_sink=request_observer(p,store)
    list(p.stream(ModelRequest((UserMessage('JSON'),),options={'response_format':{'type':'json_object'}},stream=True)))
    c=store.decorate(CapabilityContract.from_dict(p.profile['capabilityContract']))
    assert c.capabilities['structured_output'].status=='unknown'
    assert c.capabilities['structured_output'].evidence['observations'][0]['value']=='json_object'
    assert len(p._client.calls)==1


def test_free_form_or_credential_like_enum_values_are_not_stored(tmp_path):
    p=scoped_provider();store=ObservationStore(tmp_path/'obs.sqlite')
    request_observer(p,store)({'model':'tenant-alias','reasoning_effort':'sk-private-but-short'},[])
    request_observer(p,store)({'model':'tenant-alias','reasoning_effort':p.api_key},[])
    assert not store.path.exists()
