"""Version-bound high-level simulator service."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from .api import run_regression_suite
from .models import normalize_test_suite
from .verification import execution_binding, require_matching_binding


class SimulatorRegressionService:
    """Run a validated suite and persist evidence against one program version."""

    def __init__(self, store, backend=None, preparer=None):
        self.store = store
        self.backend = backend
        self.preparer = preparer

    def run_version_suite(
        self,
        project_id: str,
        version_id: str,
        suite: Mapping[str, Any],
        *,
        progress: Optional[Callable[[str, str], None]] = None,
        test_progress: Optional[Callable[[Mapping[str, Any]], None]] = None,
        expected_binding=None,
    ):
        program = self.store.load_program_ir(project_id, version_id)
        if not isinstance(program, Mapping):
            raise ValueError("当前版本没有可仿真的 PLC IR。")
        plc_model = str(program.get("plc", {}).get("cpu") or "FX3U").upper()
        normalized = normalize_test_suite(suite, plc_model=plc_model)
        if normalized["plc_model"] != plc_model:
            raise ValueError("测试套件 PLC 型号与当前程序不一致。")
        binding = execution_binding(project_id, version_id, program, normalized)
        if expected_binding is not None:
            require_matching_binding(binding, expected_binding)

        static_errors = int(
            ((program.get("analysis") or {}).get("counts") or {}).get("error", 0)
        )
        if static_errors:
            raise ValueError("当前程序仍有静态分析错误，不能进入自动仿真。")

        preparation = None
        if self.preparer is not None:
            preparation = (
                self.preparer.prepare(progress=progress)
                if progress is not None
                else self.preparer.prepare()
            )
            if not preparation.success:
                result = {
                    "schema_version": 1,
                    "name": normalized["name"],
                    "plc_model": plc_model,
                    "status": "unavailable",
                    "passed": False,
                    "counts": {
                        "passed": 0,
                        "failed": 0,
                        "error": 0,
                        "unavailable": 1,
                    },
                    "test_count": len(normalized["tests"]),
                    "attempted_count": 0,
                    "executed_count": 0,
                    "not_executed_count": len(normalized["tests"]),
                    "backend_kinds": [],
                    "results": [],
                    "error": preparation.message,
                }
                record = self.store.save_simulator_run(
                    project_id,
                    version_id,
                    normalized,
                    result,
                    execution_snapshot=binding,
                )
                return {
                    "record": record,
                    "result": result,
                    "preparation": preparation.to_dict(),
                    "verification": record["verification"],
                }

        if progress is not None:
            progress(
                "execute_tests",
                f"正在执行 {len(normalized['tests'])} 项仿真测试…",
            )
        reset_devices = []
        for address, metadata in (program.get("devices") or {}).items():
            if not isinstance(metadata, Mapping):
                continue
            kind = str(metadata.get("kind") or "").upper()
            if kind not in {"Y", "M", "D", "T", "C", "S"}:
                continue
            if not (metadata.get("written_by") or []):
                continue
            normalized_address = str(address or "").upper()
            suffix = normalized_address[len(kind) :]
            if kind in {"M", "D"} and suffix.isdigit() and int(suffix) >= 8000:
                continue
            reset_devices.append(normalized_address)
        result = run_regression_suite(
            normalized,
            backend=self.backend,
            plc_model=plc_model,
            progress=test_progress,
            reset_devices=reset_devices,
        )
        if progress is not None:
            progress("save_evidence", "正在保存仿真测试轨迹…")
        record = self.store.save_simulator_run(
            project_id,
            version_id,
            normalized,
            result,
            execution_snapshot=binding,
        )
        return {
            "record": record,
            "result": result,
            "verification": record["verification"],
            "preparation": (
                preparation.to_dict()
                if preparation is not None and hasattr(preparation, "to_dict")
                else preparation
            ),
        }


__all__ = ["SimulatorRegressionService"]
