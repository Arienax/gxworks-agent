from copy import deepcopy
import base64
from dataclasses import replace
import json
from itertools import permutations
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

import pytest

from src.gxw.models import GXWFormatError, NodeKind
from src.gxw.object_model import (build_object_program, catalog_description, default_baseline,
    export_object_model, generate_object_project, read_project)
from src.gxw.ladder_lowering import ladder_to_object_model
from src.gxw.render import render_structured_svg
from src.gxw.fb_connectivity import fb_connectivity_model
from tests.test_gxw_declarations import baseline


def source_binding_witness(index=0):
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import parse_declarations
    from src.gxw.structured_pou import parse_structured_pou
    with zipfile.ZipFile(Path(__file__).parents[1] / 'research/evidence/gxw-fbd-source-binding-20261001.zip') as archive:
        case = json.loads(archive.read('witnesses.json'))['interfaces'][index]
    declarations = {name: parse_declarations(base64.b64decode(value), logical_name=name)
                    for name, value in case['declarations'].items()}
    libraries = {}
    for definition in case['definitions']:
        sections = definition['sections']
        text = (('(*$SECTION:' + ','.join(sections) + '*)\n').encode() if sections else b'')
        text += base64.b64decode(definition['declaration_prefix_base64'])
        text += ('END_' + definition['kind'] + '\n').encode()
        name = definition['source_stream']
        libraries[name] = libraries.get(name, b'') + text
    sources = ProjectCallableSources(case['cpu'], case['logical'], declarations, libraries)
    program = parse_structured_pou(base64.b64decode(case['program_base64']), logical_name=case['logical'],
                                   preserve_unsupported_records=True)
    return program, declarations, sources, case


def source_binding_baseline():
    """Owned graph/declarations plus a minimal declaration-only source library."""
    from src.gxw.container_writer import validate_cfb_streams
    from src.gxw.declarations import serialize_declarations
    from src.gxw.project_metadata import logical_mapping, synchronize_history
    from src.gxw.project_writer import replace_project_stream
    from src.gxw.structured_pou_writer import serialize_structured_pou
    program, declarations, _, _ = source_binding_witness()
    raw = default_baseline()
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    inner = validate_cfb_streams(outer['_hdb'])
    fixture = json.loads((Path(__file__).parent / 'fixtures/gxw_fbd_source_library.json').read_text())
    changes = {program.logical_name: serialize_structured_pou(program),
               **{name: serialize_declarations(doc) for name, doc in declarations.items()},
               'IECFunction.lif': inner[mapping['IECFunction.lif']][:20] + base64.b64decode(fixture['archive_base64'])}
    hdb = outer['_hdb']
    for name, data in changes.items():
        hdb, _ = replace_project_stream(hdb, mapping[name], data)
    history, _, _ = synchronize_history(outer['history.xml'],
        {name: (mapping[name], inner[mapping[name]], data) for name, data in changes.items()})
    raw, _ = replace_project_stream(raw, '_hdb', hdb)
    raw, _ = replace_project_stream(raw, 'history.xml', history)
    return raw


def user_library_binding_baseline(*, damage=None):
    """Generated metadata boundary control, without native acceptance claims."""
    import struct
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.declarations import _string, edit_declarations, parse_declarations, serialize_declarations
    from src.gxw.project_metadata import current_rows, logical_mapping
    from src.gxw.source_header import source_payload_offset

    raw = default_baseline()
    program, documents, _ = read_project(raw)
    base = documents['1.Labels.lh']
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    inner = validate_cfb_streams(outer['_hdb'])
    slots = [name for name in mapping if name.endswith('.lif')] + ['Global1.gh']
    rows, encoding = current_rows(outer['projectdatalist.xml'], 'DSPROJECTDATA', 'D_Projectdata')
    changes, replacements = {}, []
    for index, library in enumerate(('Library_A', 'Library_B')):
        name = 'FIRST' if index == 0 or damage == 'duplicate_name' else 'SECOND'
        logical = name + '.Labels\\' + library + '.lnl'
        embedded = 'OTHER' if index == 1 and damage == 'embedded_owner' else name
        header = (base.header[:source_payload_offset(base.raw)] + _string(embedded)
            + struct.pack('<II', 0x1000003, 1) + _string('') + _string('BOOL') + struct.pack('<I', 0))
        document = parse_declarations(header + base.trailer, logical_name=logical)
        document = edit_declarations(document, upserts=[
            {'name': 'VALUE', 'data_type': 'DWORD', 'class_name': 'VAR_INPUT'},
            {'name': 'STATE', 'data_type': 'WORD', 'class_name': 'VAR_IN_OUT'}])
        declaration = serialize_declarations(document)
        if index == 1 and damage == 'unrecognized_kind':
            kind_offset = source_payload_offset(declaration) + len(_string(embedded))
            declaration = declaration[:kind_offset] + struct.pack('<I', 0x1000001) + declaration[kind_offset+4:]
        # Source names deliberately differ: metadata grouping binds them.
        source = 'DifferentBody.Program\\' + library + ('.lbo' if index == 1 and damage == 'missing_source' else '.lnb')
        for original, target, value, role in (
                (slots[2*index], logical, declaration, '1'),
                (slots[2*index+1], source, program.raw, '2')):
            changes[mapping[original]] = value
            row, = [row for row in rows if row.fields()['szName'].text.strip() == original]
            fields = row.fields()
            updates = {'szName': target, 'ucProductType': '1', 'ucFolderType': '92',
                       'uiFolderNo': '1', 'ucReserve': '0', 'ucFileType': role}
            for key, text in updates.items():
                field = fields[key]
                replacements.append((field.content_start, field.content_end, text.encode(encoding)))
    xml = outer['projectdatalist.xml']
    for start, end, replacement in sorted(replacements, reverse=True):
        xml = xml[:start] + replacement + xml[end:]
    hdb = outer['_hdb']
    for physical, value in changes.items():
        hdb, _ = replace_project_stream(hdb, physical, value)
    raw, _ = replace_project_stream(raw, '_hdb', hdb)
    raw, _ = replace_project_stream(raw, 'projectdatalist.xml', xml)
    return raw


