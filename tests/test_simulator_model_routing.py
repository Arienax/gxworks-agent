"""Model identity must reach every native observation and test entry point."""
from types import SimpleNamespace

import pytest

from application.program_inspection import ProgramInspectionService
from agent_runtime.tool_errors import ToolPreconditionError
from plc.device_policy import native_simulator_profile, native_simulator_model


class Gateway:
    def __init__(self, model, value=0):
        self.plc_model = model
        self.profile = native_simulator_profile(model)
        self.value = value
        self.plans = []

    def health(self):
        return dict(simulator_only=True, route='GX Simulator2', unit_type=48,
                    connected=True, target_binding='one', cpu_type=self.profile['cpu_type'],
                    observed_cpu_type=self.profile['model_code'],
                    observed_cpu_name=self.plc_model + 'CPU',
                    capabilities={'target_cpu_binding': True})

    def read_bound(self, plan, binding):
        assert binding == 'one'
        self.plans.append(plan)
        return {item['key']: self.value if item['key'] == self.profile['device'] else 0 for item in plan}


@pytest.mark.parametrize('model,address,run', [('FX3G', 'X7', 1), ('Q06UDEH', 'X1F', 0x100), ('L06', 'X1F', 0x100)])
def test_discovery_read_and_run_use_connected_cpu(model, address, run):
    gateway = Gateway(model, run)
    service = ProgramInspectionService(gateway=gateway)
    target = service.list_simulator_targets()['targets'][0]
    assert target['plc_model'] == model
    ref = target['target_ref']
    assert service.read_simulator_devices(ref, [address])['values'] == {address: 0}
    status = service.get_simulator_status(ref)
    assert status['run_state'] == 'run'
    assert status['run_monitor'] == ('M8000' if model.startswith('FX') else 'SD203')
    gateway.value = 0 if model.startswith('FX') else 0x102
    assert service.get_simulator_status(ref)['run_state'] == 'not_running'


@pytest.mark.parametrize('model', ['Q06UDEH', 'L06', 'FX3G'])
def test_trace_inherits_model_and_rejects_mismatch_before_read(tmp_path, model):
    from application.simulator_view import SimulationViewService
    (tmp_path / 'simulator.html').write_text('<html></html>')
    gateway = Gateway(model)
    service = ProgramInspectionService(gateway=gateway)
    ref = service.list_simulator_targets()['targets'][0]['target_ref']
    views = SimulationViewService(static_dir=tmp_path)
    config = {'signals': [{'id': 'x', 'address': 'X7' if model.startswith('FX') else 'X1F', 'data_type': 'BOOL'}]}
    try:
        view = views.watch(service, ref, config)
        assert view['config']['plc_model'] == model
        views.stop(view['view_ref'])
        before = len(gateway.plans)
        with pytest.raises(ValueError):
            views.watch(service, ref, {**config, 'plc_model': 'FX3U'})
        assert len(gateway.plans) == before
    finally:
        views.close()


def test_cpu_identity_is_not_inferred_from_client_default_or_shared_code():
    health = Gateway('Q06UDEH').health()
    health['observed_cpu_name'] = 'Q06HCPU'
    with pytest.raises(ValueError):
        native_simulator_model(health)
    health['observed_cpu_name'] = ' Q06UDEHCPU '
    assert native_simulator_model(health) == 'Q06UDEH'
    for field in ('cpu_type', 'observed_cpu_type'):
        incomplete = {k: v for k, v in health.items() if k != field}
        with pytest.raises(ValueError):
            native_simulator_model(incomplete)
    with pytest.raises(ValueError):
        native_simulator_model({'capabilities': {'target_cpu_binding': True}})


def test_explicit_connect_preserves_runtime_default_and_reuses_matching_target(monkeypatch):
    import simulator.runtime
    gateway = Gateway('L06')
    runtime = SimpleNamespace(plc_model='FX3U', ensure_gateway=lambda: {}, client=lambda **_: gateway)
    monkeypatch.setattr(simulator.runtime, 'get_simulator_gateway_runtime', lambda: runtime)
    service = ProgramInspectionService()
    assert service.connect_simulator('L06-P')['targets'][0]['plc_model'] == 'L06'
    with pytest.raises(ToolPreconditionError) as error:
        service.connect_simulator('FX3U')
    assert error.value.code == 'SIMULATOR_TARGET_MISMATCH'
    assert runtime.plc_model == 'FX3U'


