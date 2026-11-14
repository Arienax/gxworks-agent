"""Model delivery omits optional CPU version checks, never source facts."""
import copy
import json

from shared.cpu_version_projection import (
    cpu_version_requested, without_cpu_versions, project_cpu_version_knowledge, project_cpu_version_text,
)


def packet():
    return {'kind': 'instruction', 'target': 'TEST', 'scope': {'models': ['FX3G', 'Q03UDE']}, 'value': {
        'forms': [{'opcode': 'TEST', 'applicability': [
            {'available': True},
            {'conditions': [{'context': 'cpu_version', 'minimum_by_model': {'FX3G': '1.10'}}],
             'value': {'available': True}},
            {'conditions': [{'context': 'cpu_version', 'maximum_exclusive': '1.10'}],
             'value': {'available': False}}]}],
        'parameter_tables': [{'parameters': [{'name': 'S', 'bits': 32, 'word_count': 2}]}],
        'additional_knowledge': [
            {'semantics': {'constraints.cpu_version': {'minimum_series_version': '1.10'},
                           'constraints.device_models': {'allowed_models': ['Q03UDE']}}},
            {'conditions': [{'context': 'software_version', 'minimum': '2.00'}],
             'semantics': {'parameter_count': 3}}]}}


def test_projection_removes_whole_version_branches_and_keeps_model_and_software_constraints():
    original = packet()
    before = copy.deepcopy(original)
    clean = without_cpu_versions(original)
    assert original == before
    assert clean['scope'] == original['scope']
    assert clean['value']['forms'][0]['applicability'] == [{'available': True}]
    assert clean['value']['parameter_tables'] == original['value']['parameter_tables']
    assert 'software_version' in json.dumps(clean)
    assert 'constraints.device_models' in json.dumps(clean)
    assert 'cpu_version' not in json.dumps(clean)
    assert 'minimum_series_version' not in json.dumps(clean)
    assert project_cpu_version_knowledge({'knowledge': [original]}, requested=True)['knowledge'] == [original]


def test_knowledge_text_projection_keeps_requirement_and_schema_fields():
    source = packet()
    text = '[PLC KNOWLEDGE]\n' + json.dumps(source) + '\n[/PLC KNOWLEDGE]'
    assert 'cpu_version' not in project_cpu_version_text(text)
    assert project_cpu_version_text(text, requested=True) == text
    assert 'cpu_version' not in project_cpu_version_text(json.dumps(source))
    output = {'generation_input': {'knowledge': [source], 'output_schema': {
        'properties': {'cpu_version': {'type': 'string'}}}}, 'request': '用户提到 CPU 版本'}
    clean = project_cpu_version_knowledge(output)
    assert clean['request'] == output['request']
    assert clean['generation_input']['output_schema'] == output['generation_input']['output_schema']
    assert 'cpu_version' not in json.dumps(clean['generation_input']['knowledge'])


def test_only_explicit_version_interest_enables_delivery():
    assert not cpu_version_requested('FX3U 使用成组指令实现控制')
    assert not cpu_version_requested('不检查 CPU 版本')
    assert cpu_version_requested('核对 FX3U CPU 版本是否支持')
    assert cpu_version_requested('', {'hardware_profile': {'cpu_version': '1.10'}})
    assert not cpu_version_requested('', {'selected_approach': {'cpu_version': '1.10'}})


def test_manual_tool_default_and_explicit_version_delivery_use_same_source():
    from agent_runtime.runtime import build_default_tool_runtime
    from agent_runtime.plc_tools import build_tool_context
    from agent_runtime.messages import ToolCall
    from application.read_delivery import assemble_read_pages
    runtime = build_default_tool_runtime()
    context = build_tool_context({'id': 'projection-test', 'plc_model': 'FX3U', 'target_mode': 'ladder'})
    for requested in (False, True):
        args = {'query': 'SFTL SFWR OUT', 'plc_model': 'FX3U', 'check_cpu_version': requested}
        pages = []
        while True:
            result = runtime.invoke(ToolCall('read', 'search_plc_manual', args), context)
            assert not result.is_error, result.data
            pages.append(result.data)
            follow = result.data['data']['delivery']['next']
            if not follow:
                break
            args = follow['arguments']
        wire = json.dumps(assemble_read_pages(pages))
        assert ('minimum_by_model' in wire) == requested
        assert 'parameter_tables' in wire and 'SFTL' in wire and 'SFWR' in wire