def test_user_library_interfaces_bind_namespaces_and_rebuild_pending_formals():
    from src.gxw.object_model import read_project_context
    from src.gxw.declarations import edit_declarations
    from src.gxw.callable_sources import ProjectCallableSources
    raw = user_library_binding_baseline()
    context = read_project_context(raw)
    labels = 'FIRST.Labels\\Library_A.lnl'
    other = 'SECOND.Labels\\Library_B.lnl'
    for name, stream in (('FIRST', labels), ('SECOND', other)):
        formals = context.sources.fixed_interface(name, 'FUNCTION')
        assert formals['source_stream'] == stream
        assert [(row['name'], row['class_code'], row['declared_type']) for row in formals['inputs']] == [
            ('VALUE', 3, 'DWORD'), ('STATE', 5, 'WORD')]
        assert [(row['name'], row['class_code'], row['declared_type']) for row in formals['outputs']] == [
            (name, 4, 'BOOL'), ('STATE', 5, 'WORD')]
    pending = context.draft_sources({labels: {'renames': {'VALUE': 'VALUE_LONG'}}})
    assert [row['name'] for row in pending.fixed_interface('FIRST', 'FUNCTION')['inputs']] == ['VALUE_LONG', 'STATE']
    assert [row['name'] for row in context.sources.fixed_interface('FIRST', 'FUNCTION')['inputs']] == ['VALUE', 'STATE']
    assert [row['name'] for row in pending.fixed_interface('SECOND', 'FUNCTION')['inputs']] == ['VALUE', 'STATE']
    changed = edit_declarations(context.declarations[labels], remove=['STATE'])
    rebuilt = ProjectCallableSources.from_project(raw, context.program.logical_name,
        {**context.declarations, labels: changed})
    assert [row['name'] for row in rebuilt.fixed_interface('FIRST', 'FUNCTION')['outputs']] == ['FIRST']


@pytest.mark.parametrize('damage', ['duplicate_name', 'missing_source', 'embedded_owner', 'unrecognized_kind'])
def test_user_library_ambiguity_and_unobserved_kinds_do_not_supply_a_function_interface(damage):
    from src.gxw.object_model import read_project_context
    context = read_project_context(user_library_binding_baseline(damage=damage))
    name = 'FIRST' if damage == 'duplicate_name' else 'SECOND'
    with pytest.raises(GXWFormatError, match='missing or ambiguous'):
        context.sources.fixed_interface(name, 'FUNCTION')


@pytest.mark.parametrize('change', ['program', 'declaration'])
def test_native_save_metadata_is_preserved_for_noop_and_requires_vendor_save_for_edits(change):
    from src.gxw.container_writer import replace_project_stream
    from src.gxw.declarations import edit_declarations
    from src.gxw.project_writer import build_gxw_project
    # Opaque boundary control; this does not claim a valid native digest table.
    raw, _ = replace_project_stream(default_baseline(), 'Project.gd2', b'0' * 64)
    source, documents, _ = read_project(raw)
    assert build_gxw_project(raw, source).data == raw
    if change == 'program':
        first, *rest = source.nodes
        kwargs = {'programs': replace(source, nodes=(replace(first, symbol=first.symbol + '_EDIT'), *rest))}
    else:
        name = '1.Labels.lh'
        kwargs = {'declarations': {name: edit_declarations(documents[name], upserts=[
            {'name': 'work', 'data_type': 'WORD'}])}}
    with pytest.raises(GXWFormatError, match='native workspace save'):
        build_gxw_project(raw, **kwargs)


def test_prepared_native_edit_reuses_graph_declaration_binding_without_container_write():
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import native_source_plan, workspace_body
    from src.gxw.project_writer import write_prepared_project
    from src.gxw.container_writer import replace_project_stream
    # Opaque envelope control; not an assertion of native acceptance.
    raw, _ = replace_project_stream(generate_object_project(two_timers()).data, 'Project.gd2', b'0' * 64)
    context = read_project_context(raw)
    model = context.object_model()
    model['nodes'][0]['symbol'] = '中文_INSTANCE_LONG'
    model['declaration_edits'] = {'1.Labels.lh': {'renames': {'TIMER_A': '中文_INSTANCE_LONG'}}}
    prepared = prepare_object_project(model, baseline=raw)
    plan = native_source_plan(prepared)
    assert prepared.requires_native_save
    assert plan['cpu'] == 'FX3U/FX3UC'
    assert plan['updates'] == [{'before': {'name': 'TIMER_A', 'data_type': 'TON', 'class_code': 1},
                               'after': {'name': '中文_INSTANCE_LONG', 'data_type': 'TON', 'class_code': 1},
                               'initial_before': '', 'initial_after': ''}]
    before, after = prepared.replacements['1.Program.pou'][1:]
    assert base64.b64decode(plan['before_body']) == workspace_body(before)
    assert '中文_INSTANCE_LONG'.encode('utf-16le') in base64.b64decode(plan['after_body'])
    with pytest.raises(GXWFormatError, match='native workspace save'):
        write_prepared_project(prepared)
    model['nodes'][0]['symbol'] = 'TIMER_A'
    with pytest.raises(GXWFormatError, match='renamed label still has a graph reference'):
        prepare_object_project(model, baseline=raw)


@pytest.mark.parametrize('field,value', [('device', 'M100'), ('initial_value', 'TRUE'), ('comment', 'changed')])
def test_native_source_plan_rejects_declaration_fields_it_cannot_execute(field, value):
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import native_source_plan
    raw = generate_object_project(two_timers()).data
    model = read_project_context(raw).object_model()
    model['declaration_edits'] = {'1.Labels.lh': {'upserts': [{'name': 'TIMER_A', field: value}]}}
    with pytest.raises(GXWFormatError, match='devices, initial values or comments'):
        native_source_plan(prepare_object_project(model, baseline=raw))


def test_native_save_cannot_turn_stale_source_or_changed_opaque_data_into_success():
    from src.gxw.container_writer import replace_project_stream, validate_cfb_streams
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import verify_native_save
    raw = generate_object_project(two_timers()).data
    model = read_project_context(raw).object_model()
    model['nodes'][0]['symbol'] = 'RENAMED'
    model['declaration_edits'] = {'1.Labels.lh': {'renames': {'TIMER_A': 'RENAMED'}}}
    prepared = prepare_object_project(model, baseline=raw)
    with pytest.raises(GXWFormatError, match='saved native source differs'):
        verify_native_save(prepared, raw, {'status': 'saved'})
    saved = generate_object_project(model, baseline=raw).data
    assert verify_native_save(prepared, saved, {}).report['validation']['gxworks_compile'] == 'not_run'
    outer = validate_cfb_streams(saved)
    declarations = read_project_context(saved).declarations['1.Labels.lh']
    damaged = replace(declarations, rows=(replace(declarations.rows[0], unknown_u32=9), *declarations.rows[1:]))
    from src.gxw.declarations import serialize_declarations
    from src.gxw.project_metadata import logical_mapping
    physical = logical_mapping(outer['projectdatalist.xml'])['1.Labels.lh']
    hdb, _ = replace_project_stream(outer['_hdb'], physical, serialize_declarations(damaged))
    corrupted, _ = replace_project_stream(saved, '_hdb', hdb)
    with pytest.raises(GXWFormatError, match='saved native declaration fields differ'):
        verify_native_save(prepared, corrupted, {})


