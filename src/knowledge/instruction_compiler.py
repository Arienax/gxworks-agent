"""Compile complete indexed instruction material into registry-owned facts.

Documents are joined by exact manual/revision/section, never a page window.
Every official source section is inventoried, including unrecognized material.
The resulting JSON contains facts and source locations, not duplicate manuals.
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from knowledge.instruction_document import (
    _operand_evidence_bindings, _operand_table_units, document_units,
    extract_binary_result_charts,
    extract_native_signature,
    extract_model_badge_marks,
)
from knowledge.source_authority import instruction_source_authority
from plc.instruction_definition import InstructionFactGroup
from plc.instruction_semantics import arbitrate_operand_usage


METHOD = 'instruction-material-compiler-v1'


def instruction_heading_names(heading):
    """Literal instruction/function identities declared by a leaf heading."""
    leaf = str(heading or '').split(' > ')[-1]
    # Some native FNC headings omit spaces around '/'. Their explicit function
    # number distinguishes the declaration from ordinary titles such as I/O.
    native = re.match(r'^\s*\d+(?:\.\d+)*\.?\s+FNC\s*\d+\s*[–—-]\s*'
                      r'([A-Z][A-Z0-9_.$@+<>!=\-]*)\s*/', leaf)
    if native:
        return [native[1]]
    match = re.match(r'^\s*(?:\d+(?:\.\d+)*\.?\s+)?(?:FNC\s*\d+\s*[–—-]\s*)?([A-Z][A-Z0-9_.$@+<>!=\-]*)(\(_E\))?\s*/\s+', leaf)
    if match:
        return [match[1], match[1] + '_E'] if match[2] else [match[1]]
    match = re.search(r'[-(]\s*([A-Z][A-Z0-9_]*)(?:/(D\1))?\s+Instructions?\)?$', leaf)
    if match:
        return [name for name in match.groups() if name]
    # Basic IL instruction headings often list several literal forms without
    # a slash. Require a numbered heading and names separated by punctuation.
    match = re.match(r'^\s*\d+(?:\.\d+)+\s+([A-Z][A-Z0-9_.$@+<>!=\-]*(?:\s*[,/・]\s*[A-Z][A-Z0-9_.$@+<>!=\-]*)*)\s*$', leaf)
    return re.split(r'\s*[,/・]\s*', match[1]) if match else []


def _legacy_facts(form, model):
    """Project already-owned facts, keeping verification field-specific."""
    spec, coverage = form.spec, form.spec.contract_coverage()
    scope = {'models': [model], 'forms': [form.opcode]}
    facts = []
    values = [
        ('identity.form', 'form_identity', {'opcode': form.opcode, 'base_opcode': form.base_mnemonic,
                                         'double': form.double, 'pulse': form.pulse}),
        ('identity.applicability', 'cpu_applicability', {'declared_models': sorted(spec.cpu_support),
                                                       'available': spec.supports_cpu(model)}),
        ('parameters.arity', 'arity', {'minimum': spec.min_operands, 'maximum': spec.max_operands}),
        ('parameters.order', 'operand_order', {'symbols': list(spec.native_operand_order)}),
        ('parameters.roles', 'operand_roles', {'roles': [o.role.value for o in spec.operands]}),
        ('parameters.types', 'operand_types', {'types': [o.data_type for o in spec.operands]}),
        ('parameters.devices', 'device_classes', {'prefixes': [list(o.device_prefixes) for o in spec.operands]}),
        ('parameters.width', 'instruction_width', {'bits': spec.instruction_width}),
        ('execution.form', 'execution_form', {'form': spec.execution_form}),
        ('resources.completion', 'completion_ownership',
         {'device': spec.completion.device, 'placement': spec.completion.placement} if spec.completion else {}),
        ('resources.pulse_output', 'hardware_applicability',
         {'frequency_positions': [i + 1 for i in spec.pulse_output.frequency_operand_indexes],
          'output_position': spec.pulse_output.pulse_output_operand_index + 1,
          'direction_position': (spec.pulse_output.direction_output_operand_index + 1)
                                if spec.pulse_output.direction_output_operand_index is not None else None}
         if spec.pulse_output else {}),
        ('constraints.numeric_ranges', 'numeric_and_memory_boundaries',
         {'ranges': [dict(vars(item)) for item in spec.numeric_operand_boundaries]}),
        ('memory.disjoint_ranges', 'numeric_and_memory_boundaries',
         {'ranges': [dict(vars(item)) for item in spec.disjoint_bit_ranges]}),
    ]
    for dimension, legacy, value in values:
        if not value or all(v in (None, [], {}) for v in value.values()):
            continue
        status = coverage.get(legacy, 'unresolved')
        status = 'unknown' if status == 'unresolved' else status
        sources = [{**dict(source), 'target_model': model, 'opcode': form.opcode,
                    'legacy_field': legacy} for source in spec.contract_sources
                   if not source.get('verified_fields') or legacy in source['verified_fields']]
        if status == 'source_verified' and not sources:
            # A legacy label alone is never new source evidence.
            status = 'declared_unverified'
        if not sources:
            sources = [{'reference': 'existing_instruction_registry', 'opcode': form.opcode, 'target_model': model,
                        'legacy_field': legacy}]
        facts.append({'id': 'registry:' + dimension, 'dimension': dimension, 'value': value,
                      'status': status, 'sources': sources, 'scope': scope,
                      'verification': 'source_checked' if status == 'source_verified' else 'not_performed'})
    return facts


def _record(chunk):
    return {**chunk, 'id': str(chunk['id']), 'manual_text': chunk['text'],
            'source': chunk.get('manual_title', ''), 'indexed_operands': []}


def _signature_facts(signature, opcode, model):
    """Sourced structural candidates; extraction never inherits review status."""
    source = {'manual_id': signature['manual_id'], 'pdf_page': signature['page'],
              'manual_number': signature.get('manual_number'), 'revision': signature.get('revision'),
              'id': signature.get('source_id'),
              'section': signature['section'], 'offset_basis': 'page_artifacts.word_geometry_json',
              'table': signature['table_index'], 'bbox': signature['table_bbox'],
              'opcode': opcode, 'target_model': model}
    scope = {'models': [model], 'forms': [opcode]}
    metadata = signature['forms'][opcode]
    values = [('native:order', 'parameters.source_order', {'symbols': signature['symbols']}),
              ('native:identity', 'identity.literal_mnemonic',
               {'opcode': opcode, 'family_heading': signature['base'], 'bbox': metadata['bbox']})]
    if metadata['instruction_width']:
        values.append(('native:width', 'parameters.source_width', {'bits': metadata['instruction_width']}))
    if metadata['execution_form']:
        values.append(('native:execution', 'execution.source_form', {'form': metadata['execution_form']}))
    mark = next((mark for mark in signature.get('model_marks', []) if mark['model'] == model), None)
    if mark:
        values.append(('native:model_mark', 'identity.source_model_mark',
                       {'target_model': model, 'highlighted': mark['highlighted'], 'bbox': mark['bbox'],
                        'interpretation': 'source_highlight_only; CPU_support_not_promoted'}))
    return [{'id': identity, 'dimension': dimension, 'value': value, 'status': 'candidate_evidence',
             'sources': [{**source, **({'offset_basis': 'local_official_PDF.rects', 'bbox': value['bbox'], 'table': None}
                                      if dimension == 'identity.source_model_mark' else {})}],
             'scope': scope, 'verification': 'not_performed'}
            for identity, dimension, value in values]


def _operand_facts(records, symbols, opcode, model, *, definition_pages=None):
    bindings = []
    gaps = [{'position': i + 1, 'symbol': symbol, 'facet': 'purpose'} for i, symbol in enumerate(symbols)]
    for record in records:
        for start, end in _operand_table_units(record['text']):
            values = _operand_evidence_bindings(record, start, end, gaps, symbols)
            # Later operation-mode tables may redefine the same symbol under
            # conditions absent from this flat slot view. Preserve that source
            # as unprocessed material instead of claiming a global purpose.
            if definition_pages is not None:
                values = [value for value in values if
                          (record['manual_id'], value['source']['pdf_page']) in definition_pages]
            bindings.extend(values)
    groups = defaultdict(list)
    for binding in bindings:
        if binding.get('fact'):
            groups[(binding['position'], binding['facet'])].append(binding['fact'])
    facts, conflicts = [], []
    for (position, facet), candidates in sorted(groups.items()):
        resolved, collision = arbitrate_operand_usage(candidates)
        dimension = f'parameters.{position}.{facet}'
        identity = f'operand:{position}:{facet}'
        if collision:
            conflicts.append({'position': position, 'facet': facet, 'candidates': candidates})
            facts.append({'id': identity, 'dimension': dimension, 'status': 'conflict',
                          'value': {'position': position, 'symbol': symbols[position - 1], 'facet': facet,
                                    'reason': 'multiple_candidate_values', 'candidates': candidates},
                          'sources': [s for c in candidates for s in c.get('sources', [])],
                          'scope': {'models': [model], 'forms': [opcode]}})
        else:
            for index, fact in enumerate(resolved):
                sources = [{**s, 'target_model': model, 'opcode': opcode} for s in fact.get('sources', [])]
                facts.append({'id': identity + (f':{index}' if len(resolved) > 1 else ''), 'dimension': dimension,
                              'value': {'position': position, 'symbol': symbols[position - 1], 'facet': facet,
                                        'value': fact['value'], **({'conditions': fact['conditions']} if fact.get('conditions') else {})},
                              'status': fact['status'], 'sources': sources,
                              'scope': {'models': [model], 'forms': [opcode]}})
    # Independent row facts retain their own strict slot/facet arbitration.
    # The runtime quarantines the whole contradictory raw unit, while packing
    # these unambiguous facts without copying the conflicting row back in.
    return facts, conflicts


def _chart_facts(charts, opcode, model):
    signatures = {tuple((m['output'], m['comparison']) for m in chart['members']) for chart in charts}
    if len(signatures) != 1:
        conflict = [{'reason': 'different_complete_result_charts', 'chart_ids': [c['id'] for c in charts]}] if charts else []
        facts = [{'id': 'chart:results', 'dimension': 'effects.result_mapping', 'status': 'conflict',
                  'value': {'reason': 'different_complete_result_charts'}, 'sources': [c['source'] for c in charts],
                  'scope': {'models': [model], 'forms': [opcode]}}] if charts else []
        return facts, conflict
    chart = charts[0]
    chart = copy.deepcopy(chart)
    for member in chart['members']:
        member['target']['kind'] = member['target'].pop('unit', 'bit')
    scope = {'models': [model], 'forms': [opcode]}
    sources = [chart['source']]
    members = [m['output'] for m in chart['members']]
    facts = [
        {'id': 'chart:input_type', 'dimension': 'parameters.value_type', 'value': chart['value_type']},
        {'id': 'chart:enable', 'dimension': 'execution.enable', 'value': {'condition': 'command_input', 'active': 1}},
        {'id': 'chart:disabled', 'dimension': 'execution.disabled_retention',
         'value': {'action': chart['disabled'], 'outputs': [m['target'] for m in chart['members']]}, 'members': members},
        {'id': 'chart:results', 'dimension': 'effects.result_mapping', 'members': members,
         'depends_on': ['chart:input_type', 'chart:enable', 'chart:disabled', 'registry:parameters.order'],
         'value': {'behavior': 'conditional_results', 'outputs': chart['members'], 'text': chart['text'],
                   'source_unit_id': chart['source_unit_id'], 'representation': chart['representation']}},
    ]
    return [{**f, 'status': 'candidate_evidence', 'sources': sources, 'scope': scope,
             'verification': 'not_performed'} for f in facts], []


def _document_catalog(chunks, manuals):
    grouped = defaultdict(list)
    for chunk in chunks:
        if chunk['manual_id'] in manuals:
            grouped[(chunk['manual_id'], chunk['revision'], chunk['section'])].append(chunk)
    documents, by_form = [], defaultdict(list)
    for (manual, revision, section), rows in sorted(grouped.items()):
        names = instruction_heading_names(section)
        identity = f'{manual}:{revision}:{section}'
        record_rows = sorted(rows, key=lambda r: (r['pdf_page'], r.get('block_index', 0), r['id']))
        units = []
        for raw in record_rows:
            for unit in document_units(_record(raw)):
                units.append({'id': unit.id, 'chunk_id': str(raw['id']), 'representation': unit.representation,
                              'pdf_page': unit.page, 'source_span': {'start': unit.start, 'end': unit.end},
                              'cell_count': len(unit.cells), 'processing_status': 'inventoried'})
                table = re.match(r'\[TABLE page=(\d+) index=(\d+) bbox=(\[[^\n]+?\])\]', raw['text'][unit.start:unit.end])
                units[-1]['geometry_reference'] = {'table': int(table[2]), 'bbox': json.loads(table[3])} if table else {
                    'table': None, 'page_artifact': {'manual_id': manual, 'pdf_page': unit.page},
                }
        document = {'id': identity, 'manual_id': manual, 'manual_number': manuals[manual]['manual_number'],
                    'revision': revision, 'section': section, 'forms': names,
                    'models': list(manuals[manual].get('plc_models', [])),
                    'source_ids': [str(r['id']) for r in record_rows],
                    'pdf_pages': sorted({p for r in record_rows for p in range(r['pdf_page'], r['pdf_page_end'] + 1)}),
                    'units': units, 'explicit_references': [], 'unresolved_references': [],
                    'section_association': 'literal_instruction_heading' if names else 'non_instruction_or_unclassified'}
        for name in names:
            by_form[name].append((document, record_rows))
        documents.append(document)
    # Child sections belong to the nearest explicit instruction heading in
    # this same manual/revision. Numeric ancestry, never page adjacency.
    numbered = {}
    for document in documents:
        match = re.match(r'^(\d+(?:\.\d+)+)\s', document['section'])
        if match and document['forms']:
            numbered[(document['manual_id'], document['revision'], match[1])] = document
    rows_by_document = {(m + ':' + r + ':' + s): rows for (m, r, s), rows in grouped.items()}
    for document in documents:
        match = re.match(r'^(\d+(?:\.\d+)+)\s', document['section'])
        if not match or document['forms']:
            continue
        pieces = match[1].split('.')
        for length in range(len(pieces) - 1, 1, -1):
            parent = numbered.get((document['manual_id'], document['revision'], '.'.join(pieces[:length])))
            if parent:
                document['forms'] = list(parent['forms'])
                document['definition_parent'] = parent['id']
                document['section_association'] = 'explicit_section_ancestry'
                for name in document['forms']:
                    by_form[name].append((document, rows_by_document[document['id']]))
                break
    # Follow only explicit same-manual section numbers. One hop is sufficient
    # for the initial compilation; further references stay visible unresolved.
    by_number = defaultdict(list)
    for document in documents:
        match = re.match(r'^(\d+(?:\.\d+)+)\s', document['section'])
        if match:
            by_number[(document['manual_id'], document['revision'], match[1])].append(document)
    by_id = {d['id']: d for d in documents}
    raw_by_id = {str(c['id']): c for c in chunks}
    for document in documents:
        text = '\n'.join(raw_by_id[s]['text'] for s in document['source_ids'])
        references = sorted(set(re.findall(r'(?i)(?:refer to|see)\s+(?:sub)?section\s+(\d+(?:\.\d+)+)', text)))
        for number in references[:16]:
            targets = by_number.get((document['manual_id'], document['revision'], number), [])
            if len(targets) == 1 and targets[0]['id'] != document['id']:
                document['explicit_references'].append({'section_number': number, 'document_id': targets[0]['id']})
            else:
                document['unresolved_references'].append({'section_number': number, 'reason': 'missing_or_ambiguous_exact_section'})
        if len(references) > 16:
            document['unresolved_references'].append({'reason': 'reference_limit', 'remaining_count': len(references) - 16})
    return documents, by_form, by_id


def compile_instruction_definitions(index_path, source_catalog, registry, *, source_dir=None, authored_facts=None):
    """Process the Registry/manual union without changing the evidence database."""
    catalog = json.loads(Path(source_catalog).read_text(encoding='utf-8')) if not isinstance(source_catalog, dict) else source_catalog
    manuals = {m['id']: copy.deepcopy(m) for m in catalog['manuals']}
    index_path = Path(index_path)
    with sqlite3.connect(index_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        chunks = [dict(row) for row in db.execute('SELECT * FROM chunks ORDER BY id') if row['manual_id'] in manuals]
        instruction_rows = [dict(row) for row in db.execute('SELECT * FROM instructions') if row['manual_id'] in manuals]
        table_map = defaultdict(list)
        signatures = []
        for row in db.execute('SELECT * FROM tables ORDER BY manual_id,pdf_page,table_index'):
            table_map[(row['manual_id'], row['pdf_page'])].append(dict(row))
        for row in db.execute('SELECT * FROM page_artifacts ORDER BY manual_id,pdf_page'):
            if row['manual_id'] not in manuals:
                continue
            signature = extract_native_signature(dict(row), table_map[(row['manual_id'], row['pdf_page'])])
            if signature:
                signatures.append(signature)
    documents, by_form, document_ids = _document_catalog(chunks, manuals)
    layout_receipts = []
    if source_dir:
        for manual in manuals.values():
            layout = manual.get('instruction_layout')
            local = Path(source_dir) / Path(manual['file']).name
            if not layout or not local.is_file():
                continue
            try:
                import pdfplumber
            except ImportError:
                layout_receipts.append({'manual_id': manual['id'], 'status': 'PDF_geometry_runtime_unavailable'})
                continue
            count = 0
            with pdfplumber.open(local) as pdf:
                for signature in signatures:
                    if signature['manual_id'] != manual['id']:
                        continue
                    page = pdf.pages[signature['page'] - 1]
                    marks = extract_model_badge_marks(page.rects, layout)
                    if marks:
                        signature['model_marks'] = marks
                        count += 1
                    page.close()
            layout_receipts.append({'manual_id': manual['id'], 'status': 'candidate_marks_only', 'pages': count,
                                    'support_promotion': False})
    for signature in signatures:
        manual = manuals[signature['manual_id']]
        signature.update(manual_number=manual['manual_number'], revision=manual['revision'])
        witnesses = [c for c in chunks if c['manual_id'] == signature['manual_id'] and
                     c['pdf_page'] <= signature['page'] <= c['pdf_page_end'] and
                     instruction_heading_names(c['section']) == [signature['base']]]
        if witnesses:
            signature['source_id'] = str(witnesses[0]['id'])
    # The instruction icon may name a family whose only callable form has a D
    # prefix. Literal mnemonic columns associate every actual D/P variant with
    # its chapter, including forms absent from the old Registry or chunk index.
    for signature in signatures:
        owners = [(d, rows) for d, rows in by_form.get(signature['base'], [])
                  if d['manual_id'] == signature['manual_id'] and signature['page'] in d['pdf_pages']]
        for opcode in signature['forms']:
            for pair in owners:
                if pair not in by_form[opcode]:
                    by_form[opcode].append(pair)
    # Manuals with topic headings (e.g. a positioning chapter's "Instruction
    # Format") are still reached through explicit indexed opcode ownership.
    # This is chapter membership only; it does not verify the indexed signature.
    for document in documents:
        if document['forms'] or not re.search(r'\bInstruction\s+Format\s*$', document['section'], re.I):
            continue
        rows = [c for c in chunks if c['id'] in {int(i) for i in document['source_ids']}]
        names = sorted({c['instruction_opcode'].upper() for c in rows
                        if c['chunk_type'] == 'instruction' and c['instruction_opcode']})
        if len(names) == 1:
            document.update(forms=names, section_association='explicit_indexed_instruction_identity')
            for name in names:
                by_form[name].append((document, rows))
    # Explicit variants from indexed definition rows are considered only when
    # that row's base has a literal definition heading in the same manual.
    for row in instruction_rows:
        base = row['opcode'].strip().upper()
        owners = [(d, rs) for d, rs in by_form.get(base, []) if d['manual_id'] == row['manual_id']]
        for variant in json.loads(row['variants_json'] or '[]'):
            if isinstance(variant, str) and variant.upper() != base:
                for pair in owners:
                    if pair not in by_form[variant.upper()]:
                        by_form[variant.upper()].append(pair)
    models = set(m for manual in manuals.values() for m in manual.get('plc_models', []))
    models.update(cpu for spec in registry._specs.values() for cpu in spec.cpu_support)
    models.update(key[2] for key in registry._cpu_contracts)
    known = set(registry.known_mnemonics())
    forms = sorted(known | set(by_form))
    family_owners = defaultdict(set)
    for signature in signatures:
        for name in signature['forms']:
            family_owners[name].add(signature['base'])
    form_families = {name: next(iter(owners)) for name, owners in family_owners.items() if len(owners) == 1}
    raw_by_id = {str(c['id']): c for c in chunks}
    authored = defaultdict(list)
    signatures_by_form = defaultdict(list)
    for signature in signatures:
        for opcode in signature['forms']:
            signatures_by_form[opcode].append(signature)
    if authored_facts:
        from plc.instruction_definition_storage import expand_definitions
        authored_facts = expand_definitions(authored_facts)
    for entry in (authored_facts or {}).get('entries', []):
        authored[(entry['opcode'], entry['target_model'])].extend(entry.get('facts', []))
    entries, mapped_spans = [], defaultdict(list)
    for model in sorted(models):
        for opcode in forms:
            form = registry.resolve_form(opcode, cpu=model)
            ownership = by_form.get(opcode, [])
            # Registry D/P forms can use an explicitly shared base chapter;
            # evidence remains literal-form scoped, and widths do not inherit.
            if not ownership and form is not None:
                ownership = by_form.get(form.base_mnemonic, [])
            ownership = [(d, rs) for d, rs in ownership if model in d['models']]
            if form is None and not ownership:
                continue
            admitted = bool(form is not None and form.spec.supports_cpu(model))
            facts = _legacy_facts(form, model) if admitted else []
            authority = instruction_source_authority(opcode, model)
            preferred = authority.get('manual_id') if authority else None
            if not preferred and ownership:
                preferred = max(ownership, key=lambda pair: manuals[pair[0]['manual_id']].get('priority', 0))[0]['manual_id']
            active = [(d, rs) for d, rs in ownership if d['manual_id'] == preferred]
            records = [_record(r) for d, rs in active for r in rs]
            for record in records:
                record['manual_operand_rows'] = [operand for row in instruction_rows if
                    row['manual_id'] == record['manual_id'] and row['opcode'].upper() in {opcode, form.base_mnemonic if form else opcode}
                    for operand in json.loads(row.get('operands_json') or '[]')]
            literal_signatures = [s for s in signatures_by_form[opcode] if s['manual_id'] == preferred]
            if not literal_signatures:
                literal_signatures = [s for s in signatures_by_form[opcode]
                                      if model in manuals[s['manual_id']].get('plc_models', [])]
            direct_signatures = [s for s in literal_signatures if s['base'] in
                                 {opcode, form.base_mnemonic if form else opcode}]
            if direct_signatures:
                literal_signatures = direct_signatures
            signature_shapes = {tuple(s['symbols']) for s in literal_signatures}
            order = list(form.spec.native_operand_order) if admitted else []
            if not order and len(signature_shapes) == 1:
                order = list(next(iter(signature_shapes)))
            if len(literal_signatures) == 1:
                facts.extend(_signature_facts(literal_signatures[0], opcode, model))
            definition_pages = {(s['manual_id'], s['page']) for s in literal_signatures
                                if s['manual_id'] == preferred} or None
            operand_facts, operand_conflicts = _operand_facts(records, order, opcode, model,
                definition_pages=definition_pages) if order else ([], [])
            facts.extend(operand_facts)
            charts = [chart for record in records for chart in extract_binary_result_charts(
                record, symbols=order, opcode=opcode, target_model=model, width=form.spec.instruction_width)] if admitted else []
            chart_facts, chart_conflicts = _chart_facts(charts, opcode, model)
            facts.extend(chart_facts)
            facts.extend(copy.deepcopy(authored[(opcode, model)]))
            for fact in facts:
                parsed = InstructionFactGroup.from_mapping(fact)
                if parsed.dimension.startswith('effects.') and parsed.value.get('behavior'):
                    from plc.instruction_effects import validate_behavior
                    validate_behavior(parsed.value)
                if parsed.scope.get('models') != [model] or parsed.scope.get('forms') != [opcode]:
                    raise ValueError('Authored fact exceeds exact model/form ownership')
                for source in fact.get('sources', []):
                    if source.get('id') in raw_by_id:
                        for span in source.get('value_spans', []):
                            if not 0 <= span['start'] < span['end'] <= len(raw_by_id[source['id']]['text']):
                                raise ValueError('Fact witness is outside its original source')
                            mapped_spans[source['id']].append((span['start'], span['end'], fact['id']))
                        if not source.get('value_spans') and source.get('row_span'):
                            span = source['row_span']
                            mapped_spans[source['id']].append((span['start'], span['end'], fact['id']))
            materials = [{'document_id': d['id'], 'manual_id': d['manual_id'], 'revision': d['revision'],
                          'source_ids': d['source_ids'], 'pdf_pages': d['pdf_pages'],
                          'primary_semantic_source': d['manual_id'] == preferred,
                          'explicit_reference_ids': [r['document_id'] for r in d['explicit_references']]} for d, _ in ownership]
            unexplained = [{'document_id': d['id'], 'reason': 'remaining_source_content', 'unit_count': len(d['units'])}
                           for d, _ in ownership]
            gaps = []
            if not admitted:
                gaps.append('manual_only_not_admitted' if form is None else 'outside_declared_cpu_scope')
            if not ownership:
                gaps.append('no_literal_official_definition_section')
            if not admitted or form.spec.contract_coverage().get('operand_order') != 'source_verified':
                gaps.append('native_parameter_order_not_established')
            if not any(f['dimension'].startswith('effects.') for f in facts):
                gaps.append('effects_not_formalized')
            entries.append({'vendor': 'mitsubishi', 'opcode': opcode, 'target_model': model,
                            'registry_member': opcode in known, 'call_available': admitted,
                            'support_status': 'existing_declared_scope' if admitted else 'definition_only',
                            'facts': facts, 'source_materials': materials, 'uninterpreted_content': unexplained,
                            'conflicts': [*operand_conflicts, *chart_conflicts], 'gaps': gaps,
                            'semantic_completeness': 'not_established'})
    for document in documents:
        for unit in document['units']:
            lo, hi = unit['source_span']['start'], unit['source_span']['end']
            matches = [(start, end, fact) for start, end, fact in mapped_spans[unit['chunk_id']] if lo <= start < end <= hi]
            residual, cursor = [], lo
            for start, end, _ in sorted(matches):
                if cursor < start:
                    residual.append({'start': cursor, 'end': start})
                cursor = max(cursor, end)
            if cursor < hi:
                residual.append({'start': cursor, 'end': hi})
            unit.update(mapped_fact_ids=sorted({fact for _, _, fact in matches}),
                        uninterpreted_spans=residual,
                        interpretation_status='partially_interpreted' if matches else 'uninterpreted')
    catalog_state = []
    for manual in manuals.values():
        local = Path(source_dir) / Path(manual['file']).name if source_dir else None
        catalog_state.append({'id': manual['id'], 'manual_number': manual['manual_number'], 'revision': manual['revision'],
                              'models': manual.get('plc_models', []), 'source_file': Path(manual['file']).name,
                              'local_file_available': bool(local and local.is_file()),
                              'indexed_sections': sum(d['manual_id'] == manual['id'] for d in documents)})
    payload = {'schema_version': 1, 'method': METHOD, 'models': sorted(models),
               'form_families': form_families,
               'registry_forms': len(known), 'manual_heading_forms': len(by_form), 'union_forms': len(forms),
               'source_catalog': catalog_state, 'source_layout_receipts': layout_receipts, 'documents': documents, 'entries': entries,
               'reference_policy': {'max_explicit_references_per_section': 16, 'max_depth': 1, 'adjacent_page_inference': False},
               'source_verification': 'preserve_existing_and_per_fact_only', 'database_mutated': False}
    report = {'schema_version': 1, 'method': METHOD, 'registry_forms': len(known), 'manual_heading_forms': len(by_form),
              'union_forms': len(forms), 'target_models': sorted(models), 'entries': len(entries),
              'source_layout_receipts': layout_receipts,
              'documents': len(documents), 'units': sum(len(d['units']) for d in documents),
              'partially_interpreted_units': sum(u['interpretation_status'] == 'partially_interpreted' for d in documents for u in d['units']),
              'fact_statuses': dict(sorted(Counter(f['status'] for e in entries for f in e['facts']).items())),
              'effect_forms': [{'opcode': e['opcode'], 'target_model': e['target_model']} for e in entries
                               if any(f['dimension'].startswith('effects.') for f in e['facts'])],
              'gap_counts': dict(sorted(Counter(gap for e in entries for gap in e['gaps']).items())),
              'source_unattributed_sections': sum(not d['forms'] for d in documents),
              'database_mutated': False, 'support_completion': 'per_form_not_established'}
    return payload, report
