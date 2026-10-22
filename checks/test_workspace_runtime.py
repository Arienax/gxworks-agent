"""Workspace runtime selection checks; all compiler processes are simulated."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from gxworks2 import workspace_runtime as runtime


class WorkspaceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        for relative in runtime._BUILD_INPUTS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("simulated source input: " + relative, encoding="utf-8")
        self.local = Path(self.temporary.name) / "local"
        self.old = self.local / "PLC AI Studio/workspace-adapter/PlcAi.WorkspaceSourceSave.exe"
        self.old.parent.mkdir(parents=True)
        self.old.write_bytes(b"unrelated old local component")
        self.builds = {}
        self.patch(runtime, "_BUILDS", self.builds)
        self.patch(runtime, "_source_root", lambda: self.root)
        self.patch(runtime, "_windows_host", lambda: True)
        frozen = mock.patch.object(runtime.sys, "frozen", False, create=True)
        frozen.start()
        self.addCleanup(frozen.stop)
        environment = mock.patch.dict(os.environ, {
            "LOCALAPPDATA": str(self.local), "GX_WORKSPACE_ADAPTER_EXE": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.runner = self.patch(runtime.subprocess, "run", mock.Mock(side_effect=self.fake_build))
        self.addCleanup(self.clean_outputs)

    def patch(self, owner, name, value):
        patcher = mock.patch.object(owner, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def clean_outputs(self):
        for directory, _ in self.builds.values():
            directory.cleanup()
        self.builds.clear()

    def fake_build(self, command, **options):
        destination = Path(command[command.index("-OutputDirectory") + 1])
        (destination / runtime._EXECUTABLE).write_bytes(b"simulated compiler output; not executable")
        return SimpleNamespace(returncode=0, stdout="simulated build", stderr="")

    def test_source_ignores_old_local_executable_and_reuses_current_build(self):
        first = runtime.resolve_workspace_executable()
        second = runtime.resolve_workspace_executable()
        self.assertEqual(first, second)
        self.assertNotEqual(first, self.old)
        self.assertTrue(first.is_file())
        self.assertEqual(self.runner.call_count, 1)
        arguments, options = self.runner.call_args
        command, = arguments
        self.assertEqual(command[0], "powershell.exe")
        self.assertEqual(command[command.index("-File") + 1],
                         str(self.root / "tools/build_workspace_adapter.ps1"))
        self.assertFalse(options["shell"])
        self.assertEqual(options["timeout"], 90)
        self.assertEqual(options["cwd"], str(self.root))
        self.assertEqual(options["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_protocol_source_update_builds_a_new_component(self):
        first = runtime.resolve_workspace_executable()
        source = self.root / "native_adapters/WorkspaceBusinessProtocol.cs"
        before = source.stat()
        source.write_text("updated protocol source", encoding="utf-8")
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        second = runtime.resolve_workspace_executable()
        self.assertNotEqual(first, second)
        self.assertEqual(self.runner.call_count, 2)
        self.assertTrue(first.is_file())  # An earlier native call may still use it.

    def test_missing_cached_output_is_built_again(self):
        first = runtime.resolve_workspace_executable()
        first.unlink()
        second = runtime.resolve_workspace_executable()
        self.assertNotEqual(first, second)
        self.assertTrue(second.is_file())
        self.assertEqual(self.runner.call_count, 2)

    def test_explicit_argument_and_environment_are_not_replaced(self):
        explicit = Path(self.temporary.name) / "explicit.exe"
        configured = Path(self.temporary.name) / "configured.exe"
        with mock.patch.dict(os.environ, {"GX_WORKSPACE_ADAPTER_EXE": str(configured)}):
            self.assertEqual(runtime.resolve_workspace_executable(explicit), explicit)
            self.assertEqual(runtime.resolve_workspace_executable(), configured)
        self.runner.assert_not_called()

    def test_frozen_uses_current_package_even_when_its_component_is_missing(self):
        application = Path(self.temporary.name) / "release/GXWorks-Agent-Web.exe"
        bundled = application.parent / runtime._DIRECTORY / runtime._EXECUTABLE
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b"simulated bundled component")
        with mock.patch.object(sys, "frozen", True), mock.patch.object(sys, "executable", str(application)):
            self.assertEqual(runtime.resolve_workspace_executable(), bundled)
            bundled.unlink()
            self.assertEqual(runtime.resolve_workspace_executable(), bundled)
            self.assertNotEqual(runtime.resolve_workspace_executable(), self.old)
        self.runner.assert_not_called()

    def test_disabled_build_and_other_hosts_do_not_start_a_compiler(self):
        (self.root / runtime._BUILD_INPUTS[0]).unlink()
        self.assertEqual(runtime.resolve_workspace_executable(build=False), self.old)
        with mock.patch.object(runtime, "_windows_host", return_value=False):
            self.assertEqual(runtime.resolve_workspace_executable(), self.old)
        self.runner.assert_not_called()

    def test_missing_source_does_not_adopt_the_old_component(self):
        missing = "native_adapters/NativeRequest.cs"
        (self.root / missing).unlink()
        with self.assertRaises(runtime.WorkspaceRuntimeError) as caught:
            runtime.resolve_workspace_executable()
        self.assertEqual(caught.exception.code, "WORKSPACE_ADAPTER_UNAVAILABLE")
        self.assertEqual(caught.exception.details["reason"], "source_unavailable")
        self.assertEqual(caught.exception.details["missing"], [missing])
        self.assertEqual(caught.exception.details["gx_compile"], "not_run")
        self.runner.assert_not_called()

    def test_failed_build_does_not_cache_partial_output_and_can_be_retried(self):
        failed_output = []

        def fail(command, **options):
            self.fake_build(command, **options)
            failed_output.append(Path(command[-1]))
            return SimpleNamespace(returncode=1, stdout="x" * 5000, stderr="simulated compiler error")

        self.runner.side_effect = fail
        with self.assertRaises(runtime.WorkspaceRuntimeError) as caught:
            runtime.resolve_workspace_executable()
        error = caught.exception
        self.assertEqual(error.code, "WORKSPACE_ADAPTER_BUILD_FAILED")
        self.assertEqual(error.details["reason"], "build_failed")
        self.assertEqual(error.details["stage"], "runtime_setup")
        self.assertEqual(error.details["gx_compile"], "not_run")
        self.assertLessEqual(len(error.details["stdout_tail"]), 2000)
        self.assertFalse(self.builds)
        self.assertFalse(failed_output[0].exists())
        self.runner.side_effect = self.fake_build
        built = runtime.resolve_workspace_executable()
        self.assertNotEqual(built, self.old)
        self.assertEqual(self.runner.call_count, 2)

    def test_timeout_and_missing_output_report_setup_failures(self):
        self.runner.side_effect = subprocess.TimeoutExpired("powershell.exe", 90, output=b"partial")
        with self.assertRaises(runtime.WorkspaceRuntimeError) as caught:
            runtime.resolve_workspace_executable()
        self.assertEqual(caught.exception.details["reason"], "build_timeout")
        self.assertEqual(caught.exception.details["stdout_tail"], "partial")
        self.assertFalse(self.builds)
        self.runner.side_effect = None
        self.runner.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        with self.assertRaises(runtime.WorkspaceRuntimeError) as caught:
            runtime.resolve_workspace_executable()
        self.assertEqual(caught.exception.details["reason"], "build_output_missing")
        self.assertFalse(self.builds)

    def test_adapter_constructs_current_runtime_and_keeps_it_for_both_checks(self):
        from gxworks2 import workspace_adapter as adapter
        from gxw import native_write

        with mock.patch.object(adapter, "os", SimpleNamespace(name="nt")):
            saver = adapter.NativeWorkspaceSourceSave(installation=self.root)
            self.assertNotEqual(saver.executable, self.old)
            calls = []

            def validate(checker, raw, **options):
                calls.append((checker, checker.executable, raw, options))
                if options.get("_save_output"):
                    return b"saved", {"status": "simulated"}, {"status": "simulated"}, {}
                return {"status": "simulated_cold_check"}

            result = SimpleNamespace(report={})
            with mock.patch.object(adapter.NativeWorkspaceValidation, "validate", autospec=True,
                                   side_effect=validate), mock.patch.object(
                    native_write, "verify_native_compilation_save", return_value=result):
                self.assertIs(saver.compile_project(b"input", timeout=15), result)
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0][0], calls[1][0])
        self.assertEqual([call[1] for call in calls], [saver.executable, saver.executable])
        self.assertEqual([call[2] for call in calls], [b"input", b"saved"])
        self.assertTrue(calls[0][3]["_save_output"])
        self.assertTrue(calls[1][3]["inspection"]["saved_code"])
        self.assertEqual(self.runner.call_count, 1)

    def test_adapter_preserves_structured_runtime_failure(self):
        from gxworks2 import workspace_adapter as adapter

        self.runner.return_value = SimpleNamespace(returncode=1, stdout="", stderr="simulated failure")
        self.runner.side_effect = None
        with mock.patch.object(adapter, "os", SimpleNamespace(name="nt")):
            with self.assertRaises(adapter.WorkspaceAdapterError) as caught:
                adapter.NativeWorkspaceSourceSave(installation=self.root)
        payload = caught.exception.payload()
        self.assertEqual(payload["code"], "WORKSPACE_ADAPTER_BUILD_FAILED")
        self.assertEqual(payload["details"]["stage"], "runtime_setup")
        self.assertEqual(payload["details"]["gx_compile"], "not_run")
        self.assertTrue(payload["retryable"])


if __name__ == "__main__":
    unittest.main()