def test_native_rename_plan_property_preserves_independent_formals_and_source_bytes():
    hypothesis = pytest.importorskip('hypothesis')
    from hypothesis import strategies as st
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import native_source_plan
    from src.gxw.structured_pou_writer import serialize_structured_pou
    raw = source_binding_baseline()
    context = read_project_context(raw)
    original = context.object_model()
    fb = next(n for n in original['nodes'] if n['template'].startswith('function_block:'))
    # Frozen native formals are independent of the edited model/plan.
    _, _, _, witness = source_binding_witness()
    oracle = next(n['interface'] for n in witness['final_source_view']['nodes'] if 'interface' in n)

    @hypothesis.settings(max_examples=80, deadline=None, derandomize=True)
    @hypothesis.given(st.text(alphabet='abcXYZ中文实例_', min_size=1, max_size=32))
    def check(suffix):
        renamed = 'PROP_' + suffix
        model = deepcopy(original)
        next(n for n in model['nodes'] if n['id'] == fb['id'])['symbol'] = renamed
        local = context.program.logical_name.removesuffix('.Program.pou') + '.Labels.lh'
        model['declaration_edits'] = {local: {'renames': {fb['symbol']: renamed}}}
        prepared = prepare_object_project(model, baseline=raw)
        plan = native_source_plan(prepared)
        assert plan['updates'][0]['after']['name'] == renamed
        rebuilt = prepared.programs[context.program.logical_name]
        node = next(n for n in rebuilt.nodes if n.symbol == renamed)
        sources = context.sources.with_declarations({**context.declarations, **prepared.declarations})
        ports = sources.callable(node)['ports']
        assert [(p['formal_name'], p['class_code'], p['data_type'], p['side']) for p in ports] == [
            (p['name'], p['class_code'], p['declared_type'], p['side']) for p in oracle['ports']]
        assert [r.raw for r in rebuilt.unknown_records] == [r.raw for r in context.program.unknown_records]
        assert base64.b64decode(plan['after_body']) in serialize_structured_pou(rebuilt)
        assert context.object_model() == original

    check()


def test_native_row_lineage_does_not_use_shared_saved_record_id():
    from src.gxw.object_model import prepare_object_project, read_project_context
    from src.gxw.native_write import native_source_plan
    from src.gxw.project_writer import build_gxw_project
    raw = generate_object_project(two_timers()).data
    context = read_project_context(raw)
    table = '1.Labels.lh'
    from src.gxw.declarations import edit_declarations
    document = edit_declarations(context.declarations[table], upserts=[
        {'name': 'BOOL_A', 'data_type': 'BOOL'}, {'name': 'BOOL_B', 'data_type': 'BOOL'}])
    document = replace(document, rows=tuple(replace(r, record_id=0) if r.name.startswith('BOOL_') else r
                                          for r in document.rows))
    raw = build_gxw_project(raw, declarations={table: document}).data
    context = read_project_context(raw)
    model = context.object_model()
    model['declaration_edits'] = {table: {'renames': {'BOOL_A': 'BOOL_RENAMED'}, 'remove': ['BOOL_B']}}
    prepared = prepare_object_project(model, baseline=raw)
    plan = native_source_plan(prepared)
    assert [r['before']['name'] for r in plan['updates']] == ['BOOL_A']
    assert [r['name'] for r in plan['remove']] == ['BOOL_B']
    assert [r.name for r in prepared.declarations[table].rows] == ['TIMER_A', 'TIMER_B', 'BOOL_RENAMED']


def source_binding_shadow_baseline(*, hiding=True):
    from src.gxw.container_writer import validate_cfb_streams, replace_project_stream
    from src.gxw.declarations import edit_declarations
    from src.gxw.project_metadata import logical_mapping, synchronize_history
    from src.gxw.project_writer import build_gxw_project
    raw = source_binding_baseline()
    _, declarations, _ = read_project(raw)
    global_doc = edit_declarations(declarations['Global1.gh'], upserts=[
        {'name': 'TIMER_A', 'data_type': 'BOOL', 'class_name': 'VAR_GLOBAL',
         'device': 'Y2', 'iec_address': '%QX2'}])
    raw = build_gxw_project(raw, declarations={'Global1.gh': global_doc}).data
    witness = json.loads((Path(__file__).parent / 'fixtures/gxw_fbd_source_library.json').read_text())['label_hiding_native']
    control = next(r for r in witness['controls'] if r['global_variable_hiding'] is hiding)
    outer = validate_cfb_streams(raw)
    mapping = logical_mapping(outer['projectdatalist.xml'])
    inner = validate_cfb_streams(outer['_hdb'])
    name = next(name for name in mapping if name.endswith('.prj'))
    value = base64.b64decode(control['metadata_base64'])
    hdb, _ = replace_project_stream(outer['_hdb'], mapping[name], value)
    history, _, _ = synchronize_history(outer['history.xml'], {name: (mapping[name], inner[mapping[name]], value)})
    raw, _ = replace_project_stream(raw, '_hdb', hdb)
    raw, _ = replace_project_stream(raw, 'history.xml', history)
    return raw


