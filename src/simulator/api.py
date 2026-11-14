# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""High-level Simulator API; low-level writes are not Agent tools."""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from .gateway import DEFAULT_GATEWAY_URL, GXSimulatorGatewayClient, detect_simulator_environment
from .runner import PLCTestRunner


def _backend_for_model(backend, plc_model):
    selected = backend if backend is not None else GXSimulatorGatewayClient(
        os.environ.get("GX_SIMULATOR_GATEWAY_URL", DEFAULT_GATEWAY_URL),
        token=os.environ.get("GX_SIMULATOR_GATEWAY_TOKEN", ""), plc_model=plc_model)
    if isinstance(selected, GXSimulatorGatewayClient):
        from plc.device_policy import native_simulator_profile
        target = native_simulator_profile(plc_model)
        if selected.connected and native_simulator_profile(selected.plc_model)['cpu_menu'] != target['cpu_menu']:
            raise ValueError('已连接模拟器型号与测试套件型号不一致')
        selected.plc_model = plc_model
    return selected


def simulator_status():
    evidence = detect_simulator_environment()
    client = GXSimulatorGatewayClient(
        os.environ.get("GX_SIMULATOR_GATEWAY_URL", DEFAULT_GATEWAY_URL),
        token=os.environ.get("GX_SIMULATOR_GATEWAY_TOKEN", ""),
    )
    try:
        evidence["gateway"] = client.health()
        evidence["gateway_reachable"] = True
    except Exception as error:
        evidence["gateway"] = {"error": str(error)}
        evidence["gateway_reachable"] = False
    evidence["ready"] = bool(
        evidence["gateway_reachable"]
        and evidence["gateway"].get("simulator_only")
        and evidence["gateway"].get("mx_component_available")
    )
    return evidence


def run_test_case(
    test_case: Mapping[str, Any],
    *,
    backend=None,
    plc_model="FX3U",
    progress=None,
    reset_devices=(),
):
    selected = _backend_for_model(backend, plc_model)
    return PLCTestRunner(
        selected,
        progress=progress,
        reset_devices=reset_devices,
    ).run(
        test_case,
        plc_model=plc_model,
    )


def run_regression_suite(
    suite: Mapping[str, Any],
    *,
    backend=None,
    plc_model="FX3U",
    progress=None,
    reset_devices=(),
):
    selected = _backend_for_model(backend, plc_model)
    return PLCTestRunner(
        selected,
        progress=progress,
        reset_devices=reset_devices,
    ).run_suite(
        suite,
        plc_model=plc_model,
    )


__all__ = ["run_regression_suite", "run_test_case", "simulator_status"]
