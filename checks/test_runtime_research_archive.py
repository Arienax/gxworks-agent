from tools.runtime_research_archive import RuntimeResearchArchive


def row(opcode, value):
    return ({'vendor': 'mitsubishi', 'opcode': opcode, 'target_model': 'FX3U'},
            {'vendor': 'mitsubishi', 'opcode': opcode, 'target_model': 'FX3U', 'known': value})


def test_candidate_research_cannot_replace_active_version_and_unchanged_owners_inherit(tmp_path):
    store = RuntimeResearchArchive(tmp_path/'private.sqlite')
    store.begin('v1', 'original')
    store.put(*row('A', 'old-A')); store.put(*row('B', 'old-B')); store.commit()
    store.begin('v2', 'v1')
    store.put(*row('A', 'new-A')); store.commit()
    assert store.get('v1', opcode='A', model='FX3U')['sidecar']['known'] == 'old-A'
    assert store.get('v2', opcode='A', model='FX3U')['sidecar']['known'] == 'new-A'
    assert store.get('v2', opcode='B', model='FX3U')['sidecar']['known'] == 'old-B'
    assert store.get('original', opcode='A', model='FX3U') is None
    store.close()


def test_failed_compile_rolls_back_only_uncommitted_research_version(tmp_path):
    path = tmp_path/'private.sqlite'
    store = RuntimeResearchArchive(path)
    store.begin('good', 'original'); store.put(*row('A', 'good')); store.commit()
    store.begin('failed', 'good'); store.put(*row('A', 'bad')); store.close()
    store = RuntimeResearchArchive(path)
    assert store.get('failed', opcode='A', model='FX3U') is None
    assert store.get('good', opcode='A', model='FX3U')['sidecar']['known'] == 'good'
    store.close()


def test_projection_restores_unknown_behavior_and_original_admission_field_presence(tmp_path):
    from knowledge.runtime_fact_projection import project_runtime_owner
    original = {'vendor': 'mitsubishi', 'opcode': 'SYNTH', 'target_model': 'FX3U',
        'call_available': True, 'facts': [
            {'id': 'known-negative', 'dimension': 'disabled.output',
             'status': 'source_verified', 'verification': 'source_checked',
             'sources': [{'manual_id': 'manual', 'revision': 'A', 'pdf_page': 1}],
             'value': {'available': False, 'result_output': 'undefined_value',
                       'core_execution_established': False}},
            {'id': 'pending', 'dimension': 'effects.behavior',
             'status': 'candidate_evidence', 'verification': 'not_performed',
             'sources': [], 'value': {'unresolved_items': ['output_span']}}],
        'superseded_facts': [{'id': 'old'}]}
    public, sidecar, _ = project_runtime_owner(original)
    assert [f['id'] for f in public['facts']] == ['known-negative']
    assert public['facts'][0]['value']['available'] is False
    assert 'core_execution_established' not in public['facts'][0]['value']
    admission = {key: {'present': key in public, 'value': public.get(key)}
                 for key in ('call_available', 'support_status', 'call_admission_reason', 'call_admission_details')}
    public.update(call_available=False, support_status='definition_only',
                  call_admission_reason='knowledge_only_not_admitted', call_admission_details={})
    store = RuntimeResearchArchive(tmp_path/'private.sqlite')
    store.begin('projected', 'original')
    store.put(public, sidecar, admission_input=admission)
    store.commit()
    assert store.restore('projected', public) == original
    store.close()