@pytest.mark.parametrize('hiding', [False, True])
def test_saved_native_hiding_option_controls_fb_source_binding_without_changing_declarations(hiding):
    from src.gxw.object_model import read_project_context
    raw = source_binding_shadow_baseline(hiding=hiding)
    context = read_project_context(raw)
    model = context.object_model()
    fb = next(n for n in model['nodes'] if n['symbol'] == 'TIMER_A')
    assert context.sources.global_variable_hiding is hiding
    assert [(r.name, r.data_type) for r in context.declarations['Global1.gh'].rows] == [('TIMER_A', 'BOOL')]
    if hiding:
        assert [p['name'] for p in fb['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
        assert context.sources.label('timer_a')[0] == '1.Labels.lh'
        assert generate_object_project(model, baseline=raw).data == raw
    else:
        assert all('formal_name' not in p for p in fb['ports'])
        with pytest.raises(GXWFormatError, match='ambiguous'):
            context.sources.label('TIMER_A')


@pytest.mark.parametrize('target', ['instance', 'local', 'hidden-global'])
def test_shadowed_label_rename_keeps_the_graph_and_the_other_scope_on_their_original_declaration(target):
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import read_project_context
    raw = source_binding_shadow_baseline()
    context = read_project_context(raw)
    model = context.object_model()
    fb = next(n for n in model['nodes'] if n['symbol'] == 'TIMER_A')
    name = '混合范围实例_STAGE_A'
    command = ({'action': 'update_node', 'id': fb['id'], 'field': 'symbol', 'value': name}
               if target == 'instance' else {'action': 'update_label', 'table':
               'Global1.gh' if target == 'hidden-global' else '1.Labels.lh',
               'name': 'TIMER_A', 'field': 'name', 'value': name})
    edited = edit_draft(model, command, context=context, templates=context.catalog())['model']
    after = read_project_context(generate_object_project(edited, baseline=raw).data)
    local_name = 'TIMER_A' if target == 'hidden-global' else name
    global_name = name if target == 'hidden-global' else 'TIMER_A'
    assert after.program.nodes[0].symbol == local_name
    assert after.declarations['1.Labels.lh'].rows[0].name == local_name
    assert [(r.name, r.data_type) for r in after.declarations['Global1.gh'].rows] == [(global_name, 'BOOL')]
    assert [p['name'] for p in after.object_model()['nodes'][0]['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    assert model['nodes'][0]['symbol'] == 'TIMER_A'


def test_hidden_global_edit_cannot_leave_a_renamed_local_fb_pointing_at_the_global_bool():
    from src.gxw.object_model import read_project_context
    context = read_project_context(source_binding_shadow_baseline())
    model = context.object_model()
    model['declaration_edits'] = {'1.Labels.lh': {'renames': {'TIMER_A': 'LOCAL_NEXT'}}}
    with pytest.raises(GXWFormatError):
        generate_object_project(model, baseline=context.raw)
    model = context.object_model()
    model['declaration_edits'] = {'1.Labels.lh': {'upserts': [
        {'name': 'TIMER_A', 'data_type': 'BOOL', 'kind': 'variable'}]}}
    with pytest.raises(GXWFormatError):
        generate_object_project(model, baseline=context.raw)


@pytest.mark.parametrize('unrelated', ['范围TIMER_A', 'TIMER_A变', 'A_TIMER_A'])
def test_scoped_fb_rename_preserves_an_independent_unicode_identifier_containing_the_old_name(unrelated):
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import read_project_context
    raw = source_binding_shadow_baseline()
    context = read_project_context(raw)
    model = context.object_model()
    terminal = next(node for node in model['nodes'] if node['template'] == 'input')
    terminal['symbol'] = unrelated
    model['declaration_edits'] = {'1.Labels.lh': {'upserts': [
        {'name': unrelated, 'data_type': 'BOOL', 'class_name': 'VAR'}]}}
    raw = generate_object_project(model, baseline=raw).data
    context = read_project_context(raw)
    edited = edit_draft(context.object_model(), {'action': 'update_label', 'table': '1.Labels.lh',
        'name': 'TIMER_A', 'field': 'name', 'value': 'LOCAL_'}, context=context)['model']
    final = read_project_context(generate_object_project(edited, baseline=raw).data)
    assert any(node.symbol == unrelated for node in final.program.nodes)
    assert any(row.name == unrelated and row.data_type == 'BOOL' for row in final.declarations['1.Labels.lh'].rows)
    assert final.program.nodes[0].symbol == 'LOCAL_'
    assert any(row.name == 'TIMER_A' and row.data_type == 'BOOL' for row in final.declarations['Global1.gh'].rows)


@pytest.mark.parametrize("case", ["s", "fn", "f2", "t2c"])
def test_import_and_unchanged_object_export_preserve_every_project_byte(case):
    raw = baseline(case)
    p, labels, _ = read_project(raw)
    model = export_object_model(p, labels)
    assert generate_object_project(model, baseline=raw).data == raw
    ET.fromstring(render_structured_svg(p))


def two_timers():
    return {"schema_version": 1, "program": "1.Program.pou", "canvas_height": 10,
            "nodes": [
                {"id": "a", "template": "function_block:TON", "symbol": "TIMER_A", "x": 8, "y": 2},
                {"id": "b", "template": "function_block:TON", "symbol": "TIMER_B", "x": 21, "y": 2},
                {"id": "x", "template": "input", "symbol": "X0", "x": 6, "y": 3},
                {"id": "pt1", "template": "input", "symbol": "T#1s", "x": 6, "y": 4},
                {"id": "pt2", "template": "input", "symbol": "T#2s", "x": 19, "y": 4},
                {"id": "out", "template": "output", "symbol": "Y0", "x": 26, "y": 3},
            ], "wires": [{"start": [1, 0], "end": [1, 10]}, {"from": "a.Q", "to": "b.IN"}]}


def test_named_port_link_creates_native_wire_and_local_fb_bindings():
    result = generate_object_project(two_timers())
    p, labels, _ = read_project(result.data)
    net, = fb_connectivity_model(p)["nets"]
    assert net["connection"] == "wire_network"
    assert (net["sources"][0]["instance"], net["sinks"][0]["instance"]) == ("TIMER_A", "TIMER_B")
    assert [(r.name, r.type_reference) for r in labels['1.Labels.lh'].rows] == [("TIMER_A", "TON"), ("TIMER_B", "TON")]


@pytest.mark.parametrize('order', list(permutations(range(4))))
def test_project_callable_formals_follow_native_saved_numbers_after_display_reorder(order):
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import parse_declarations
    witness = json.loads((Path(__file__).parent / 'fixtures/gxw_fbd_source_library.json').read_text())['reordered_project_formals_native']
    document = parse_declarations(base64.b64decode(witness['declarations_base64']), logical_name=witness['logical_name'])
    reordered = replace(document, rows=tuple(document.rows[i] for i in order))
    sources = ProjectCallableSources(witness['cpu'], 'AUTHOR_FBD.Program.pou',
                                    {witness['logical_name']: reordered}, {})
    interface = sources.fixed_interface('FB_DOUBLE_CLICK')
    assert [[r['name'], r['declared_type'], r['class_code']] for r in interface['inputs']] == witness['native_input_formals']
    assert [[r['name'], r['declared_type'], r['class_code']] for r in interface['outputs']] == witness['native_output_formals']
    assert [r['source_offset'] for r in interface['inputs']] == [412, 114]
    assert [r.name for r in document.rows] == witness['displayed_order']


def test_project_callable_cannot_guess_a_formal_order_from_duplicate_saved_numbers():
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import parse_declarations
    witness = json.loads((Path(__file__).parent / 'fixtures/gxw_fbd_source_library.json').read_text())['reordered_project_formals_native']
    document = parse_declarations(base64.b64decode(witness['declarations_base64']), logical_name=witness['logical_name'])
    document = replace(document, rows=tuple(replace(r, record_id=1) if r.name == 'PERIOD' else r for r in document.rows))
    sources = ProjectCallableSources(witness['cpu'], 'AUTHOR_FBD.Program.pou',
                                    {witness['logical_name']: document}, {})
    with pytest.raises(GXWFormatError, match='ambiguous saved declaration order'):
        sources.fixed_interface('FB_DOUBLE_CLICK')


@pytest.mark.parametrize('index', range(29))
def test_source_ports_match_frozen_native_formals_and_preserve_source_geometry(index):
    from src.gxw.structured_pou_writer import serialize_structured_pou
    from src.gxw.semantic import build_semantic_model
    program, declarations, sources, case = source_binding_witness(index)
    model = export_object_model(program, declarations, sources=sources)
    assert serialize_structured_pou(build_object_program(program, model, sources=sources)) == program.raw
    by_offset = {node.offset: node for node in program.nodes}
    for node in case['final_source_view']['nodes']:
        if 'interface' not in node:
            continue
        ports = sources.callable(by_offset[node['offset']])['ports']
        assert len({port['name'] for port in ports}) == len(ports)
        for expected in node['interface']['ports']:
            actual = ports[expected['index']]
            assert (actual['formal_name'], actual['side'], actual['data_type'], actual['class_code'], actual['occurrence']) == (
                expected['name'], expected['side'], expected['declared_type'], expected['class_code'], expected['occurrence'])
    semantics = build_semantic_model(program, function_block_instances=sources.semantic_specs(program))
    for block in semantics.function_blocks:
        assert block.type_known
        assert all(port.formal_name is not None for port in block.ports)


@pytest.mark.parametrize('case', json.loads((Path(__file__).parent /
    'fixtures/gxw_fbd_library_cpu_sections_native.json').read_text())['cases'],
    ids=lambda case: case['cpu'])
def test_native_cpu_name_selects_its_source_section_without_family_fallback(case):
    from src.gxw.callable_sources import ProjectCallableSources
    template = ('(*$SECTION:{section}*)\nFUNCTION_BLOCK CPU_BOUND\n'
        'VAR_INPUT\n{formal}: {data_type};\nEND_VAR\nEND_FUNCTION_BLOCK\n')
    library = (template.format(section=case['section'],formal='MATCHED',data_type='BOOL') +
        template.format(section='UNRELATED_CPU',formal='WRONG',data_type='WORD')).encode()
    sources = ProjectCallableSources(case['cpu'],'MAIN.Program.pou',{}, {'cpu.lif':library})
    formals = sources.fixed_interface('CPU_BOUND')
    assert [(p['name'],p['declared_type']) for p in formals['inputs']] == [('MATCHED','BOOL')]
    unknown = ProjectCallableSources(case['cpu'] + '_UNTESTED','MAIN.Program.pou',{}, {'cpu.lif':library})
    with pytest.raises(GXWFormatError,match='missing or ambiguous'):
        unknown.fixed_interface('CPU_BOUND')


@pytest.mark.parametrize('endpoint', ['in.STATE', 'out.STATE'])
def test_source_inout_endpoints_bind_distinct_native_sides(endpoint):
    from src.gxw.connectivity import build_connectivity_graph
    program, declarations, sources, _ = source_binding_witness()
    model = export_object_model(program, declarations, sources=sources)
    fb = model['nodes'][0]
    selected = next(port for port in fb['ports'] if port['name'] == endpoint)
    model['nodes'].append({'id': 'target', 'template': 'output', 'symbol': 'D30',
                           'x': fb['x'] + selected['x'], 'y': fb['y'] + selected['y'] - 1})
    model['wires'].append({'from': fb['id'] + '.' + endpoint, 'to': 'target.IN'})
    result = build_object_program(program, model, sources=sources)
    target = next(node for node in result.nodes if node.symbol == 'D30')
    index = fb['ports'].index(selected)
    graph = build_connectivity_graph(result)
    assert graph.ports_connected(fb['source_offset'], index, target.offset, 0)
    assert not graph.ports_connected(fb['source_offset'], 3 if index == 1 else 1, target.offset, 0)
    model['wires'][-1]['from'] = fb['id'] + '.STATE'
    with pytest.raises(GXWFormatError, match='unknown wire endpoint'):
        build_object_program(program, model, sources=sources)


def test_source_bound_instance_rename_is_atomic_and_foreign_type_edit_is_rejected():
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.editor import edit_draft
    raw = source_binding_baseline()
    program, declarations, _ = read_project(raw)
    sources = ProjectCallableSources.from_project(raw, program.logical_name, declarations)
    original = export_object_model(program, declarations, sources=sources)
    assert generate_object_project(original, baseline=raw).data == raw
    renamed = edit_draft(original, {'action': 'update_label', 'table': '1.Labels.lh', 'name': 'TIMER_A',
                                   'field': 'name', 'value': 'SEQUENCE_DELAY_STAGE_A'})['model']
    assert original['nodes'][0]['symbol'] == 'TIMER_A'
    changed = generate_object_project(renamed, baseline=raw).data
    after, labels, _ = read_project(changed)
    assert after.nodes[0].symbol == 'SEQUENCE_DELAY_STAGE_A'
    assert [row.name for row in labels['1.Labels.lh'].rows] == ['SEQUENCE_DELAY_STAGE_A', 'TIMER_B']
    label_only = deepcopy(original)
    label_only['declaration_edits'] = {'1.Labels.lh': {'renames': {'TIMER_A': 'NEW_NAME'}}}
    with pytest.raises(GXWFormatError, match='update graph and declaration together'):
        generate_object_project(label_only, baseline=raw)
    wrong_type = deepcopy(original)
    wrong_type['declaration_edits'] = {'1.Labels.lh': {'upserts': [
        {'name': 'TIMER_A', 'kind': 'function_block', 'data_type': 'TON'}]}}
    with pytest.raises(GXWFormatError, match='same instance name and type'):
        generate_object_project(wrong_type, baseline=raw)


@pytest.mark.parametrize('type_code,type_reference', [(15, 'TON'), (0, '')])
def test_current_declared_fb_type_binds_ports_with_retained_native_type_fields(type_code, type_reference):
    from src.gxw.declarations import parse_declarations, serialize_declarations, edit_declarations
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import ProjectSourceContext
    program, declarations, sources, _ = source_binding_witness()
    table = '1.Labels.lh'
    original = declarations[table]
    # Native SetVariableDataType and cold save retain the prior reference even
    # though the current declaration and graph both use the replacement FB.
    retained = replace(original, rows=tuple(
        replace(row, type_code=type_code, type_reference=type_reference) if row.name == 'TIMER_A' else row
        for row in original.rows))
    reread = parse_declarations(serialize_declarations(retained), logical_name=table)
    current_docs = {**declarations, table: reread}
    current = sources.with_declarations(current_docs)
    context = ProjectSourceContext(b'', program, current_docs, [program.logical_name], current)
    model = context.object_model()
    assert [p['name'] for p in model['nodes'][0]['ports']] == [
        'SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    assert not any(issue['code'] in ('callable_source_gap', 'unknown_function_block_type')
                   for issue in model['issues'])
    assert model['labels'][table][0]['kind'] == 'function_block'
    draft = edit_draft(model, {'action': 'update_label', 'table': table, 'name': 'TIMER_A',
        'field': 'name', 'value': 'FLOW_RENAMED'}, context=context)['model']
    updated = edit_declarations(reread, renames={'TIMER_A': 'FLOW_RENAMED'})
    pending = current.with_declarations({**current_docs, table: updated})
    rebuilt = build_object_program(program, draft, sources=pending, original_sources=current)
    assert rebuilt.nodes[0].symbol == 'FLOW_RENAMED'
    assert [p['name'] for p in pending.callable(rebuilt.nodes[0])['ports']] == [
        'SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    saved = parse_declarations(serialize_declarations(updated), logical_name=table)
    assert saved.rows[0].data_type == 'FLOW_PORTS'
    assert saved.rows[0].type_reference == type_reference
    assert saved.rows[0].type_code == type_code
    assert saved.rows[1].raw == reread.rows[1].raw
    from src.gxw.project_writer import _bind_function_blocks
    rebound = _bind_function_blocks({program.logical_name: program}, {table: reread}, {}, {})
    assert rebound[table] == reread
    # Type editing uses the resolved FB kind even when native bookkeeping is
    # uninitialized; callers still have to change the graphic interface too.
    typed = edit_draft(model, {'action': 'update_label', 'table': table, 'name': 'TIMER_A',
        'field': 'data_type', 'value': 'TON'}, context=context)['model']
    assert typed['declaration_edits'][table]['upserts'] == [
        {'name': 'TIMER_A', 'kind': 'function_block', 'data_type': 'TON'}]


def test_verified_source_prototype_adds_a_custom_fb_and_synchronizes_its_label():
    from src.gxw.callable_sources import ProjectCallableSources
    raw = source_binding_baseline()
    program, declarations, _ = read_project(raw)
    sources = ProjectCallableSources.from_project(raw, program.logical_name, declarations)
    model = export_object_model(program, declarations, sources=sources)
    prototype = model['nodes'][0]
    model['nodes'].append({'id': 'clone', 'template': prototype['template'],
        'prototype_offset': prototype['source_offset'], 'symbol': 'TIMER_C', 'x': 35, 'y': 2})
    clone = next(item for item in catalog_description(sources=sources, program=program)
                 if item.get('prototype_offset') == prototype['source_offset'])
    assert [port['name'] for port in clone['ports']] == ['SIGNAL', 'in.STATE', 'RESULT', 'out.STATE']
    result = generate_object_project(model, baseline=raw)
    after, labels, _ = read_project(result.data)
    assert after.nodes[-1].type_name == 'FLOW_PORTS'
    assert [(row.name, row.type_reference) for row in labels['1.Labels.lh'].rows][-1] == ('TIMER_C', 'FLOW_PORTS')
    model['nodes'][-1].pop('prototype_offset')
    generated, generated_labels, _ = read_project(generate_object_project(model, baseline=raw).data)
    generic = generated.nodes[-1]
    assert generic.type_name == 'FLOW_PORTS'
    assert [port.port_kind_code for port in generic.ports] == [1, 1, 0, 0]
    assert [(row.name, row.type_reference) for row in generated_labels['1.Labels.lh'].rows][-1] == ('TIMER_C', 'FLOW_PORTS')


@pytest.mark.parametrize('endpoint', ['in.STATE', 'out.STATE'])
def test_project_fb_interface_comes_from_owner_kind_and_formal_rows_without_implicit_enable(endpoint):
    import struct
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import _string, parse_declarations, edit_declarations, serialize_declarations
    from tests.test_gxw_declarations import document
    base = document('d0')
    owner_end = 54 + len(_string(base.owner_name))
    raw = (base.raw[:54] + _string('LOCAL_FLOW') + struct.pack('<II',0x1000002,1)
           + base.raw[owner_end+8:])
    definition = parse_declarations(raw, logical_name='LOCAL_FLOW.Labels.lh')
    definition = edit_declarations(definition, upserts=[
        {'name':'SIGNAL','data_type':'BOOL','class_name':'VAR_INPUT'},
        {'name':'NUMBER','data_type':'STRING[20]','class_name':'VAR_OUTPUT'},
        {'name':'STATE','data_type':'WORD','class_name':'VAR_IN_OUT'},
        {'name':'WORK','data_type':'ARRAY [0..3] OF INT'}])
    definition = parse_declarations(serialize_declarations(definition),logical_name=definition.logical_name)
    source, declarations, _ = read_project(default_baseline())
    context = ProjectCallableSources('Q03UDV',source.logical_name,
        {**declarations, definition.logical_name:definition},{})
    model = export_object_model(source,declarations,sources=context)
    model['nodes'] = [{'id':'flow','template':'function_block:LOCAL_FLOW','symbol':'FLOW_A','x':8,'y':2}]
    model['wires'] = []
    rebuilt = build_object_program(source,model,sources=context)
    ports = context.callable(rebuilt.nodes[0],bind_instance=False)['ports']
    assert [(p['name'],p['side'],p['data_type'],p['class_code'],p['negated']) for p in ports] == [
        ('SIGNAL','in','BOOL',3,False),('in.STATE','in','WORD',5,False),
        ('NUMBER','out','STRING[20]',4,False),('out.STATE','out','WORD',5,False)]
    changed = edit_declarations(definition,renames={'STATE':'NEXT_STATE'})
    updated = context.with_declarations({**context.declarations,definition.logical_name:changed})
    assert [p['name'] for p in updated.callable(rebuilt.nodes[0],bind_instance=False)['ports']] == [
        'SIGNAL','in.NEXT_STATE','NUMBER','out.NEXT_STATE']
    # The source fields stay read-only while new named connections resolve
    # against pending formals, before a candidate is saved and reread.
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou
    from src.gxw.connectivity import build_connectivity_graph
    caller = edit_declarations(declarations['1.Labels.lh'], upserts=[
        {'name':'FLOW_A','data_type':'LOCAL_FLOW','kind':'function_block'}])
    source = parse_structured_pou(serialize_structured_pou(rebuilt), logical_name=source.logical_name)
    current = context.with_declarations({**context.declarations,'1.Labels.lh':caller})
    model = export_object_model(source,current.declarations,sources=current)
    target = model['nodes'][0]
    port = next(p for p in target['ports'] if p['name']=='out.STATE')
    model['nodes'].append({'id':'state_out','template':'output','symbol':'D30',
                          'x':target['x']+port['x']+1,'y':target['y']+port['y']-1})
    model['wires'] = [{'from':target['id']+'.out.NEXT_STATE','to':'state_out.IN'}]
    pending = current.with_declarations({**current.declarations,definition.logical_name:changed})
    from src.gxw.editor import edit_draft
    from src.gxw.object_model import ProjectSourceContext
    draft_context = ProjectSourceContext(b'', source, current.declarations, [source.logical_name], current)
    original_draft = deepcopy(model)
    original_draft['wires'] = []
    port_edit = edit_draft(original_draft, {'action': 'update_port', 'id': target['id'],
        'port': endpoint, 'negated': True}, context=draft_context)['model']
    wired = edit_draft(port_edit, {'action': 'add_wire', 'from': target['id'] + '.' + endpoint,
        'to': 'state_out.IN'}, context=draft_context)['model']
    rebound = edit_draft(wired, {'action': 'update_label', 'table': definition.logical_name,
        'name': 'STATE', 'field': 'name', 'value': 'NEXT_STATE'}, context=draft_context)
    renamed_endpoint = endpoint.replace('STATE', 'NEXT_STATE')
    assert rebound['model']['nodes'][0]['port_edits'] == {renamed_endpoint: {'negated': True}}
    assert rebound['model']['wires'][0]['from'] == target['id'] + '.' + renamed_endpoint
    assert original_draft['nodes'][0]['ports'] == target['ports']
    rebound_program = build_object_program(source, rebound['model'], sources=pending, original_sources=current)
    rebound_block = rebound_program.nodes[0]
    rebound_ports = pending.callable(rebound_block)['ports']
    assert [p['name'] for p in rebound_ports if p['negated']] == [renamed_endpoint]
    rebound_terminal = next(n for n in rebound_program.nodes if n.symbol == 'D30')
    rebound_index = next(i for i, p in enumerate(rebound_ports) if p['name'] == renamed_endpoint)
    assert build_connectivity_graph(rebound_program).ports_connected(rebound_block.offset,
        rebound_index, rebound_terminal.offset, 0)
    model['declaration_edits'] = {definition.logical_name: {'renames': {'STATE': 'NEXT_STATE'}}}
    model['wires'] = []
    view = edit_draft(model, {'action': 'add_wire', 'from': target['id']+'.out.NEXT_STATE',
        'to': 'state_out.IN'}, context=draft_context,
        templates=draft_context.catalog(declaration_edits=model['declaration_edits']))
    assert [p['name'] for p in view['presentation']['nodes'][0]['ports']] == [
        'SIGNAL','in.NEXT_STATE','NUMBER','out.NEXT_STATE']
    assert [p['name'] for p in view['model']['nodes'][0]['ports']] == [
        'SIGNAL','in.STATE','NUMBER','out.STATE']
    model = view['model']
    connected = build_object_program(source,model,sources=pending,original_sources=current)
    terminal = next(n for n in connected.nodes if n.symbol=='D30')
    block = connected.nodes[0]
    next_index = next(i for i,p in enumerate(pending.callable(block)['ports']) if p['name']=='out.NEXT_STATE')
    assert build_connectivity_graph(connected).ports_connected(block.offset,next_index,terminal.offset,0)
    incompatible = current.with_declarations({**current.declarations,
        definition.logical_name:edit_declarations(definition,remove=['STATE'])})
    with pytest.raises(GXWFormatError,match='source formal interface differs'):
        build_object_program(source,model,sources=incompatible,original_sources=current)
    # Formal classes do not turn an ordinary program into a callable POU.
    as_program = replace(definition,owner_pou_type=0x1000000)
    unavailable = context.with_declarations({**context.declarations,definition.logical_name:as_program})
    with pytest.raises(GXWFormatError,match='missing or ambiguous'):
        unavailable.callable(rebuilt.nodes[0],bind_instance=False)


@pytest.mark.parametrize('endpoint', ['in.STATE','out.STATE'])
def test_callable_negation_is_explicit_and_independent_of_inout_binding(endpoint):
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.editor import edit_draft
    raw = source_binding_baseline()
    source, docs, _ = read_project(raw)
    context = ProjectCallableSources.from_project(raw,source.logical_name,docs)
    model = export_object_model(source,docs,sources=context)
    target = model['nodes'][0]
    assert not next(p for p in target['ports'] if p['name']==endpoint)['negated']
    edited = edit_draft(model,{'action':'update_port','id':target['id'],'port':endpoint,'negated':True})['model']
    output = generate_object_project(edited,baseline=raw).data
    after, after_docs, _ = read_project(output)
    updated = ProjectCallableSources.from_project(output,after.logical_name,after_docs)
    actual = updated.callable(after.nodes[0])['ports']
    old_ports = context.callable(source.nodes[0])['ports']
    assert [(p['name'],p['class_code'],p['side']) for p in actual] == [(p['name'],p['class_code'],p['side']) for p in old_ports]
    assert [p['negated'] for p in actual] == [True if p['name']==endpoint else p['negated'] for p in old_ports]
    assert docs['1.Labels.lh'].rows == after_docs['1.Labels.lh'].rows
    invalid = deepcopy(edited)
    invalid['nodes'][0]['port_edits'] = {'STATE':{'negated':False}}
    with pytest.raises(GXWFormatError,match='source endpoint'):
        generate_object_project(invalid,baseline=raw)


@pytest.mark.parametrize('damage', ['missing', 'type', 'local_global', 'cpu', 'duplicate_library'])
def test_unresolved_source_bindings_remain_explicit_and_cannot_be_cloned(damage):
    from src.gxw.callable_sources import ProjectCallableSources
    from src.gxw.declarations import edit_declarations
    program, declarations, sources, case = source_binding_witness()
    if damage == 'missing':
        declarations['1.Labels.lh'] = edit_declarations(declarations['1.Labels.lh'], remove=['TIMER_A'])
    elif damage == 'type':
        declarations['1.Labels.lh'] = edit_declarations(declarations['1.Labels.lh'], upserts=[
            {'name': 'TIMER_A', 'data_type': 'TON', 'kind': 'function_block'}])
    elif damage == 'local_global':
        declarations['Global1.gh'] = edit_declarations(declarations['Global1.gh'], upserts=[
            {'name': 'TIMER_A', 'data_type': 'FLOW_PORTS', 'kind': 'function_block', 'class_name': 'VAR_GLOBAL'}])
    elif damage == 'cpu':
        definition = case['definitions'][0]
        text = b'(*$SECTION:FX3U*)\n' + base64.b64decode(definition['declaration_prefix_base64']) + b'END_FUNCTION_BLOCK\n'
        sources = ProjectCallableSources('UNOBSERVED_CPU', program.logical_name, declarations, {'library.lif': text})
    else:
        name = program.nodes[0].type_name.casefold()
        sources.catalog[name].append(sources.catalog[name][0])
    sources = sources.with_declarations(declarations)
    model = export_object_model(program, declarations, sources=sources)
    node = model['nodes'][0]
    assert [port['name'] for port in node['ports']] == ['0', '1', '2', '3']
    assert any(issue['code'] == 'callable_source_gap' and issue.get('node_offset') == node['source_offset']
               for issue in model['issues'])
    model['nodes'].append({'id': 'clone', 'template': node['template'], 'prototype_offset': node['source_offset'],
                           'symbol': 'TIMER_C', 'x': 35, 'y': 2})
    with pytest.raises(GXWFormatError):
        build_object_program(program, model, sources=sources)


def test_unregistered_framed_source_record_is_preserved_during_known_object_edits():
    import struct
    from src.gxw.models import UnknownRecord
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou
    source, declarations, _ = read_project(default_baseline())
    opaque = UnknownRecord(max(record.offset for record in source.iter_records()) + 1, 12, 99,
                           struct.pack('<III', 12, 99, 0x12345678))
    with_unknown = replace(source, unknown_records=(opaque,), record_count=source.record_count + 1)
    encoded = serialize_structured_pou(with_unknown)
    program = parse_structured_pou(encoded, logical_name=source.logical_name, preserve_unsupported_records=True)
    model = export_object_model(program, declarations)
    model['nodes'][0]['symbol'] = 'X3'
    changed = build_object_program(program, model)
    assert changed.unknown_records[0].raw == opaque.raw
    assert parse_structured_pou(serialize_structured_pou(changed), preserve_unsupported_records=True).unknown_records[0].raw == opaque.raw


@pytest.mark.parametrize("damage", ["node_id", "offset", "template", "port", "diagonal", "dimensions"])
def test_invalid_models_fail_explicitly(damage):
    model = two_timers()
    if damage == "node_id": model["nodes"][1]["id"] = "a"
    if damage == "offset": model["nodes"][0]["source_offset"] = 99999
    if damage == "template": model["nodes"][0]["template"] = "function_block:UNKNOWN"
    if damage == "port": model["wires"][1]["from"] = "a.FAKE"
    if damage == "diagonal": model["nodes"][1]["y"] += 1
    if damage == "dimensions": model["nodes"][0]["width"] = 100
    with pytest.raises(GXWFormatError):
        generate_object_project(model)


def test_preview_escapes_symbols_as_xml_text():
    p, _, _ = read_project(default_baseline())
    p = replace(p, nodes=(replace(p.nodes[0], symbol='<script>alert("x")</script>'), *p.nodes[1:]))
    svg = render_structured_svg(p)
    root = ET.fromstring(svg)
    assert not root.findall('.//{http://www.w3.org/2000/svg}script')
    assert '&lt;script&gt;' in svg


def test_ladder_conversion_retains_series_parallel_and_multiple_outputs():
    ladder = {"device_comments": {}, "rungs": [{"rung_id": 1, "shared_inputs": [{"type": "NO", "address": "X0"}],
        "branches": [{"inputs": [{"type": "parallel_block", "branches": [
            [{"type": "NO", "address": "X1"}], [{"type": "NC", "address": "X2"}]]}],
            "outputs": [{"type": "COIL", "address": "Y0"}, {"type": "COIL", "address": "Y1"}]}]},
        {"rung_id": 2, "branches": [{"inputs": [{"type": "NO", "address": "X3"}],
                                     "outputs": [{"type": "COIL", "address": "Y2"}]}]}]}
    result = generate_object_project(ladder_to_object_model(ladder))
    p, _, _ = read_project(result.data)
    from src.gxw.connectivity import build_connectivity_graph
    graph = build_connectivity_graph(p)
    by_name = {n.symbol: n for n in p.nodes}
    for port in (0, 1):
        assert graph.ports_connected(by_name['X1'].offset, port, by_name['X2'].offset, port)
    assert graph.ports_connected(by_name['X0'].offset, 1, by_name['X1'].offset, 0)
    assert graph.ports_connected(by_name['X1'].offset, 1, by_name['Y0'].offset, 0)
    assert graph.ports_connected(by_name['Y0'].offset, 0, by_name['Y1'].offset, 0)
    assert not graph.ports_connected(by_name['Y0'].offset, 0, by_name['Y2'].offset, 0)
    assert by_name['X2'].kind == NodeKind.CONTACT_NC


@pytest.mark.parametrize("case,marker", [
    ("coil-05-port-067", "S"),
    ("coil-07-port-003", "S"),
    ("coil-08-port-003", "R"),
    ("contact-17-port-003", "↑"),
    ("contact-18-port-003", "↓"),
    ("coil-07-port-011", None),
])
def test_native_ladder_modifiers_survive_object_roundtrip_and_preview(case, marker):
    from src.gxw.structured_pou import parse_structured_pou
    from src.gxw.structured_pou_writer import serialize_structured_pou

    cases = json.loads((Path(__file__).parent / "fixtures/gxw_ladder_primitives.json").read_text())["cases"]
    row = next(r for r in cases if r["case"] == case)
    raw = base64.b64decode(row["program_base64"])
    program = parse_structured_pou(raw, logical_name="1.Program.pou")
    model = export_object_model(program)
    rebuilt = build_object_program(program, model)
    assert serialize_structured_pou(rebuilt) == raw
    root = ET.fromstring(render_structured_svg(program))
    texts = [n.text for n in root.findall('.//{http://www.w3.org/2000/svg}text')]
    if marker:
        assert marker in texts
    else:
        # The native SET-kind/port-11 counterexample compiles to inverted OUT.
        assert "S" not in texts and "R" not in texts
    imported = next(n for n in model["nodes"] if n["symbol"] == row["target_symbol"])
    if row["kind"] in {7, 8, 17, 18}:
        imported.pop("source_offset")
        with pytest.raises(GXWFormatError, match="no verified native ABI template"):
            build_object_program(program, model)
