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

    def __init__(self, *, base_url: Optional[str] = None, executable=None):
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

    def probe_simulator(self, *, timeout=2.0) -> Dict[str, Any]:
        """Prove the simulator route is connected and the FX CPU is in RUN."""

        health = self.ensure_gateway()
        if not health.get("mx_component_available"):
            return {
                "ready": False,
                "health": health,
                "error": "未检测到与网关位数匹配的 MX Component ActProgType。",
            }
        client = self.client(timeout=timeout)
        try:
            details = client.connect()
            run_value = client.read_many(["M8000"]).get("M8000")
            cpu_run = int(run_value or 0) == 1
            return {
                "ready": cpu_run,
                "connected": True,
                "cpu_run": cpu_run,
                "run_monitor": run_value,
                "health": health,
                "connection": details,
                "error": "" if cpu_run else "GX Simulator2 已连接，但 FX CPU 尚未进入 RUN。",
            }
        except Exception as error:
            return {
                "ready": False,
                "connected": False,
                "cpu_run": False,
                "health": health,
                "error": str(error),
            }
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

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