def test_selected_compile_keeps_research_available_to_next_incremental_build(tmp_path):
    import copy
    import pytest
    from test_instruction_owner_store import fixture
    from knowledge.runtime_fact_publication import OwnerStoreWriter
    from plc.instruction_owner_store import OwnerDefinitionStore
    from scripts.compile_instruction_semantics import compile_semantics
    raw = fixture()
    selected = raw['entries'][0]
    unresolved = copy.deepcopy(selected['facts'][0])
    unresolved.update(id='authored:pending', status='candidate_evidence', verification='not_performed',
                      value={'unresolved_items': ['layout'], 'known_parameter': 'S'})
    selected['facts'].append(unresolved)
    negative = copy.deepcopy(selected['facts'][0])
    negative.update(id='authored:negative', value={'available': False, 'error_code': 4100})
    selected['facts'].append(negative)
    writer = OwnerStoreWriter(tmp_path/'input.sqlite', {k: v for k, v in raw.items() if k != 'entries'})
    for entry in raw['entries']:
        writer.add_entry(entry)
    manifest = tmp_path/'instruction_definitions.manifest.json'
    writer.finalize(manifest)
    writer.close()
    archive = tmp_path/'research.sqlite'
    owner = (selected['target_model'], selected['opcode'])
    first = compile_semantics(manifest, tmp_path/'first', version='synthetic:private-first',
        source_batches={owner: [unresolved, negative]}, source_owners_only=True, research_archive=archive)
    output = OwnerDefinitionStore(first['manifest'])
    public = output.get_entry(selected['opcode'], cpu=selected['target_model'])
    assert 'authored:pending' not in {f['id'] for f in public['facts']}
    assert next(f for f in public['facts'] if f['id'] == negative['id'])['value'] == negative['value']
    assert output.get_entry(raw['entries'][1]['opcode'], cpu=raw['entries'][1]['target_model']) == raw['entries'][1]
    output.close()
    with pytest.raises(ValueError, match='requires its private research archive'):
        compile_semantics(first['manifest'], tmp_path/'without-private', version='synthetic:missing',
                          source_batches={owner: []}, source_owners_only=True)
    second = compile_semantics(first['manifest'], tmp_path/'second', version='synthetic:private-second',
        source_batches={owner: []}, source_owners_only=True, research_archive=archive)
    output = OwnerDefinitionStore(second['manifest'])
    public = output.get_entry(selected['opcode'], cpu=selected['target_model'])
    assert 'authored:pending' not in {f['id'] for f in public['facts']}
    saved = RuntimeResearchArchive(archive)
    restored = saved.restore(output.fact_bundle_version, public)
    assert next(f for f in restored['facts'] if f['id'] == unresolved['id']) == unresolved
    assert next(f for f in restored['facts'] if f['id'] == negative['id'])['value'] == negative['value']
    saved.close()
    output.close()


