"""Lazy lifecycle manager for the local GX Simulator2 HTTP gateway.

Nothing in this module starts during application import.  The external x86
gateway is created only after a user explicitly starts a simulator test.
"""

from __future__ import annotations

import atexit
import os
import secrets
import socket
import subprocess
import time
import urllib.parse
import weakref
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from .gateway import (
    DEFAULT_GATEWAY_URL,
    GXSimulatorGatewayClient,
    GatewayOperationError,
    gateway_compatibility,
)


class SimulatorRuntimeError(RuntimeError):
    pass


def _loopback_listener_present(base_url):
    """Observe a numeric IPv4 listener without a slow refused TCP connect.

    Unknown or unsupported inventory falls back to the authenticated health
    request. A busy endpoint is never treated as absent or terminated.
    """
    if os.name != 'nt':
        return None
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.hostname != '127.0.0.1':
        return None
    import ctypes
    from ctypes import wintypes

    class TcpRow(ctypes.Structure):
        _fields_ = [('state', wintypes.DWORD), ('local_address', wintypes.DWORD),
                   ('local_port', wintypes.DWORD), ('remote_address', wintypes.DWORD),
                   ('remote_port', wintypes.DWORD)]

    try:
        api = ctypes.WinDLL('iphlpapi').GetTcpTable
        api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
        api.restype = wintypes.DWORD
        size = wintypes.DWORD()
        result = api(None, ctypes.byref(size), False)
        if result != 122:  # ERROR_INSUFFICIENT_BUFFER
            return None
        for _ in range(3):
            buffer = ctypes.create_string_buffer(size.value)
            result = api(buffer, ctypes.byref(size), False)
            if result == 122:  # Connections can change while the table is read.
                continue
            if result != 0 or size.value < ctypes.sizeof(wintypes.DWORD):
                return None
            count = wintypes.DWORD.from_buffer(buffer).value
            offset = ctypes.sizeof(wintypes.DWORD)
            if count > (len(buffer) - offset) // ctypes.sizeof(TcpRow):
                return None
            rows = ctypes.cast(ctypes.addressof(buffer) + offset, ctypes.POINTER(TcpRow))
            return any(row.state == 2  # MIB_TCP_STATE_LISTEN
                       and socket.ntohs(row.local_port & 0xffff) == (parsed.port or 80)
                       and row.local_address in (0, socket.htonl(0x7f000001))
                       for row in (rows[index] for index in range(count)))
    except (OSError, AttributeError):
        return None
    return None


def _gateway_candidates():
    configured = os.environ.get("GX_SIMULATOR_GATEWAY_EXE", "").strip()
    if configured:
        yield Path(configured).expanduser()
    # Prefer the component shipped with the running release.  LOCALAPPDATA is
    # retained only as a compatibility fallback for older installations; it
    # must not shadow a newer, already-verified bundled gateway.
    executable_dir = Path(os.path.abspath(os.path.dirname(os.sys.executable)))
    yield executable_dir / "simulator-gateway" / "PlcAi.GxSimulator2Gateway.exe"
    yield executable_dir / "PlcAi.GxSimulator2Gateway.exe"
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        yield (
            Path(local_app_data)
            / "PLC AI Studio"
            / "simulator-gateway"
            / "PlcAi.GxSimulator2Gateway.exe"
        )


