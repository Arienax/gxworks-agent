"""Run an isolated, explicitly labelled browser acceptance fixture.

No user workspace, credentials, network model, GX process or PLC is touched.
The temporary workspace is removed when the local preview is stopped.
"""
import argparse
import copy
from contextlib import ExitStack, contextmanager
import json
from itertools import count
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _demo_exception_diagnostic(error):
    """Offline fixture diagnostics omit exception bodies and request values."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        locations = []
        for frame in traceback.extract_tb(error.__traceback__):
            path = Path(frame.filename)
            try:
                path = path.resolve().relative_to(ROOT)
            except ValueError:
                path = Path(path.name)
            locations.append(f"{path}:{frame.lineno}:{frame.name}")
        print("DEMO_JOB_EXCEPTION " + type(error).__name__ + " " + " -> ".join(locations), file=sys.stderr, flush=True)
        error = error.__cause__ or error.__context__


@contextmanager
def isolated_demo_settings(directory, provider_factory=None):
    """Keep the real settings service, replacing only its process-local I/O."""
    import storage.config as config_manager
    import storage.credentials as credential_store
    import model_runtime.provider as model_provider
    from application.settings import SettingsService

    config_path = Path(directory) / "demo-config.json"
    target = credential_store.credential_target_for_profile("offline")
    config_path.write_text(json.dumps({"language": "zh-CN", "activeModelProfileId": "offline", "modelProfiles": [{
        "id": "offline", "name": "离线演示", "adapter": "openai_compatible", "model": "offline",
        "baseUrl": "https://offline.invalid/v1", "credentialTarget": target,
        "capabilities": {"tools": False}, "generationDefaults": {"temperature": 0.3}, "requestOverrides": {}}]},
        ensure_ascii=False), encoding="utf-8")
    keys = {target: "demo-key-stored-only-in-memory"}

    def read_key(target=credential_store.CREDENTIAL_TARGET):
        return keys.get(target, "")

    def write_key(value, target=credential_store.CREDENTIAL_TARGET):
        value = str(value or "").strip()
        if not value:
            raise ValueError("API Key 不能为空。")
        keys[target] = value

    def delete_key(target=credential_store.CREDENTIAL_TARGET):
        keys.pop(target, None)

    def connection_probe(profile, key):
        if key == "demo-fail":
            raise model_provider.ModelProviderError("Offline authentication failure", code="authentication")
        return "离线演示：连接测试通过（未访问外部服务）。"

    with ExitStack() as stack:
        stack.enter_context(patch.object(config_manager, "get_config_path", lambda: config_path))
        # config_manager captured these imports; isolate both references so even
        # a desktop compatibility helper cannot reach the user's Credential Manager.
        for module in (credential_store, config_manager):
            stack.enter_context(patch.object(module, "read_api_key", read_key))
            stack.enter_context(patch.object(module, "write_api_key", write_key))
        stack.enter_context(patch.object(credential_store, "delete_api_key", delete_key))
        stack.enter_context(patch.object(model_provider, "test_model_profile", connection_probe))
        if provider_factory is not None:
            stack.enter_context(patch.object(model_provider, "create_provider", provider_factory))
        yield SettingsService()


def demo_ladder():
    ladder = {"device_comments": {"X0": "启动", "X1": "停止", "X2": "保护输入", "M0": "运行保持", "Y0": "电机"}, "rungs": []}
    for i, (inputs, output) in enumerate([
        ([('NO', 'X0')], ('SET', 'M0')),
        ([('NO', 'X1')], ('RST', 'M0')),
        ([('NO', 'M0'), ('NC', 'X2')], ('COIL', 'Y0')),
    ], 1):
        ladder["rungs"].append({"rung_id": i, "header_element": None, "shared_inputs": [], "branches": [
            {"branch_id": 1, "y_offset_level": 0, "inputs": [{"type": kind, "address": address, "label": ""} for kind, address in inputs],
             "outputs": [{"type": "APP_INSTR", "opcode": output[0], "operands": [output[1]]} if output[0] in ("SET", "RST") else {"type": output[0], "address": output[1], "label": ""}]}]})

    return ladder


class DemoProvider:
    calls = count(1)

    def __init__(self, profile, key, *, delay=2.0):
        self.profile = copy.deepcopy(profile)
        self.api_key = key
        self.delay = delay

    def stream(self, request):
        from model_runtime.provider import TextDelta
        kind = "analysis" if request.response_contract.name == "analysis" else "generation"
        print(f"DEMO_MODEL_CALL {next(self.calls)} {kind}", file=sys.stderr, flush=True)
        time.sleep(self.delay)
        if kind == "analysis":
            output = {"summary": "演示：启动按钮置位运行保持，停止按钮复位，保护输入禁止电机输出。", "approaches": [], "missing_info": [],
                      "suggested_io": {"X": {"X0": "启动", "X1": "停止", "X2": "保护输入"}, "Y": {"Y0": "电机"}, "M": {"M0": "运行保持"}}, "assumptions": []}
        else:
            output = demo_ladder()
            if any("验收越界" in str(getattr(message, "content", "")) for message in request.messages):
                # Deliberately change N0003, so a N0001-only scope must reject it.
                output["rungs"][2]["branches"][0]["outputs"][0]["address"] = "Y1"
                output["device_comments"]["Y1"] = output["device_comments"].pop("Y0")
        yield TextDelta(json.dumps(output, ensure_ascii=False))


class DemoIntegrationDisabled(ValueError):
    """A labelled refusal from the acceptance fixture, not a product failure."""


@contextmanager
def isolated_demo_execution():
    """Disable host integrations even when the host already has credentials/devices."""
    import application.execution
    import application.mcp_integrations as mcp

    class DemoExecution:
        def __init__(self, *_args, **_kwargs):
            pass

        def submit_read(self, *_args, **_kwargs):
            raise ValueError("离线演示不连接 GX、Simulator 或真实 PLC。")

        submit_approved = submit_read

        def close(self, **_kwargs):
            pass

    def mcp_status(project_id, service_url):
        # The production panel displays API errors, but not arbitrary status
        # messages; a visible refusal avoids presenting a fake registration.
        raise DemoIntegrationDisabled("离线演示不注册 MCP，不读取或修改用户 Codex 配置、服务绑定及凭据。")

    def reject_mcp(*_args, **_kwargs):
        raise mcp.MCPIntegrationError("离线演示不注册 MCP，也不测试真实接入；用户配置、服务绑定及凭据均未访问。")

    with ExitStack() as stack:
        stack.enter_context(patch.object(application.execution, "GXExecutionCoordinator", DemoExecution))
        stack.enter_context(patch.object(application.execution, "read_environment", lambda: {
            "status": "unavailable", "passed": False, "desktop_execution_required": True, "gateway_started": False,
            "evidence": {"reason": "Isolated browser fixture; desktop execution disabled"}}))
        stack.enter_context(patch.object(mcp, "status", mcp_status))
        stack.enter_context(patch.object(mcp, "test_connection", reject_mcp))
        stack.enter_context(patch.object(mcp, "connect_codex", reject_mcp))
        yield


class DemoHardwareReader:
    def availability(self):
        return {"available": False, "backend": "disabled_demo",
                "message": "离线演示禁止真实 PLC 读取；不会使用本机已配置的适配器或逻辑站。"}

    def fingerprint(self):
        from gxworks2.hardware_read import HardwareError
        raise HardwareError(self.availability()["message"])

    def read_once(self, *_args, **_kwargs):
        self.fingerprint()


def create_demo_fixture(root, settings):
    from application.hardware import HardwareService
    from application.workbench import WorkbenchService
    from plc.core import PLCCore
    from plc.ir import build_plc_ir
    from plc.semantics import normalize_semantic_requirements
    from storage.session import SessionStore
    from simulator import InMemoryTestBackend, SimulatorRegressionService

    root = Path(root)
    store = SessionStore(base_dir=root / "workspace", legacy_dir=root / "empty-legacy")
    project = store.create_project("离线演示 · 电机启停控制", plc_model="FX3U")
    spec = {"summary": "启动置位 M0，停止复位 M0，保护输入禁止 Y0。", "io_table": [], "parameters": [],
            "execution_semantics": [{"id": "SEM001", "semantic": "LEVEL", "devices": ["X1", "M0", "Y0"],
                                     "evidence": "停止输入 X1 动作后，运行保持 M0 和电机 Y0 应关闭。"}]}
    store.set_confirmed_spec(project["id"], spec)
    program = build_plc_ir(demo_ladder(), plc_model="FX3U", revision=1,
                           confirmed_spec=store.get_project(project["id"])["confirmed_spec"],
                           semantic_requirements=normalize_semantic_requirements(spec["execution_semantics"]))
    version_id, output_dir = store.prepare_version(project["id"])
    built = PLCCore().compile_project(program, output_dir)
    store.complete_version(project["id"], version_id, {**store._ir_metadata(program), "target_mode": "ladder", "plc_model": "FX3U",
        "summary": "仅供离线页面验收；停止故障来自内存夹具，不代表本程序实际行为。", "artifacts": built["artifacts"],
        "confirmed_spec_snapshot": store.get_project(project["id"])["confirmed_spec"],
        "validation": {"status": "passed", "messages": ["离线样例的确定性结构校验通过；未运行 GX 或 PLC。"]}})
    report = store.create_report(project["id"], {"report_id": "review-demo", "report_type": "program_review", "base_version_id": version_id,
        "status": "local_only", "summary": "离线验收夹具：模拟停止后输出保持故障，仅用于测试问题定位与波形回放。",
        "findings": [{"finding_id": "stop", "severity": "warning", "title": "离线夹具：停止后输出仍保持",
            "rung_ids": [2, 3], "addresses": ["X1", "M0", "Y0"],
            "message": "内存夹具故意忽略停止输入，供问题卡片与失败测试跳转验收；不是对 PLC 程序的诊断结论。",
            "evidence": ["内存后端在 300 ms 写入 X1=1 后，仍观测到 Y0=1。"],
            "suggestion": "打开关联测试，跳到 stop 断言并核对 X1、M0、Y0 波形。"}]})
    suite = {"name": "离线夹具 · 停止行为复现", "plc_model": "FX3U", "tests": [{
        "name": "demo_motor_stop", "description": "内存夹具故意忽略停止输入；仅验收步骤、证据与回放界面。",
        "initial": {"X0": 0, "X1": 0, "X2": 0, "M0": 0}, "sample_ms": 20, "timeout_ms": 600,
        "trace_devices": ["X0", "X1", "X2", "M0", "Y0"],
        "steps": [{"id": "start", "at_ms": 100, "set": {"X0": 1}, "expect": {"Y0": 1}},
                  {"id": "release", "at_ms": 200, "set": {"X0": 0}, "expect": {"Y0": 1}},
                  {"id": "stop", "at_ms": 300, "set": {"X1": 1}, "expect": {"Y0": 0}}],
        "metadata": {"workbench": {"schema_version": 1, "project_id": project["id"], "version_id": version_id,
            "requirement_ids": ["SEM001"], "issue_ids": ["review-demo:stop"], "source_plan_id": None}}}]}
    plan = store.save_simulator_test_plan(project["id"], version_id, suite, source="offline_demo")

    def faulty_memory_logic(backend, values):
        # The backend is a test double, not a PLC instruction interpreter.
        if values.get("X0") == 1:
            backend.values["M0"] = 1
        backend.values["Y0"] = int(bool(backend.values.get("M0")) and not backend.values.get("X2"))

    run = SimulatorRegressionService(store, backend=InMemoryTestBackend(on_write=faulty_memory_logic)).run_version_suite(
        project["id"], version_id, plan["suite"])
    service = WorkbenchService(store.base_dir, root / "state", settings=settings)
    service.hardware = HardwareService(service, reader=DemoHardwareReader())
    return service, {"project_id": project["id"], "version_id": version_id, "report_id": report["report_id"],
                     "plan_id": plan["binding"]["plan_id"], "run_id": run["record"]["run_id"]}


@contextmanager
def isolated_demo_app(directory, *, port=8765, model_delay=2.0):
    from fastapi.responses import JSONResponse
    from integrations.web.app import create_app

    provider = lambda profile, key: DemoProvider(profile, key, delay=model_delay)
    with isolated_demo_settings(directory, provider) as settings, isolated_demo_execution():
        service, fixture = create_demo_fixture(directory, settings)
        original_run_job = service._run_job
        def run_job(*args, **kwargs):
            try:
                return original_run_job(*args, **kwargs)
            except Exception as error:
                _demo_exception_diagnostic(error)
                raise
        service._run_job = run_job
        origin = f"http://127.0.0.1:{port}"
        app = create_app(service.store.base_dir, state_dir=service.state_dir, service=service, origin=origin,
                         operator_token="isolated-browser-acceptance", agent_token="isolated-demo-agent")
        @app.exception_handler(DemoIntegrationDisabled)
        async def demo_integration_disabled(_request, error):
            return JSONResponse({"error": {"code": "disabled_demo", "message": str(error)}}, status_code=400)
        try:
            yield app, fixture
        finally:
            service.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model-delay", type=float, default=2.0, help="Offline delay in seconds for refresh/reconnect acceptance (0–10)")
    args = parser.parse_args()
    if not 0 <= args.model_delay <= 10:
        parser.error("--model-delay must be between 0 and 10 seconds")
    import uvicorn

    with tempfile.TemporaryDirectory(prefix="gx-web-browser-qa-") as directory, \
            isolated_demo_app(directory, port=args.port, model_delay=args.model_delay) as (app, fixture):
        origin = f"http://127.0.0.1:{args.port}"
        print(f"DEMO {origin}/#token=isolated-browser-acceptance", flush=True)
        print("DEMO_FIXTURE " + json.dumps(fixture, ensure_ascii=False), flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
