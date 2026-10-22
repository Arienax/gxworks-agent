"""Select the current Workspace helper and build it lazily in source runs.

Release packages use their own helper. Source runs compile the current checkout
on first use, then reuse that output within this process while the build inputs
remain unchanged. An unrelated executable in LOCALAPPDATA is never adopted as
the default source runtime.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


_EXECUTABLE = "PlcAi.WorkspaceSourceSave.exe"
_DIRECTORY = "workspace-adapter"
_BUILD_INPUTS = (
    "tools/build_workspace_adapter.ps1",
    "native_adapters/WorkspaceSourceSave.cs",
    "native_adapters/WorkspaceValidation.cs",
    "native_adapters/WorkspaceTasks.cs",
    "native_adapters/WorkspaceSfcGraph.cs",
    "native_adapters/NativeRequest.cs",
    "native_adapters/WorkspaceBusinessProtocol.cs",
    "native_adapters/WorkspaceSourceSave.manifest",
)
_BUILD_LOCK = threading.RLock()
# Holding the TemporaryDirectory keeps each output alive for its native calls;
# its normal finalizer removes it when this Python process exits.
_BUILDS = {}


class WorkspaceRuntimeError(RuntimeError):
    def __init__(self, message, code, details):
        super().__init__(message)
        self.code = code
        self.details = dict(details)


def _source_root():
    return Path(__file__).resolve().parents[2]


def _windows_host():
    return os.name == "nt"


def _failure(message, code, reason, *, build_script=None, **details):
    context = {"stage": "runtime_setup", "gx_compile": "not_run", "reason": reason,
               **details}
    if build_script is not None:
        context["build_script"] = str(build_script)
    return WorkspaceRuntimeError(message, code, context)


def _input_state(root):
    state, missing = [], []
    for relative in _BUILD_INPUTS:
        path = root / relative
        try:
            if not path.is_file():
                missing.append(relative)
                continue
            info = path.stat()
        except OSError:
            missing.append(relative)
            continue
        state.append((str(path.resolve()), info.st_mtime_ns, info.st_size))
    if missing:
        raise _failure(
            "当前源码缺少 Workspace 组件的构建文件；请恢复源码，或显式指定运行组件。",
            "WORKSPACE_ADAPTER_UNAVAILABLE", "source_unavailable",
            build_script=root / _BUILD_INPUTS[0], missing=missing)
    return tuple(state)


def _output_tail(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value or "")[-2000:]


def _build_current_source(root):
    with _BUILD_LOCK:
        state = _input_state(root)
        cached = _BUILDS.get(state)
        if cached is not None:
            directory, executable = cached
            if executable.is_file() and executable.stat().st_size > 0:
                return executable
            del _BUILDS[state]
            directory.cleanup()
        script = root / _BUILD_INPUTS[0]
        try:
            directory = tempfile.TemporaryDirectory(prefix="gxworks-workspace-",
                                                    ignore_cleanup_errors=True)
        except OSError as error:
            raise _failure(
                "无法创建 Workspace 组件的临时构建目录。",
                "WORKSPACE_ADAPTER_UNAVAILABLE", "build_directory_unavailable",
                build_script=script) from error
        executable = Path(directory.name) / _EXECUTABLE
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(script), "-OutputDirectory", directory.name]
        try:
            try:
                result = subprocess.run(
                    command, shell=False, cwd=str(root), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=90,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as error:
                raise _failure(
                    "Workspace 组件构建超时，尚未启动 GX Works2 工程操作。",
                    "WORKSPACE_ADAPTER_BUILD_FAILED", "build_timeout", build_script=script,
                    stdout_tail=_output_tail(error.stdout), stderr_tail=_output_tail(error.stderr)) from error
            except OSError as error:
                raise _failure(
                    "无法启动 Workspace 组件构建，请检查本机 PowerShell 和 .NET Framework。",
                    "WORKSPACE_ADAPTER_BUILD_FAILED", "build_start_failed", build_script=script,
                    error_type=type(error).__name__) from error
            if result.returncode:
                raise _failure(
                    "Workspace 组件构建失败，尚未启动 GX Works2 工程操作。",
                    "WORKSPACE_ADAPTER_BUILD_FAILED", "build_failed", build_script=script,
                    returncode=result.returncode, stdout_tail=_output_tail(result.stdout),
                    stderr_tail=_output_tail(result.stderr))
            if not executable.is_file() or executable.stat().st_size == 0:
                raise _failure(
                    "Workspace 组件构建没有生成可用的程序文件。",
                    "WORKSPACE_ADAPTER_BUILD_FAILED", "build_output_missing", build_script=script)
        except BaseException:
            directory.cleanup()
            raise
        _BUILDS[state] = (directory, executable)
        return executable


def resolve_workspace_executable(executable=None, *, build=True) -> Path:
    """Resolve explicit, packaged or current-source components without fallback.

    ``build=False`` lets callers report an unavailable host/GX installation
    without starting a compiler. Explicit paths are never built or replaced.
    """
    configured = executable or os.environ.get("GX_WORKSPACE_ADAPTER_EXE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / _DIRECTORY / _EXECUTABLE
    if not build or not _windows_host():
        return (Path(os.environ.get("LOCALAPPDATA", "")) / "PLC AI Studio"
                / _DIRECTORY / _EXECUTABLE).expanduser().resolve()
    return _build_current_source(_source_root())