class SimulatorGatewayRuntime:
    """Own at most one gateway process and expose a configured client."""

    def __init__(self, *, base_url: Optional[str] = None, executable=None, plc_model="FX3U"):
        from plc.device_policy import native_simulator_profile
        self.plc_model = str(plc_model)
        native_simulator_profile(self.plc_model)
        self.base_url = str(
            base_url
            or os.environ.get("GX_SIMULATOR_GATEWAY_URL")
            or DEFAULT_GATEWAY_URL
        ).rstrip("/")
        self.executable = Path(executable).expanduser() if executable else None
        self._process = None
        self._token = ""
        self._started_executable = None
        self._isolation = None
        self._clients = []

    def find_executable(self) -> Optional[Path]:
        candidates = [self.executable] if self.executable else list(_gateway_candidates())
        for candidate in candidates:
            if candidate and candidate.is_file():
                return candidate.resolve()
        return None

    def _configured_token(self) -> str:
        return self._token or os.environ.get("GX_SIMULATOR_GATEWAY_TOKEN", "").strip()

    def client(self, *, timeout=5.0, reset_timeout=15.0) -> GXSimulatorGatewayClient:
        self._clients = [reference for reference in self._clients if reference() is not None]
        client = GXSimulatorGatewayClient(
            self.base_url,
            token=self._configured_token(),
            timeout=float(timeout),
            reset_timeout=float(reset_timeout),
            plc_model=self.plc_model,
        )
        self._clients.append(weakref.ref(client))
        return client

    def _sync_clients(self) -> None:
        alive = []
        for reference in self._clients:
            client = reference()
            if client is None:
                continue
            client.base_url = self.base_url
            client.token = self._configured_token()
            alive.append(reference)
        self._clients = alive

    @staticmethod
    def _free_loopback_url() -> str:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = int(listener.getsockname()[1])
        return f"http://127.0.0.1:{port}"

    def _isolate_endpoint(self, reason: str) -> None:
        previous = self.base_url
        self.base_url = self._free_loopback_url()
        self._isolation = {
            "from": previous,
            "to": self.base_url,
            "reason": str(reason or "gateway endpoint conflict"),
        }
        os.environ["GX_SIMULATOR_GATEWAY_URL"] = self.base_url
        self._sync_clients()

    def _annotate_health(self, health: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(health)
        compatible, reason = gateway_compatibility(result)
        result["protocol_compatible"] = compatible
        result["gateway_url"] = self.base_url
        if reason:
            result["protocol_error"] = reason
        if self._started_executable is not None:
            result["gateway_executable"] = str(self._started_executable)
        if self._isolation:
            result["endpoint_isolation"] = dict(self._isolation)
        return result

    def health(self) -> Optional[Dict[str, Any]]:
        if _loopback_listener_present(self.base_url) is False:
            return None
        try:
            result = self.client(timeout=1.0).health()
        except GatewayOperationError as error:
            raise SimulatorRuntimeError(
                "本机仿真端口已有 HTTP 服务响应，但不是可用的 PLC AI Simulator2 网关："
                + str(error)
            ) from error
        except Exception:
            return None
        if result.get("service") != "plc-ai-gx-simulator2-gateway":
            raise SimulatorRuntimeError(
                "本机仿真端口已被其他程序占用，未连接到 PLC AI Simulator2 网关。"
            )
        if result.get("simulator_only") is not True:
            raise SimulatorRuntimeError("仿真网关没有声明 GX Simulator2 专用路由。")
        return result

    def ensure_gateway(self, *, timeout=6.0) -> Dict[str, Any]:
        """Return gateway health, starting the bundled process only if absent."""

        try:
            existing = self.health()
        except SimulatorRuntimeError as error:
            # Never terminate an unknown process that owns the configured
            # endpoint. Start our authenticated bundled gateway on an isolated
            # loopback port instead.
            self._isolate_endpoint(str(error))
            existing = None
        if existing is not None:
            compatible, reason = gateway_compatibility(existing)
            if compatible and self._configured_token():
                return self._annotate_health(existing)
            owned = self._process is not None and self._process.poll() is None
            if owned:
                self.close()
                if compatible:
                    reason = "当前程序持有的网关访问令牌已失效。"
            else:
                self._isolate_endpoint(
                    reason
                    or "检测到无法认证的既有网关，已切换到隔离端口。"
                )

        executable = self.find_executable()
        if executable is None:
            raise SimulatorRuntimeError(
                "未找到 PLC AI GX Simulator2 网关。请先安装或构建仿真网关。"
            )
        token = os.environ.get("GX_SIMULATOR_GATEWAY_TOKEN", "").strip()
        if len(token) < 16:
            token = secrets.token_urlsafe(32)
        self._token = token
        os.environ["GX_SIMULATOR_GATEWAY_TOKEN"] = token
        os.environ["GX_SIMULATOR_GATEWAY_URL"] = self.base_url
        self._sync_clients()
        environment = os.environ.copy()
        environment["GX_SIMULATOR_GATEWAY_TOKEN"] = token
        environment["GX_SIMULATOR_GATEWAY_URL"] = self.base_url
        # The gateway listens on a numeric port, while callers configure the
        # client with a loopback URL.  Keep both sides on the same endpoint;
        # otherwise an isolated/non-default test URL starts the gateway on its
        # default port and the client waits forever on a different one.
        parsed_base_url = urllib.parse.urlsplit(self.base_url)
        environment["GX_SIMULATOR_GATEWAY_PORT"] = str(parsed_base_url.port or 80)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._started_executable = executable
            self._process = subprocess.Popen(
                [str(executable)],
                cwd=str(executable.parent),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except OSError as error:
            raise SimulatorRuntimeError(f"无法启动 GX Simulator2 网关：{error}") from error

        deadline = time.monotonic() + max(0.5, float(timeout))
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise SimulatorRuntimeError(
                    f"GX Simulator2 网关启动后立即退出（代码 {self._process.returncode}）。"
                )
            health = self.health()
            if health is not None:
                compatible, reason = gateway_compatibility(health)
                if not compatible:
                    self.close()
                    raise SimulatorRuntimeError(
                        reason + " 请使用当前软件随附的 simulator-gateway。"
                    )
                return self._annotate_health(health)
            time.sleep(0.1)
        self.close()
        raise SimulatorRuntimeError("等待 GX Simulator2 网关启动超时。")

    @contextmanager
    def startup_probe(self):
        """Share one attested MX connection during a controlled startup only."""
        connection = {}
        try:
            yield lambda **kwargs: self.probe_simulator(_connection=connection, **kwargs)
        finally:
            self._close_probe_connection(connection)

    @staticmethod
    def _close_probe_connection(connection):
        client = connection.pop('client', None)
        connection.clear()
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    def probe_simulator(self, *, timeout=2.0, _connection=None, read_run=True) -> Dict[str, Any]:
        """Prove the selected simulator CPU and its source-defined RUN state."""
        from plc.device_policy import (native_simulator_read_plan, simulator_run_monitor,
                                       simulator_cpu_is_running)

        connection = {} if _connection is None else _connection

        health = self.ensure_gateway()
        if not health.get("mx_component_available"):
            return {
                "ready": False,
                "health": health,
                "error": "未检测到与网关位数匹配的 MX Component ActProgType。",
            }
        from simulator.gateway import detect_simulator_environment
        environment = detect_simulator_environment()
        cpu_processes = [name for name in environment.get('simulator_processes', [])
                         if 'simmanager' not in str(name).casefold()]
        if not cpu_processes:
            # The manager window is not an executing simulator CPU. In this
            # state Open can block in the vendor connection setup; an
            # observational probe must not attempt to start or configure it.
            self._close_probe_connection(connection)
            return dict(ready=False, connected=False, cpu_run=False, health=health,
                        environment=environment, error='尚未启动仿真 CPU；未调用 MX Component Open。')
        try:
            client = connection.get('client')
            if client is None:
                client = self.client(timeout=timeout)
                connection['client'] = client
                connection['details'] = client.connect()
            details = connection['details']
            if not read_run:
                return dict(ready=False, connected=True, cpu_run=None, health=health,
                            connection=details, error='等待原生程序传送完成后检查 RUN。')
            monitor, _expected = simulator_run_monitor(self.plc_model)
            binding = details.get('target_binding')
            if binding:
                run_value = client.read_bound(
                    native_simulator_read_plan([monitor], self.plc_model), binding).get(monitor)
            else:
                run_value = client.read_many([monitor]).get(monitor)
            cpu_run = simulator_cpu_is_running(run_value, self.plc_model)
            return {
                "ready": cpu_run,
                "connected": True,
                "cpu_run": cpu_run,
                "run_monitor": run_value,
                "run_monitor_device": monitor,
                "health": health,
                "connection": details,
                "error": "" if cpu_run else "GX Simulator2 已连接，但选定 CPU 尚未进入 RUN。",
            }
        except Exception as error:
            self._close_probe_connection(connection)
            return {
                "ready": False,
                "connected": False,
                "cpu_run": False,
                "health": health,
                "error": str(error),
            }
        finally:
            # Older FX-only gateways cannot attest reuse across observations.
            # Public probes retain their original open/read/close lifetime.
            if _connection is None or not connection.get('details', {}).get('target_binding'):
                self._close_probe_connection(connection)

    def disconnect(self) -> None:
        try:
            client = self.client(timeout=1.0)
            health = client.health()
            if health.get("connected"):
                client.connected = True
                client.disconnect()
        except Exception:
            pass

    def close(self) -> None:
        """Stop only the gateway process created by this application."""

        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            self.client(timeout=1.0)._request("POST", "/shutdown", {})
            process.wait(timeout=2.0)
        except Exception:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except Exception:
                pass


_runtime = None


def get_simulator_gateway_runtime() -> SimulatorGatewayRuntime:
    global _runtime
    if _runtime is None:
        _runtime = SimulatorGatewayRuntime()
        atexit.register(_runtime.close)
    return _runtime


__all__ = [
    "SimulatorGatewayRuntime",
    "SimulatorRuntimeError",
    "get_simulator_gateway_runtime",
]
