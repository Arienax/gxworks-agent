"""High-level Simulator API; low-level writes are not Agent tools."""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from .gateway import DEFAULT_GATEWAY_URL, GXSimulatorGatewayClient, detect_simulator_environment
from .runner import PLCTestRunner


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
    selected = backend or GXSimulatorGatewayClient(
        os.environ.get("GX_SIMULATOR_GATEWAY_URL", DEFAULT_GATEWAY_URL),
        token=os.environ.get("GX_SIMULATOR_GATEWAY_TOKEN", ""),
    )
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
    selected = backend or GXSimulatorGatewayClient(
        os.environ.get("GX_SIMULATOR_GATEWAY_URL", DEFAULT_GATEWAY_URL),
        token=os.environ.get("GX_SIMULATOR_GATEWAY_TOKEN", ""),
    )
    return PLCTestRunner(
        selected,
        progress=progress,
        reset_devices=reset_devices,
    ).run_suite(
        suite,
        plc_model=plc_model,
    )


__all__ = ["run_regression_suite", "run_test_case", "simulator_status"]