def test_explicit_connect_supplies_selected_cpu_before_open(monkeypatch):
    import simulator.runtime
    gateway = Gateway('FX3U')
    gateway.connected = False
    health = gateway.health
    gateway.health = lambda: health() if gateway.connected else {'connected': False}
    def connect():
        assert gateway.plc_model == 'Q06UDEH'
        gateway.profile = native_simulator_profile(gateway.plc_model)
        gateway.connected = True
    gateway.connect = connect
    runtime = SimpleNamespace(plc_model='FX3U', ensure_gateway=lambda: {}, client=lambda **_: gateway)
    monkeypatch.setattr(simulator.runtime, 'get_simulator_gateway_runtime', lambda: runtime)
    assert ProgramInspectionService().connect_simulator('Q06UDEH')['targets'][0]['plc_model'] == 'Q06UDEH'
    assert runtime.plc_model == 'FX3U'


def test_native_test_backend_uses_suite_model_and_keeps_real_connection_identity():
    from simulator.api import _backend_for_model
    from simulator.gateway import GXSimulatorGatewayClient
    assert _backend_for_model(None, 'L06').plc_model == 'L06'
    client = GXSimulatorGatewayClient()
    assert _backend_for_model(client, 'Q06UDEH') is client
    assert client.plc_model == 'Q06UDEH'
    client.connected = True
    with pytest.raises(ValueError):
        _backend_for_model(client, 'FX3U')
    assert client.plc_model == 'Q06UDEH'


def test_preparation_selects_cpu_before_native_startup():
    from gxworks2.simulation import GXSimulator2PreparationService
    runtime = SimpleNamespace(plc_model='FX3U')
    preparer = GXSimulator2PreparationService(runtime=runtime)
    preparer.select_model('Q06UDEH')
    assert runtime.plc_model == 'Q06UDEH'
    with pytest.raises(ValueError):
        preparer.select_model('FX5U')
    assert runtime.plc_model == 'Q06UDEH'


@pytest.mark.parametrize('model', ['FX1N', 'FX3G', 'Q00J', 'Q06UDEH', 'L06'])
def test_simulator_addresses_keep_model_radix_and_separate_native_scope(model):
    from plc.device_policy import native_simulator_read_plan, native_write_plan, native_reset_plan
    address = 'X17' if model.startswith('FX') else 'X1F'
    assert native_simulator_read_plan([address, 'Y0', 'M0', 'D0', 'T0', 'C0'], model) == [
        {'key': a, 'device': d} for a, d in [(address, address), ('Y0','Y0'), ('M0','M0'),
                                            ('D0','D0'), ('T0','TN0'), ('C0','CN0')]]
    assert native_write_plan({address: 1}, model, simulator=True)[0]['value'] == 1
    for values in ({'Y0': 1}, {'SD203': 0}, {'M8000': 0} if model.startswith('FX') else {'SM0': 0}):
        with pytest.raises(ValueError):
            native_write_plan(values, model, simulator=True)
    assert native_reset_plan(['Y0','T0','C0'], {address: 1}, model, simulator=True)['clear'][1]['device'] == 'TN0'
    with pytest.raises(ValueError):
        native_simulator_read_plan(['X8' if model.startswith('FX') else 'X1G'], model)


def test_q_high_ordinary_registers_are_not_fx_special_registers():
    from plc.device_policy import native_reset_plan
    assert native_reset_plan(['D9000', 'M9000'], {}, 'Q06UDEH', simulator=True)['clear'] == [
        {'key': 'D9000', 'device': 'D9000', 'value': 0},
        {'key': 'M9000', 'device': 'M9000', 'value': 0}]
    with pytest.raises(ValueError):
        native_reset_plan(['D8000'], {}, 'FX3U', simulator=True)
