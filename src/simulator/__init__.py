"""GX Simulator2 integration and deterministic PLC test execution."""

from .api import run_regression_suite, run_test_case, simulator_status
from .backends import FaultInjectingBackend, InMemoryTestBackend
from .gateway import (
    GXSimulatorGatewayClient,
    GatewayOperationError,
    GatewayProtocolError,
    GatewayUnavailableError,
)
from .models import TestCaseValidationError, normalize_test_case, normalize_test_suite
from .planning import (
    SimulatorTestPlanError,
    build_test_generation_context,
    normalize_generated_test_suite,
)
from .runner import PLCTestRunner
from .service import SimulatorRegressionService
from .workflow import SimulatorVersionWorkflowService, SimulatorWorkflowError

__all__ = [
    "FaultInjectingBackend",
    "GXSimulatorGatewayClient",
    "GatewayOperationError",
    "GatewayProtocolError",
    "GatewayUnavailableError",
    "InMemoryTestBackend",
    "PLCTestRunner",
    "SimulatorRegressionService",
    "SimulatorGatewayRuntime",
    "SimulatorRuntimeError",
    "SimulatorTestPlanError",
    "SimulatorVersionWorkflowService",
    "SimulatorWorkflowError",
    "TestCaseValidationError",
    "normalize_test_case",
    "normalize_test_suite",
    "normalize_generated_test_suite",
    "build_test_generation_context",
    "get_simulator_gateway_runtime",
    "run_regression_suite",
    "run_test_case",
    "simulator_status",
]


def __getattr__(name):
    # Importing schemas or offline services must not initialize desktop execution.
    if name not in {"SimulatorGatewayRuntime", "SimulatorRuntimeError", "get_simulator_gateway_runtime"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import runtime
    value = getattr(runtime, name)
    globals()[name] = value
    return value