def test_initial_and_incremental_language_partition_preserves_runtime_limits_and_archives_research(tmp_path):
    import copy
    import pytest
    from test_instruction_owner_store import fixture
    from test_runtime_fact_publication import language_feature
    from knowledge.runtime_fact_publication import OwnerStoreWriter, project_language_features
    from plc.instruction_owner_store import OwnerDefinitionStore
    from scripts.compile_instruction_semantics import (
        _restore_language_feature, compile_semantics,
    )

    raw = fixture()
    feature = language_feature('st')
    base = feature['facts'][0]
    negative = copy.deepcopy(base)
    negative.update(id='language:known-negative', dimension='constraints.availability',
        value={'available': False, 'error_code': 4100},
        conditions=[{'field': 'cpu_version', 'minimum': '1.00'}])
    off_state = copy.deepcopy(base)
    off_state.update(id='language:off-state', dimension='constraints.project_option',
        value={'state': 'OFF', 'available': False},
        conditions=[{'field': 'cpu_version', 'minimum': '1.00'}])
    unimplemented = copy.deepcopy(base)
    unimplemented.update(id='language:unimplemented', dimension='execution.core_execution_capability',
        value={'implementation_status': 'not_implemented'},
        conditions=[{'field': 'plc_model', 'equals': 'FX5U'}])
    contextual = copy.deepcopy(base)
    contextual.update(id='language:contextual-limit', dimension='constraints.documented_requirements',
        value={'predicate': {'operator': 'requires', 'item': 'selected_hardware'},
            'required_context': [{'field': 'hardware_configuration'}],
            'unresolved_items': ['current_hardware_configuration'],
            'branch_selection_is_not_hardware_validation': True},
        conditions=[{'field': 'plc_model', 'equals': 'FX5U'}])
    research = copy.deepcopy(base)
    research.update(id='language:private-research', status='candidate_evidence',
        verification='not_performed', value={'unresolved_items': ['research_checklist']})
    feature['facts'] = [negative, off_state, unimplemented, contextual, research]
    history = [{'id': 'language:replaced-research', 'claim': 'previous extraction'}]

    source_manifest = tmp_path/'input'/'instruction_definitions.manifest.json'
    source_manifest.parent.mkdir()
    database = source_manifest.parent/'plc_facts.sqlite'
    metadata = {key: value for key, value in raw.items() if key != 'entries'}
    metadata['superseded_language_facts'] = {'ST.CALL': history}
    writer = OwnerStoreWriter(database, metadata, field_pooling=True, search_pooling=True, fact_rows=True)
    for entry in raw['entries']:
        writer.add_entry(entry)
    published_feature = project_language_features([feature])[0][0]
    writer.add_language_feature(published_feature)
    writer.finalize(source_manifest)

    archive_path = tmp_path/'research.sqlite'
    first = compile_semantics(source_manifest, tmp_path/'first', version='synthetic:language-partition',
        research_archive=archive_path, partition_runtime_research=True)
    output = OwnerDefinitionStore(first['manifest'])
    runtime_feature = output._language_rebuild_source('ST.CALL')
    runtime_facts = {fact['id']: fact for fact in runtime_feature['facts']}
    assert set(runtime_facts) == {
        'language:known-negative', 'language:off-state', 'language:unimplemented', 'language:contextual-limit'}
    assert runtime_facts['language:known-negative']['value'] == negative['value']
    assert runtime_facts['language:known-negative']['conditions'] == negative['conditions']
    assert runtime_facts['language:off-state']['value'] == off_state['value']
    assert runtime_facts['language:unimplemented']['value'] == unimplemented['value']
    assert runtime_facts['language:contextual-limit']['value']['unresolved_items'] == ['current_hardware_configuration']
    assert 'superseded_language_facts' not in output.definition_metadata
    assert output.definition_metadata['research_partition'] == 'private-runtime-research-v1'

    archive = RuntimeResearchArchive(archive_path)
    restored, restored_history = _restore_language_feature(
        runtime_feature, archive, output.fact_bundle_version)
    expected = project_language_features([feature])[0][0]
    assert restored == expected
    assert restored_history == history
    archive.close()
    output.close()

    addition = copy.deepcopy(base)
    addition.update(id='language:next-independent-rule', dimension='constraints.range',
        value={'minimum': 1, 'maximum': 8}, conditions=[{'field': 'cpu_version', 'minimum': '1.00'}])
    incoming = copy.deepcopy(feature)
    incoming['facts'] = [addition]
    second = compile_semantics(first['manifest'], tmp_path/'second', version='synthetic:language-incremental',
        language_batches=[incoming], source_owners_only=True, research_archive=archive_path)
    output = OwnerDefinitionStore(second['manifest'])
    runtime_feature = output._language_rebuild_source('ST.CALL')
    runtime_facts = {fact['id']: fact for fact in runtime_feature['facts']}
    assert set(runtime_facts) == {
        'language:known-negative', 'language:off-state', 'language:unimplemented',
        'language:contextual-limit', 'language:next-independent-rule'}
    assert runtime_facts['language:known-negative']['value']['available'] is False
    assert runtime_facts['language:contextual-limit']['conditions'] == contextual['conditions']
    archive = RuntimeResearchArchive(archive_path)
    restored, restored_history = _restore_language_feature(
        runtime_feature, archive, output.fact_bundle_version)
    assert {fact['id'] for fact in restored['facts']} == {
        'language:known-negative', 'language:contextual-limit',
        'language:off-state', 'language:unimplemented',
        'language:private-research', 'language:next-independent-rule'}
    assert restored_history == history
    archive.close()
    output.close()

    with pytest.raises(ValueError, match='cannot establish or upgrade'):
        compile_semantics(source_manifest, tmp_path/'selected-first-partition',
            source_batches={}, source_owners_only=True,
            research_archive=archive_path, version='synthetic:invalid-partial-partition')
