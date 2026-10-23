"""Exercise the workspace wire contract without Windows or native processes."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gxworks2 import workspace_adapter  # noqa: E402
from gxworks2.workspace_protocol import WORKSPACE_PROTOCOL, workspace_business_request  # noqa: E402


_MISSING = object()


def _unexecuted_reply(version=2, *, message="incompatible_workspace_protocol"):
    return {
        "protocol_version": version,
        "status": "failed",
        "message": message,
        "native_version": "1.635.0.1",
        "cpu": "",
        "compile": "not_requested",
        "check": "not_requested",
        "validation": None,
        "creation": None,
        "calls": [],
    }


def _observed_reply():
    return {
        "protocol_version": WORKSPACE_PROTOCOL,
        "status": "observed",
        "message": "",
        "native_version": "1.635.0.1",
        "cpu": "FX3U/FX3UC",
        "compile": "completed_accepted",
        "check": "completed_accepted",
        "validation": {"events": [], "compile_status": "completed_accepted",
                       "check_status": "completed_accepted"},
        "creation": None,
        "calls": [{"operation": "Compiler.PrepareBuildData", "hresult": 0, "code": 0}],
    }


class WorkspaceProtocolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="gxw-protocol-check-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.executable = self.root / "PlcAi.WorkspaceSourceSave.exe"
        self.request = {
            "operation": "validate_project",
            "protocol_version": 1,
            "installation": str(self.root / "GX Works2"),
            "directory": str(self.root),
            "owner_token": "0123456789abcdef0123456789abcdef",
            "source": {
                "cpu": "FX3U/FX3UC",
                "codepage": 936,
                "programs": [{"name": "MAIN", "program_kind": 1, "pou_type": 0x01000000}],
            },
        }

    @contextmanager
    def _processes(self, *replies):
        processes = []
        for index, (returncode, reply) in enumerate(replies):
            process = Mock()
            process.pid = 41000 + index
            process.returncode = returncode
            process.poll.return_value = returncode
            process.communicate.return_value = (json.dumps(reply, ensure_ascii=False), "")
            processes.append(process)
        with patch.object(workspace_adapter.subprocess, "Popen", side_effect=processes) as launch, \
                patch.object(workspace_adapter, "_clean_native_temp") as cleanup:
            yield launch, cleanup, processes

    def _run(self):
        return workspace_adapter._run_native_adapter(
            self.executable, self.request, self.root, 10,
            workspace_adapter.WorkspaceValidationError)

    def _assert_protocol_error(self, error, reply, *, gx_compile, retryable):
        payload = error.payload()
        self.assertEqual(payload["code"], "WORKSPACE_PROTOCOL_MISMATCH")
        self.assertIs(payload["retryable"], retryable)
        self.assertIsInstance(payload["message"], str)
        self.assertIsInstance(payload["next_actions"], list)
        details = payload["details"]
        self.assertEqual(details["stage"], "transport")
        self.assertEqual(details["expected_protocol_version"], WORKSPACE_PROTOCOL)
        self.assertEqual(details["observed_protocol_version"], reply.get("protocol_version"))
        self.assertIs(type(details["observed_protocol_version"]), type(reply.get("protocol_version")))
        self.assertEqual(details["executable"], str(self.executable))
        self.assertEqual(details["gx_compile"], gx_compile)
        self.assertEqual(error.observation, reply)

    def test_internal_v1_becomes_wire_v3_without_changing_the_request(self):
        original = deepcopy(self.request)
        wire = workspace_business_request(self.request)
        self.assertEqual(wire, {
            "schema_version": 3,
            "action": "check_project",
            "workspace": {key: self.request[key] for key in ("installation", "directory", "owner_token")},
            "project": {"cpu": "FX3U/FX3UC", "encoding": 936},
            "change": {"programs": [{"name": "MAIN", "language": "ladder", "kind": "program"}]},
        })
        self.assertEqual(self.request, original)
        wire["change"]["programs"][0]["name"] = "OTHER"
        self.assertEqual(self.request, original)

    def test_internal_protocol_is_strictly_integer_one(self):
        for version in (0, 2, 3, True, False, 1.0, "1", None):
            with self.subTest(version=version, value_type=type(version).__name__):
                request = deepcopy(self.request)
                request["protocol_version"] = version
                with self.assertRaises(ValueError):
                    workspace_business_request(request)

    def test_python_and_csharp_agree_on_wire_v3(self):
        source = (ROOT / "native_adapters" / "WorkspaceBusinessProtocol.cs").read_text(encoding="utf-8-sig")
        match = re.search(r"\binternal\s+const\s+int\s+Version\s*=\s*(\d+)\s*;", source)
        self.assertIsNotNone(match)
        self.assertEqual(WORKSPACE_PROTOCOL, 3)
        self.assertEqual(int(match.group(1)), WORKSPACE_PROTOCOL)

    def test_current_reply_is_normalized_only_after_wire_validation(self):
        reply = _observed_reply()
        with self._processes((0, reply)) as (launch, cleanup, processes):
            returncode, observed = self._run()
        self.assertEqual(returncode, 0)
        expected = deepcopy(reply)
        expected["protocol_version"] = 1
        self.assertEqual(observed, expected)
        self.assertEqual(reply["protocol_version"], 3)
        launch.assert_called_once()
        cleanup.assert_called_once_with(processes[0].pid, self.request["owner_token"])
        processes[0].kill.assert_not_called()
        sent = json.loads(processes[0].communicate.call_args.args[0])
        self.assertEqual(sent, workspace_business_request(self.request))
        self.assertEqual(self.request["protocol_version"], 1)

    def test_old_unknown_and_malformed_versions_are_rejected_without_replay(self):
        versions = (1, 2, 0, -1, 4, 999, True, False, 3.0, "3", None, [], {}, _MISSING)
        for version in versions:
            with self.subTest(version=version, value_type=type(version).__name__):
                reply = _unexecuted_reply(version, message="The runtime rejected this request / 运行组件拒绝请求")
                if version is _MISSING:
                    reply.pop("protocol_version")
                with self._processes((1, reply)) as (launch, cleanup, processes):
                    with self.assertRaises(workspace_adapter.WorkspaceValidationError) as raised:
                        self._run()
                known_old = type(version) is int and version in (1, 2)
                self._assert_protocol_error(raised.exception, reply,
                    gx_compile="not_run" if known_old else "unverified", retryable=known_old)
                launch.assert_called_once()
                cleanup.assert_called_once_with(processes[0].pid, self.request["owner_token"])

    def test_known_old_unexecuted_reply_is_classified_without_reading_its_message(self):
        for version in (1, 2):
            for message in ("", "incompatible_workspace_protocol", "运行组件版本需要更新", "An unrelated description"):
                with self.subTest(version=version, message=message):
                    reply = _unexecuted_reply(version, message=message)
                    with self._processes((1, reply)) as (launch, _, _):
                        with self.assertRaises(workspace_adapter.WorkspaceValidationError) as raised:
                            self._run()
                    self._assert_protocol_error(raised.exception, reply, gx_compile="not_run", retryable=True)
                    launch.assert_called_once()

    def test_incomplete_or_executed_old_reply_cannot_claim_compile_did_not_run(self):
        changes = (
            ("status", "observed"),
            ("calls", [{"operation": "OpenProjectEX2", "hresult": 0, "code": 0}]),
            ("calls", None),
            ("compile", "completed_accepted"),
            ("check", "completed_rejected"),
            ("validation", {}),
            ("creation", {}),
        ) + tuple((key, _MISSING) for key in (
            "status", "calls", "compile", "check", "validation", "creation"))
        for key, value in changes:
            with self.subTest(field=key, value=value):
                reply = _unexecuted_reply()
                if value is _MISSING:
                    reply.pop(key)
                else:
                    reply[key] = value
                with self._processes((1, reply)) as (launch, _, _):
                    with self.assertRaises(workspace_adapter.WorkspaceValidationError) as raised:
                        self._run()
                self._assert_protocol_error(raised.exception, reply, gx_compile="unverified", retryable=False)
                launch.assert_called_once()

    def test_success_exit_with_old_protocol_is_unverified_and_never_replayed(self):
        reply = _unexecuted_reply()
        with self._processes((0, reply)) as (launch, _, _):
            with self.assertRaises(workspace_adapter.WorkspaceValidationError) as raised:
                self._run()
        self._assert_protocol_error(raised.exception, reply, gx_compile="unverified", retryable=False)
        launch.assert_called_once()

    def test_protocol_mismatch_takes_precedence_over_collision_retry(self):
        reply = _unexecuted_reply(message="native_temporary_directory_collision")
        with self._processes((1, reply)) as (launch, _, _):
            with self.assertRaises(workspace_adapter.WorkspaceValidationError) as raised:
                self._run()
        self._assert_protocol_error(raised.exception, reply, gx_compile="not_run", retryable=True)
        launch.assert_called_once()

    def test_current_protocol_collision_retries_with_the_same_request(self):
        collision = _unexecuted_reply(WORKSPACE_PROTOCOL, message="native_temporary_directory_collision")
        with self._processes((1, collision), (0, _observed_reply())) as (launch, cleanup, processes):
            returncode, observed = self._run()
        self.assertEqual(returncode, 0)
        self.assertEqual(observed["protocol_version"], 1)
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(cleanup.call_count, 2)
        self.assertEqual(processes[0].communicate.call_args.args[0], processes[1].communicate.call_args.args[0])

    def test_collision_retry_remains_bounded(self):
        collision = _unexecuted_reply(WORKSPACE_PROTOCOL, message="native_temporary_directory_collision")
        with self._processes(*[(1, collision)] * 3) as (launch, cleanup, _):
            returncode, observed = self._run()
        self.assertEqual(returncode, 1)
        self.assertEqual(observed["message"], "native_temporary_directory_collision")
        self.assertEqual(observed["protocol_version"], 1)
        self.assertEqual(launch.call_count, 3)
        self.assertEqual(cleanup.call_count, 3)

    def test_collision_message_after_a_native_call_does_not_retry(self):
        reply = _unexecuted_reply(WORKSPACE_PROTOCOL, message="native_temporary_directory_collision")
        reply["calls"] = [{"operation": "CoCreateInstance", "hresult": 0, "code": 0}]
        with self._processes((1, reply)) as (launch, _, _):
            returncode, observed = self._run()
        self.assertEqual(returncode, 1)
        self.assertEqual(observed["calls"], reply["calls"])
        launch.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
