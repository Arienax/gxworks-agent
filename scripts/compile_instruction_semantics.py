"""Compile programming semantics into an existing v4 owner-store format.

The source index is an offline compilation input. Runtime retrieval reads only
the resulting owner store and the ordinary instruction catalogue resources.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]

_RUNTIME_RESEARCH_PARTITION = 'private-runtime-research-v1'
_LANGUAGE_RESEARCH_VENDOR = 'language-feature'


def _fact_claim(fact):
    return json.dumps({key: value for key, value in fact.items() if key != 'sources'},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def compact_fact_history(facts, history):
    """Keep changed claims; identical claims already retain their old sources."""
    current = {fact['id']: fact for fact in facts}
    retained = []
    source_key = lambda source: json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    for previous in history:
        active = current.get(previous.get('id'))
        if (active is not None and _fact_claim(previous) == _fact_claim(active)
                and {source_key(s) for s in previous.get('sources', [])}
                    <= {source_key(s) for s in active.get('sources', [])}):
            continue
        retained.append(previous)
    return retained


def merge_source_facts(existing, incoming):
    """Refresh compatible extractions, keeping real disagreements as variants."""
    from knowledge.structured_facts import _merge_fact_rows
    from plc.instruction_relations import _source_related

    def refines(before, after):
        if isinstance(before, dict):
            if not isinstance(after, dict):
                return False
            # These fields describe how the same parameter cell was extracted.
            # A parser-method change is not a disagreement about its type or
            # direction; the old method remains in superseded_facts.
            parameter = all(key in before and key in after for key in ('name', 'type', 'direction'))
            return all(k in after and (
                parameter and k in {'type_basis', 'direction_basis'}
                and isinstance(v, str) and isinstance(after[k], str)
                or refines(v, after[k])) for k, v in before.items())
        if isinstance(before, list):
            return isinstance(after, list) and len(before) == len(after) and all(refines(a, b) for a, b in zip(before, after))
        return before == after and type(before) is type(after) or before in (None, 'unknown', 'not_established')

    facts, originals = copy.deepcopy(existing), []
    positions = {f['id']: i for i, f in enumerate(facts)}
    pending = []
    for fresh in incoming:
        index = positions.get(fresh['id'])
        old = facts[index] if index is not None else None
        compatible = old is not None and all(old.get(k, []) == fresh.get(k, []) for k in
            ('dimension', 'scope', 'conditions'))
        review_strength = {'unknown': 0, 'declared_unverified': 1, 'candidate_evidence': 2, 'source_verified': 3}
        reviewed = (old is not None and old.get('status') in review_strength
                    and fresh.get('status') in review_strength
                    and review_strength[fresh['status']] >= review_strength[old['status']])
        if (compatible and reviewed and fresh.get('verification') == 'source_checked' and _source_related(old, fresh)
                and refines(old.get('value', {}), fresh.get('value', {}))):
            updated = copy.deepcopy(old)
            updated['value'] = copy.deepcopy(fresh['value'])
            updated['status'], updated['verification'] = fresh['status'], fresh['verification']
            for key in ('sources', 'depends_on'):
                merged = list(old.get(key, []))
                merged.extend(item for item in fresh.get(key, []) if item not in merged)
                if merged:
                    updated[key] = copy.deepcopy(merged)
            if updated != old:
                if _fact_claim(updated) != _fact_claim(old):
                    originals.append(copy.deepcopy(old))
                facts[index] = updated
        else:
            pending.append(fresh)
    _merge_fact_rows(facts, pending)
    return facts, originals


def replace_source_namespaces(existing, incoming, prefixes):
    """Install complete offline extractor snapshots, retaining old claims for debug.

    This is explicit re-authoring, not the partial-source merge above. A current
    extraction may correct or demote an earlier extraction of the same page.
    Conflicts inside the fresh snapshot remain conflicts.
    """
    from knowledge.fact_corpus import source_reference
    prefixes = _complete_source_prefixes(prefixes)
    owned = {fact['id']: fact for fact in existing if fact['id'].startswith(prefixes)}
    fresh = [copy.deepcopy(fact) for fact in incoming if fact['id'].startswith(prefixes)]
    if len({fact['id'] for fact in fresh}) != len(fresh):
        raise ValueError('Source snapshot contains duplicate fact identities')
    kept = [copy.deepcopy(fact) for fact in existing if not fact['id'].startswith(prefixes)]
    current_ids = {fact['id'] for fact in fresh}
    removed = set(owned) - current_ids
    from plc.instruction_relations import DERIVED_PREFIXES
    by_id = {fact['id']: fact for fact in [*kept, *fresh]}
    roots = [fact for fact in [*kept, *fresh] if not fact['id'].startswith(DERIVED_PREFIXES)]
    pending = [identity for fact in roots for identity in fact.get('depends_on', [])]
    visited, external = set(), set()
    while pending and removed:
        identity = pending.pop()
        if identity in visited:
            continue
        visited.add(identity)
        if identity in removed:
            external.add(identity)
        elif identity in by_id:
            pending.extend(by_id[identity].get('depends_on', []))
    if external:
        raise ValueError('Source snapshot removes external prerequisites: ' + ','.join(sorted(external)))
    history = []
    for fact in fresh:
        fact['sources'] = [source_reference(source) if source.get('manual_id') else source
                           for source in fact.get('sources', [])]
        previous = owned.get(fact['id'])
        if previous is not None and (_fact_claim(previous) != _fact_claim(fact)
                or any(source not in fact['sources'] for source in previous.get('sources', []))):
            history.append(copy.deepcopy(previous))
    history.extend(copy.deepcopy(owned[identity]) for identity in owned if identity in removed)
    other = [fact for fact in incoming if not fact['id'].startswith(prefixes)]
    current, refined = merge_source_facts([*kept, *fresh], other)
    return current, [*history, *refined]


def _complete_source_prefixes(prefixes):
    import re
    prefixes = tuple(prefixes)
    if not prefixes or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:[.:][A-Za-z_][A-Za-z0-9_]*)*[.:]', prefix)
                           for prefix in prefixes):
        raise ValueError('Expected a complete source extractor namespace')
    return prefixes


def load_source_facts(paths, *, replace_source_prefixes=(), return_owner_replacements=False):
    """Load one or more offline bundles, merging facts by exact owner.

    Partial batches from multiple packages are combined with the same conflict-
    preserving merge used during compilation. A replacement prefix names a
    complete snapshot, so two packages cannot both author that namespace for
    the same instruction or language owner in one invocation.
    """
    prefixes = _complete_source_prefixes(replace_source_prefixes) if replace_source_prefixes else ()
    batches, languages = {}, None
    instruction_snapshots, language_snapshots = {}, {}
    empty_owner_replacements = {}
    package_owner_facts = {}

    def register_snapshot(snapshots, owner, facts, package_index):
        present = {prefix for prefix in prefixes
                   if any(fact['id'].startswith(prefix) for fact in facts)}
        for prefix in present:
            key = (owner, prefix)
            previous = snapshots.get(key)
            if previous is not None and previous != package_index:
                raise ValueError(
                    f'Ambiguous replacement snapshot for owner {owner!r} and namespace {prefix!r}: '
                    'multiple --source-facts packages provide it')
        for prefix in present:
            snapshots[(owner, prefix)] = package_index

    for package_index, path in enumerate(paths):
        source_data = json.loads(Path(path).read_text(encoding='utf-8'))
        current_package_owner_facts = {}
        for entry in source_data.get('entries', []):
            owner = (entry['target_model'], entry['opcode'])
            incoming = entry['facts']
            current_package_owner_facts.setdefault(owner, []).extend(incoming)
            package_owner_facts.setdefault((package_index, owner), []).extend(incoming)
            register_snapshot(instruction_snapshots, owner, incoming, package_index)
            if owner in batches:
                batches[owner], _ = merge_source_facts(batches[owner], incoming)
            else:
                batches[owner] = copy.deepcopy(incoming)

        declared_replacements = source_data.get('empty_source_replacements', [])
        if not isinstance(declared_replacements, list):
            raise ValueError('empty_source_replacements must be a list')
        seen_replacements = set()
        for declaration in declared_replacements:
            if not isinstance(declaration, dict) or set(declaration) != {
                    'target_model', 'opcode', 'prefix'}:
                raise ValueError('Invalid empty source replacement declaration')
            if (any(not isinstance(declaration.get(key), str) or not declaration[key]
                    for key in ('target_model', 'opcode', 'prefix'))):
                raise ValueError('Empty source replacement requires exact owner strings and a prefix')
            owner = (declaration['target_model'], declaration['opcode'])
            prefix, = _complete_source_prefixes([declaration['prefix']])
            key = (owner, prefix)
            if key in seen_replacements:
                raise ValueError('Duplicate empty source replacement declaration')
            seen_replacements.add(key)
            if prefix in prefixes:
                raise ValueError('An exact-owner empty snapshot cannot also be a global replacement prefix')
            if owner not in current_package_owner_facts:
                raise ValueError('Empty source replacement owner has no entry in its package')
            if any(fact['id'].startswith(prefix) for fact in current_package_owner_facts[owner]):
                raise ValueError('Empty source replacement contains facts for its namespace')
            previous = instruction_snapshots.get(key)
            if previous is not None and previous != package_index:
                raise ValueError(
                    f'Ambiguous replacement snapshot for owner {owner!r} and namespace {prefix!r}: '
                    'multiple --source-facts packages provide it')
            instruction_snapshots[key] = package_index
            empty_owner_replacements[key] = package_index

        incoming_languages = source_data.get('language_facts')
        if incoming_languages is None:
            continue
        seen_targets = set()
        for feature in incoming_languages:
            target = feature['target']
            if target in seen_targets:
                raise ValueError('Duplicate incoming language feature target')
            seen_targets.add(target)
            register_snapshot(language_snapshots, target, feature.get('facts', []), package_index)
        if languages is None:
            languages = copy.deepcopy(incoming_languages)
        else:
            languages, _ = merge_language_features(languages, incoming_languages)
    for (owner, prefix), declaration_package in empty_owner_replacements.items():
        if any(fact_package != declaration_package and fact_owner == owner
               and any(fact['id'].startswith(prefix) for fact in rows)
               for (fact_package, fact_owner), rows in package_owner_facts.items()):
            raise ValueError(
                f'Ambiguous replacement snapshot for owner {owner!r} and namespace {prefix!r}: '
                'another --source-facts package provides that namespace')
    if return_owner_replacements:
        return batches, languages, frozenset(
            (owner[0], owner[1], prefix) for owner, prefix in empty_owner_replacements)
    return batches, languages



def merge_language_features(existing, incoming, *, replace_source_prefixes=()):
    """Compose source namespaces within the existing exact language owners."""
    from knowledge.runtime_fact_publication import validate_language_feature, project_language_features
    current={feature['target']:copy.deepcopy(feature) for feature in existing}
    fresh={}
    for feature in incoming:
        validate_language_feature(feature)
        if feature['target'] in fresh:raise ValueError('Duplicate incoming language feature target')
        fresh[feature['target']]=copy.deepcopy(feature)
    history={}
    for target in sorted(set(current)|set(fresh)):
        previous=current.get(target)
        addition=fresh.get(target)
        if previous is None:
            current[target]=addition
            continue
        if addition and previous.get('language') and previous['language']!=addition.get('language',target.split('.')[0].lower()):
            raise ValueError('Language owner namespace differs')
        facts=addition.get('facts',[]) if addition else []
        owner_prefixes = tuple(prefix for prefix in replace_source_prefixes
                               if any(f['id'].startswith(prefix) for f in facts))
        if owner_prefixes:
            merged,replaced=replace_source_namespaces(previous['facts'],facts,owner_prefixes)
        elif addition:
            merged,replaced=merge_source_facts(previous['facts'],facts)
        else:
            continue
        previous['facts']=merged
        if replaced:history[target]=replaced
        if addition:
            for key in ('aliases','references'):
                for item in addition.get(key,[]):
                    if item not in previous.setdefault(key,[]):previous[key].append(copy.deepcopy(item))
            if 'models' in previous:
                for model in addition.get('models',sorted({m for f in facts for m in f['scope']['models']})):
                    if model not in previous['models']:previous['models'].append(model)
    projected,withheld=project_language_features(list(current.values()))
    if withheld:raise ValueError('Language source refresh withholds technical content: '+str(withheld))
    return projected,history


def _language_archive_entry(feature, *, superseded_facts=None):
    """Give a language feature a stable archive identity without changing its schema."""
    target = feature.get('target')
    if not isinstance(target, str) or '.' not in target:
        raise ValueError('Language archive requires an exact feature target')
    language = feature.get('language') or target.partition('.')[0].lower()
    entry = copy.deepcopy(feature)
    entry.update(vendor=_LANGUAGE_RESEARCH_VENDOR, opcode=target, target_model=language)
    if superseded_facts:
        entry['superseded_facts'] = copy.deepcopy(superseded_facts)
    return entry


def _language_feature_from_archive(entry):
    feature = copy.deepcopy(entry)
    for field in ('vendor', 'opcode', 'target_model', 'superseded_facts'):
        feature.pop(field, None)
    return feature


def _restore_language_feature(feature, archive, version):
    """Restore the prior complete language compile input and its private history."""
    entry = archive.restore(version, _language_archive_entry(feature))
    history = entry.pop('superseded_facts', [])
    return _language_feature_from_archive(entry), history


def _project_language_feature(feature, archive, *, superseded_facts=()):
    """Project one language row through the same strict owner policy and sidecar."""
    from knowledge.runtime_fact_projection import project_runtime_owner
    from knowledge.runtime_fact_publication import validate_language_feature

    entry = _language_archive_entry(feature, superseded_facts=superseded_facts)
    public, sidecar, projection = project_runtime_owner(
        entry, external_id_references=[fact['id'] for fact in feature.get('facts', [])])
    archive.put(public, sidecar)
    result = _language_feature_from_archive(public)
    validate_language_feature(result, published=True)
    return result, projection


def compile_semantics(manifest, output_dir, *, source_index=None, source_pdf_root=None,
                      source_batches=None, language_batches=None, replace_source_prefixes=(),
                      refresh_reviewed_calls=False, source_owners_only=False, version, progress=None,
                      research_archive=None, refresh_structured_calls=False,
                      partition_runtime_research=False, empty_source_replacements=()):
    from knowledge.runtime_fact_publication import OwnerStoreWriter
    from plc.instruction_owner_store import OwnerDefinitionStore, MANIFEST_NAME
    from plc.instruction_knowledge import compose_programming_facts
    from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
    from tools.fact_fx5_signatures import MANUAL_ID, _geometry_operands, enrich_operand_storage
    from tools.fact_instruction_catalogue import instruction_cpu_catalogue, catalogue_availability_facts
    from tools.fact_applied_review import reviewed_applied_behavior, enrich_compiled_native_parameters

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target_manifest = output_dir / MANIFEST_NAME
    target_database = output_dir / 'plc_facts.sqlite'
    if target_manifest.exists() or target_database.exists():
        raise ValueError('Compilation output already exists')
    if partition_runtime_research and research_archive is None:
        raise ValueError('Initial runtime research partition requires --research-archive')
    if partition_runtime_research and source_owners_only:
        raise ValueError('Initial runtime research partition requires a full compile, not --source-owners-only')
    source_rows, catalogue, source_facts = {}, {}, {}
    checked_empty_replacements = []
    for declaration in empty_source_replacements:
        if not isinstance(declaration, (tuple, list)) or len(declaration) != 3:
            raise ValueError('Expected exact owner and complete namespace for empty source replacement')
        model, opcode, prefix = declaration
        if any(not isinstance(value, str) or not value for value in (model, opcode, prefix)):
            raise ValueError('Empty source replacement requires exact owner strings and a prefix')
        _complete_source_prefixes([prefix])
        checked_empty_replacements.append((model, opcode, prefix))
    if len(set(checked_empty_replacements)) != len(checked_empty_replacements):
        raise ValueError('Duplicate exact-owner empty source replacement')
    empty_source_replacements = tuple(checked_empty_replacements)
    for model, opcode, prefix in empty_source_replacements:
        owner = (model, opcode)
        if owner not in (source_batches or {}):
            raise ValueError('Empty source replacement owner is absent from source batches: ' + repr(owner))
        if any(fact['id'].startswith(prefix) for fact in (source_batches or {})[owner]):
            raise ValueError('Empty source replacement batch contains facts for its namespace: ' + repr(owner))
    for prefix in replace_source_prefixes:
        if not any(fact['id'].startswith(prefix) for batch in (source_batches or {}).values() for fact in batch) and not any(
                fact['id'].startswith(prefix) for feature in (language_batches or []) for fact in feature['facts']):
            raise ValueError('Replacement namespace is absent from the source snapshot: ' + prefix)
    if source_index is not None:
        connection = sqlite3.connect(Path(source_index).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            for page, words in connection.execute(
                    'SELECT pdf_page,word_geometry_json FROM page_artifacts WHERE manual_id=?', (MANUAL_ID,)):
                rows = _geometry_operands(json.loads(words or '[]'))
                if rows:
                    source_rows[page] = rows
            catalogue = instruction_cpu_catalogue(connection, pdf_root=source_pdf_root)
            connection.row_factory = sqlite3.Row
            source_facts = reviewed_applied_behavior(connection, manuals=['fx3_programming_r'],
                                                     cpu_catalogue=catalogue, source_pdf_root=source_pdf_root)[0]
        finally:
            connection.close()
    store = OwnerDefinitionStore(manifest)
    writer = None
    archive = None
    counts = Counter()
    try:
        # A seeded instruction-only update preserves the language tables as-is.
        # Loading unrelated language bodies here would block an owner update on
        # a feature which this invocation neither selects nor changes.
        metadata = store.definition_metadata if source_owners_only else store.metadata
        existing_partition = metadata.get('research_partition')
        if existing_partition and research_archive is None:
            raise ValueError('This runtime requires its private research archive for recompilation')
        if research_archive is not None:
            if source_owners_only and existing_partition != _RUNTIME_RESEARCH_PARTITION:
                raise ValueError('A selected compile cannot establish or upgrade the full runtime research partition; run one full partition first')
            from tools.runtime_research_archive import RuntimeResearchArchive
            archive = RuntimeResearchArchive(research_archive)
            archive.begin(version, store.fact_bundle_version)
            metadata['research_partition'] = _RUNTIME_RESEARCH_PARTITION
        languages = metadata.pop('language_facts', [])
        private_language_history = {}
        if archive is not None:
            previous_history = metadata.pop('superseded_language_facts', {})
            if not isinstance(previous_history, dict) or any(
                    not isinstance(target, str) or not isinstance(rows, list)
                    for target, rows in previous_history.items()):
                raise ValueError('Invalid language research history in runtime metadata')
            private_language_history = copy.deepcopy(previous_history)
        if source_owners_only and language_batches is not None:
            # Rebuild selected language owners, then validate their complete
            # replacements. Unselected language payloads stay in the seed.
            targets = {feature['target'] for feature in language_batches}
            languages = [feature for target in sorted(targets)
                         if (feature := store._language_rebuild_source(target)) is not None]
        if archive is not None:
            if source_owners_only:
                selected_targets = ({feature['target'] for feature in language_batches}
                                    if language_batches is not None else set())
                if set(private_language_history) - selected_targets:
                    raise ValueError('Language research history outside the selected owners requires a full runtime partition')
            restored_languages = []
            for feature in languages:
                feature, history = _restore_language_feature(feature, archive, store.fact_bundle_version)
                restored_languages.append(feature)
                if history:
                    existing = private_language_history.setdefault(feature['target'], [])
                    existing.extend(row for row in history if row not in existing)
            languages = restored_languages
        if language_batches is not None:
            languages, language_history = merge_language_features(
                languages, language_batches, replace_source_prefixes=replace_source_prefixes)
            if archive is not None:
                for target, rows in language_history.items():
                    history = private_language_history.setdefault(target, [])
                    history.extend(row for row in rows if row not in history)
            elif language_history:
                previous = metadata.setdefault('superseded_language_facts', {})
                for target, rows in language_history.items():
                    history = previous.setdefault(target, [])
                    history.extend(row for row in rows if row not in history)
        if archive is not None:
            feature_targets = {feature['target'] for feature in languages}
            if set(private_language_history) - feature_targets:
                raise ValueError('Language research history has no matching language feature owner')
        metadata['fact_bundle_version'] = version
        writer = OwnerStoreWriter(target_database, metadata, field_pooling=True, search_pooling=True, fact_rows=True,
                                  seed_database=store.database_path if source_owners_only else None)
        remaining_sources = set(source_batches or {})
        def write_entry(entry):
            if entry.get('superseded_facts'):
                history = entry['superseded_facts']
                entry['superseded_facts'] = compact_fact_history(entry['facts'], history)
                counts['redundant_history_removed'] += len(history) - len(entry['superseded_facts'])
            if archive is not None:
                from knowledge.runtime_fact_projection import project_runtime_owner
                from knowledge.instruction_compiler import (refresh_reviewed_call_admission,
                    refresh_reviewed_structured_call_admission,
                    refresh_reviewed_structured_expression_admission,
                    refresh_reviewed_fbd_instruction_block_admission)
                public, sidecar, projection = project_runtime_owner(entry,
                    external_id_references=[fact['id'] for fact in entry.get('facts', [])])
                admission_input = {}
                refresher = (refresh_reviewed_fbd_instruction_block_admission
                             if public.get('support_status') == 'reviewed_fbd_signature'
                             else refresh_reviewed_structured_expression_admission
                             if public.get('support_status') == 'reviewed_st_expression'
                             else refresh_reviewed_structured_call_admission
                             if public.get('support_status') == 'reviewed_st_signature'
                             else refresh_reviewed_call_admission)
                admission_valid = (public.get('call_available') is not True
                    or refresher(copy.deepcopy(public), DEFAULT_INSTRUCTION_REGISTRY))
                fbd_marker = (public.get('call_admission_details') or {}).get('fbd_call')
                if (admission_valid and isinstance(fbd_marker, dict)
                        and public.get('support_status') != 'reviewed_fbd_signature'):
                    fbd_check = copy.deepcopy(public)
                    if not refresh_reviewed_fbd_instruction_block_admission(
                            fbd_check, DEFAULT_INSTRUCTION_REGISTRY):
                        public = fbd_check
                if public.get('call_available') is True and not admission_valid:
                    # Withholding supporting evidence cannot leave an inherited
                    # compatibility True pretending to be reviewed admission.
                    for field in ('call_available', 'support_status', 'call_admission_reason', 'call_admission_details'):
                        admission_input[field] = {'present': field in public,
                                                  'value': copy.deepcopy(public.get(field))}
                    public.update(call_available=False, support_status='definition_only',
                        call_admission_reason='knowledge_only_not_admitted', call_admission_details={})
                    counts['publication_calls_withheld'] += 1
                archive.put(public, sidecar, admission_input=admission_input)
                counts['research_facts_withheld'] += projection['withheld_facts']
                counts['research_fields_moved'] += projection['research_fields_moved']
                entry = public
            if source_owners_only:writer.replace_entry(entry)
            else:writer.add_entry(entry)
        headers = ({'vendor': 'mitsubishi', 'opcode': opcode, 'target_model': model}
                   for model, opcode in sorted(source_batches or {})) if source_owners_only else store.iter_owner_headers()
        for header in headers:
            entry = store.get_entry(header['opcode'], cpu=header['target_model'], vendor=header['vendor'])
            if entry is None:continue
            if archive is not None:
                entry = archive.restore(store.fact_bundle_version, entry)
            fresh = source_facts.get((entry['target_model'], entry['opcode']), [])
            if fresh:
                entry['facts'], changed = enrich_compiled_native_parameters(entry.get('facts', []), fresh)
                counts['source_parameter_tables_enriched'] += len(changed)
                if changed:
                    entry.setdefault('superseded_facts', []).extend(changed)
            batch = (source_batches or {}).get((entry['target_model'], entry['opcode']), [])
            remaining_sources.discard((entry['target_model'], entry['opcode']))
            owner = (entry['target_model'], entry['opcode'])
            owner_prefixes = [prefix for model, opcode, prefix in empty_source_replacements
                              if (model, opcode) == owner]
            # A batch may contain different complete namespaces for different
            # owners. Absence here is not an empty snapshot; retiring a whole
            # namespace requires the explicit exact-owner declaration above.
            present_prefixes = [prefix for prefix in replace_source_prefixes
                                if any(fact['id'].startswith(prefix) for fact in batch)]
            replacement_prefixes = tuple(dict.fromkeys([*present_prefixes, *owner_prefixes]))
            replaces_owned = replacement_prefixes and any(
                fact['id'].startswith(replacement_prefixes) for fact in entry.get('facts', []))
            if batch or replaces_owned:
                if replacement_prefixes:
                    entry['facts'], changed = replace_source_namespaces(
                        entry.get('facts', []), batch, replacement_prefixes)
                else:
                    entry['facts'], changed = merge_source_facts(entry.get('facts', []), batch)
                counts['source_recompiled_owners'] += 1
                counts['source_extractions_refined'] += len(changed)
                if changed:
                    entry.setdefault('superseded_facts', []).extend(changed)
            availability = catalogue_availability_facts(entry['opcode'], entry.get('facts', []),
                                                        entry['target_model'], catalogue)
            if availability:
                by_id = {fact['id']: fact for fact in entry.get('facts', [])}
                by_id.update((fact['id'], fact) for fact in availability)
                entry['facts'] = list(by_id.values())
                counts['cpu_catalogue_bindings'] += 1
            counts['parameter_storage_fields_added'] += enrich_operand_storage(entry.get('facts', []), entry['opcode'], source_rows)
            before = len(entry.get('facts', []))
            entry['facts'], replaced = compose_programming_facts(DEFAULT_INSTRUCTION_REGISTRY,
                entry['opcode'], cpu=entry['target_model'], existing=entry.get('facts', []))
            if replaced:
                entry.setdefault('superseded_facts', []).extend(replaced)
            if refresh_reviewed_calls and (entry['target_model'], entry['opcode']) in (source_batches or {}):
                from knowledge.instruction_compiler import refresh_reviewed_call_admission, withdraw_native_call_admission
                previous_facts = entry['facts']
                if refresh_reviewed_call_admission(entry, DEFAULT_INSTRUCTION_REGISTRY):
                    counts['reviewed_call_contracts_refreshed'] += 1
                    if entry['facts'] != previous_facts:
                        entry['facts'], replaced = compose_programming_facts(DEFAULT_INSTRUCTION_REGISTRY,
                            entry['opcode'], cpu=entry['target_model'], existing=entry['facts'])
                        if replaced:
                            entry.setdefault('superseded_facts', []).extend(replaced)
                elif withdraw_native_call_admission(entry):
                    counts['unqualified_native_admissions_withdrawn'] += 1
            if refresh_structured_calls and (entry['target_model'], entry['opcode']) in (source_batches or {}):
                from knowledge.instruction_compiler import (refresh_reviewed_structured_call_admission,
                    refresh_reviewed_structured_expression_admission,
                    refresh_reviewed_fbd_instruction_block_admission)
                if refresh_reviewed_structured_call_admission(entry, DEFAULT_INSTRUCTION_REGISTRY):
                    counts['reviewed_structured_contracts_refreshed'] += 1
                if refresh_reviewed_structured_expression_admission(entry, DEFAULT_INSTRUCTION_REGISTRY):
                    counts['reviewed_structured_expressions_refreshed'] += 1
                if refresh_reviewed_fbd_instruction_block_admission(entry, DEFAULT_INSTRUCTION_REGISTRY):
                    counts['reviewed_fbd_contracts_refreshed'] += 1
            counts['programming_owners_recompiled'] += 1
            counts['net_added_facts'] += len(entry['facts']) - before
            counts['owners'] += 1
            write_entry(entry)
            if progress and counts['owners'] % 5000 == 0:
                progress(dict(counts))
        from knowledge.instruction_compiler import source_knowledge_entry
        for model, opcode in sorted(remaining_sources):
            batch = source_batches[(model, opcode)]
            if not batch:
                continue
            entry = source_knowledge_entry(opcode, model, batch,
                registry_member=DEFAULT_INSTRUCTION_REGISTRY.resolve_catalog_form(opcode) is not None)
            entry['facts'], replaced = compose_programming_facts(DEFAULT_INSTRUCTION_REGISTRY,
                opcode, cpu=model, existing=entry['facts'])
            if replaced:
                entry['superseded_facts'] = replaced
            if refresh_reviewed_calls:
                from knowledge.instruction_compiler import refresh_reviewed_call_admission, withdraw_native_call_admission
                if refresh_reviewed_call_admission(entry, DEFAULT_INSTRUCTION_REGISTRY):
                    counts['reviewed_call_contracts_refreshed'] += 1
                elif withdraw_native_call_admission(entry):
                    counts['unqualified_native_admissions_withdrawn'] += 1
            if refresh_structured_calls:
                from knowledge.instruction_compiler import (refresh_reviewed_structured_call_admission,
                    refresh_reviewed_structured_expression_admission,
                    refresh_reviewed_fbd_instruction_block_admission)
                if refresh_reviewed_structured_call_admission(entry, DEFAULT_INSTRUCTION_REGISTRY):
                    counts['reviewed_structured_contracts_refreshed'] += 1
                if refresh_reviewed_structured_expression_admission(entry, DEFAULT_INSTRUCTION_REGISTRY):
                    counts['reviewed_structured_expressions_refreshed'] += 1
                if refresh_reviewed_fbd_instruction_block_admission(entry, DEFAULT_INSTRUCTION_REGISTRY):
                    counts['reviewed_fbd_contracts_refreshed'] += 1
            write_entry(entry)
            counts['source_owners_added'] += 1
            counts['source_recompiled_owners'] += 1
            counts['owners'] += 1
        for feature in languages:
            # An explicitly selected rebuild also restores a legacy partial index.
            if archive is not None:
                feature, projection = _project_language_feature(
                    feature, archive,
                    superseded_facts=private_language_history.get(feature['target'], ()))
                counts['language_features_projected'] += 1
                counts['language_research_facts_withheld'] += projection['withheld_facts']
                counts['language_research_fields_moved'] += projection['research_fields_moved']
            writer.add_language_feature(feature, replace=source_owners_only)
        result = writer.finalize(target_manifest)
        if archive is not None:
            archive.commit()
        changed = counts['programming_owners_recompiled'] + counts['source_owners_added']
        return {**counts, 'owners': result['sqlite']['owners'], 'changed_owners': changed,
                'unchanged_owners_retained': result['sqlite']['owners']-changed if source_owners_only else 0,
                'owner_access': store.stats(), 'incremental_seeded': bool(source_owners_only),
                'manifest': str(target_manifest), 'stored_facts': result['sqlite']['stored_facts']}
    finally:
        store.close()
        if writer is not None:
            writer.close()
        if archive is not None:
            archive.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-manifest', type=Path, required=True)
    parser.add_argument('--source-index', type=Path)
    parser.add_argument('--source-pdf-root', type=Path)
    parser.add_argument('--source-facts', type=Path, action='append', default=[],
                        help='Offline authored bundle containing instruction entries and/or language_facts (may be repeated)')
    parser.add_argument('--replace-source-prefix', action='append', default=[],
                        help='Replace one complete authored extractor namespace; previous claims remain in debug history')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--refresh-reviewed-calls', action='store_true',
                        help='Recompile native call admission and existing verified registry snapshots from matching reviewed contracts and source facts')
    parser.add_argument('--source-owners-only', action='store_true',
                        help='Keep other owners unchanged; recompile only the explicit source-facts owners')
    parser.add_argument('--refresh-structured-calls', action='store_true',
                        help='Admit selected pure ST functions through their own complete source contracts; never adds Native forms')
    parser.add_argument('--research-archive', type=Path,
                        help='Private inverse-data SQLite archive; restores previous research before compiling and separates it before runtime publication')
    parser.add_argument('--partition-runtime-research', action='store_true',
                        help='Perform the initial full partition of all instruction and language resources; requires --research-archive and forbids --source-owners-only')
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    batches, languages, empty_source_replacements = load_source_facts(
        args.source_facts, replace_source_prefixes=args.replace_source_prefix,
        return_owner_replacements=True)
    result = compile_semantics(args.input_manifest, args.output_dir, source_index=args.source_index,
        source_pdf_root=args.source_pdf_root,
        source_batches=batches, language_batches=languages, replace_source_prefixes=args.replace_source_prefix,
        refresh_reviewed_calls=args.refresh_reviewed_calls,
        refresh_structured_calls=args.refresh_structured_calls,
        empty_source_replacements=empty_source_replacements,
        source_owners_only=args.source_owners_only,
        research_archive=args.research_archive,
        version=args.version, progress=lambda counts: print(json.dumps(counts), flush=True),
        partition_runtime_research=args.partition_runtime_research)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
