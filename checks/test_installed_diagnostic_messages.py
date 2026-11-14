from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

from gxw import diagnostic_reports as reports


def _diagnostic(code, argument='TestTON.IN', phase='compile'):
    return {'original': {'code': code, 'arguments': [{'text': argument}], 'kind': 2},
            'report_origin': {'phase': phase, 'poll': 3, 'report_index': 1},
            'source_projection': {'status': 'unresolved'}}


def _lookup(rows):
    return {'status': 'queried', 'installation': 'selected-installation',
            'database': 'selected-installation/Error.mdb', 'language': 'en',
            'versions': {'compiler': 'actual-version'},
            'servers': [{'id': 6, 'name': 'Compiler Server'}], 'rows': rows}


def _message(code, text='Installed resource message'):
    return {'Code': code, 'Message': text, 'Recover': 'Installed recovery',
            'ID': 961, 'ServerID': 6, 'Class': 15, 'Define': 'INSTALLED_DEFINE'}


def test_messages_preserve_reports_order_stage_and_compilation_outcome(monkeypatch):
    first = _diagnostic(0x050f1034)
    second = _diagnostic(0x050f0160, 'DMOV K100 D190', 'check')
    unknown = _diagnostic(0x12345678)
    result = {'adapter': {'exit_code': 1}, 'compilation': {'status': 'rejected',
              'source_diagnostics': [first, second, unknown]},
              'diagnostic_projection': {'source_diagnostics': [first]},
              'public_check': {'completed': False}}
    before = deepcopy(result)
    calls = []

    def query(installation, codes):
        calls.append((installation, codes))
        return _lookup([_message(0x050f1034), _message(0x050f0160)])

    monkeypatch.setattr(reports, '_installed_diagnostic_messages', query)
    reports.bind_installed_diagnostic_messages(result, 'selected-installation')
    assert calls == [('selected-installation', {0x050f1034, 0x050f0160, 0x12345678})]
    for current, previous in zip(result['compilation']['source_diagnostics'],
                                 before['compilation']['source_diagnostics']):
        assert current['original'] == previous['original']
        assert current['source_projection'] == previous['source_projection']
        assert {k: v for k, v in current['report_origin'].items() if k != 'message_resource'} == previous['report_origin']
    assert first['message'] == 'Installed resource message'
    assert second['recovery'] == 'Installed recovery'
    assert first['report_origin']['message_resource']['versions'] == {'compiler': 'actual-version'}
    assert 'message' not in unknown and 'message_resource' not in unknown['report_origin']
    assert result['adapter'] == before['adapter']
    assert result['compilation']['status'] == 'rejected'
    assert result['public_check'] == before['public_check']


def test_missing_resources_preserve_original_failure(monkeypatch):
    result = {'compilation': {'status': 'rejected', 'source_diagnostics': [_diagnostic(0x050f0028)]}}
    before = deepcopy(result)
    monkeypatch.setattr(reports, '_installed_diagnostic_messages',
                        lambda *_: {'status': 'unavailable', 'reason': 'missing database'})
    reports.bind_installed_diagnostic_messages(result, 'missing-installation')
    assert result['compilation'] == before['compilation']
    assert result['adapter']['diagnostic_message_lookup']['status'] == 'unavailable'


def test_duplicate_resource_rows_do_not_choose_an_arbitrary_message(monkeypatch):
    diagnostic = _diagnostic(0x050f0028)
    before = deepcopy(diagnostic)
    monkeypatch.setattr(reports, '_installed_diagnostic_messages',
                        lambda *_: _lookup([_message(0x050f0028, 'a'), _message(0x050f0028, 'b')]))
    reports.bind_installed_diagnostic_messages({'raw_diagnostics': [diagnostic]}, 'selected-installation')
    assert diagnostic == before


@pytest.mark.skipif(os.name != 'nt', reason='Windows resource reader host')
def test_selected_installation_is_stdin_data_not_shell_code(monkeypatch):
    import subprocess
    calls = []
    installation = r"D:\selected '$(); installation"

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(_lookup([])).encode())

    monkeypatch.setattr(subprocess, 'run', run)
    reports._installed_diagnostic_messages(installation, {0x050f1034})
    command, options = calls[0]
    assert installation not in ' '.join(command)
    assert json.loads(options['input']) == {'installation': installation, 'codes': [0x050f1034]}
    assert not options.get('shell')


@pytest.mark.skipif(os.name != 'nt', reason='Windows resource reader host')
def test_reader_failure_does_not_escape_to_native_validation(monkeypatch):
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr(subprocess, 'run', timeout)
    assert reports._installed_diagnostic_messages('selected-installation', {1})['status'] == 'unavailable'


def test_validation_tool_attaches_messages_after_projection_without_a_new_native_check(monkeypatch, tmp_path):
    from gxworks2 import workspace_adapter
    from gxw import project_metadata

    executable = tmp_path / 'existing-adapter.exe'
    executable.write_bytes(b'')
    adapter = object.__new__(workspace_adapter.NativeWorkspaceValidation)
    adapter.executable, adapter.installation = executable, tmp_path / 'selected-installation'
    observation = {'status': 'failed', 'validation': {'events': []}}
    diagnostic = _diagnostic(0x050f1034)
    projected = {'adapter': {}, 'compilation': {'status': 'rejected', 'source_diagnostics': [diagnostic]}}
    native_calls = []

    def native(*args):
        native_calls.append(args)
        return 1, observation

    monkeypatch.setattr(workspace_adapter, 'native_validation_source_plan', lambda raw: {})
    monkeypatch.setattr(workspace_adapter, '_run_native_adapter', native)
    monkeypatch.setattr(workspace_adapter, '_validation_snapshots', lambda *_: {})
    monkeypatch.setattr(workspace_adapter, 'project_native_validation', lambda *_: projected)
    monkeypatch.setattr(project_metadata, 'project_gui_configuration', lambda _: {})
    monkeypatch.setattr(reports, '_installed_diagnostic_messages',
                        lambda installation, codes: _lookup([_message(0x050f1034)]))
    result = adapter.validate(b'current project')
    assert len(native_calls) == 1
    assert native_calls[0][1]['installation'] == str(adapter.installation)
    assert result['compilation']['status'] == 'rejected'
    assert result['compilation']['source_diagnostics'][0]['message'] == 'Installed resource message'
    assert result['adapter']['exit_code'] == 1
