"""Launch an isolated DeepSeek Harness SDK process for a GXWorks job."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from model_runtime.contract import RESPONSE_CONTROLS
from model_runtime.responses_wire import encode_options

from .vendor.deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig, RunResult, Session
from .vendor.deepseek_harness.models import JsonObject, Notification


_PROVIDER = "gxworks-provider"
_BRIDGE_PLUGIN = Path(__file__).with_name("bridge.mjs")
_WINDOWS_SDK_PLUGIN = Path(__file__).with_name("windows_sdk.mjs")
_DISABLED_ROWS = (
    "session-log-deepseek", "plugin-package-inventory-deepseek", "llm-deepseek",
    "tool-fs", "tool-working-directory", "persistent-bash", "persistent-pwsh",
    "terminal-bash", "terminal-pwsh",
)
_REGISTRY_UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
_DESKTOP_CLI = Path("resources/runtime/cli/bin/dsh.cmd")
_BUNDLED_EXE = "deepseek-harness-sdk-runtime-win-x64.exe"


def _windows_desktop_bin() -> Path | None:
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, _REGISTRY_UNINSTALL, 0, winreg.KEY_READ | view) as uninstall:
                    for index in range(winreg.QueryInfoKey(uninstall)[0]):
                        try:
                            with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as app:
                                name = winreg.QueryValueEx(app, "DisplayName")[0]
                                if str(name).strip().casefold() != "deepseek harness":
                                    continue
                                location = winreg.QueryValueEx(app, "InstallLocation")[0]
                        except OSError:
                            continue
                        candidate = Path(os.path.expandvars(str(location).strip().strip('"'))) / _DESKTOP_CLI
                        if candidate.is_file():
                            return candidate
            except OSError:
                continue
    return None


def find_dsh_bin() -> str | None:
    override = os.environ.get("GXWORKS_DSH_BIN", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        raise FileNotFoundError(f"GXWORKS_DSH_BIN does not identify a DSH executable: {candidate}")
    if os.name == "nt":
        bundled_roots = [Path(sys.executable).resolve().parent / "dsh-runtime"]
        if getattr(sys, "frozen", False):
            bundled_roots.insert(0, Path(sys._MEIPASS) / "dsh-runtime")
        else:
            bundled_roots.insert(0, Path(__file__).resolve().parents[3] / ".runtime/dsh-runtime")
        for root in bundled_roots:
            candidate = root / _BUNDLED_EXE
            if candidate.is_file():
                return str(candidate)
        if getattr(sys, "frozen", False):
            raise FileNotFoundError("The packaged DeepSeek Harness runtime is missing. Reinstall GXWorks Agent.")
    executable = shutil.which("dsh")
    if executable:
        return str(Path(executable).resolve())
    desktop = _windows_desktop_bin() if os.name == "nt" else None
    return str(desktop.resolve()) if desktop is not None else None


def _desktop_launch_args(executable: Path, patch: Path) -> tuple[str, ...] | None:
    # The desktop .cmd starts a second process. Own that process directly so an
    # interrupted SDK shutdown can terminate the actual runtime on Windows.
    if os.name != "nt" or executable.suffix.lower() != ".cmd":
        return None
    if len(executable.parents) < len(_DESKTOP_CLI.parts):
        return None
    desktop = executable.parents[len(_DESKTOP_CLI.parts) - 1]
    if executable != desktop / _DESKTOP_CLI:
        return None
    electron = desktop / "DeepSeek Harness.exe"
    archive = desktop / "resources/app.asar"
    if not electron.is_file() or not archive.is_file():
        return None
    entry = archive / "dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js"
    return (str(electron), "--expose-internals", str(entry), "--profile", "sdk-minimal", "--patch", str(patch))


class DshRuntime:
    """Own a DSH process and resume sessions stored under an explicit home."""

    def __init__(
        self,
        config: Mapping[str, Any],
        bridge_url: str,
        bridge_token: str,
        home: str | Path,
        cwd: str | Path,
    ) -> None:
        self._config = dict(config)
        self._bridge_url = bridge_url
        self._bridge_token = bridge_token
        self._home = Path(home).resolve()
        self._cwd = Path(cwd).resolve()
        self._state_lock = threading.Lock()
        self._harness: DeepSeekHarness | None = None
        self._launch_directory: tempfile.TemporaryDirectory | None = None
        self._closed = False
        self._needs_durable_shutdown = False

    def _create_harness(self, directory: Path) -> DeepSeekHarness:
        config = self._config
        if config.get("adapter", "openai_compatible") != "openai_compatible":
            raise ValueError("The DSH runtime requires an OpenAI-compatible provider")
        api = str(config.get("api") or "openai-completions")
        if api not in {"openai-completions", "openai-responses"}:
            raise ValueError(f"Unsupported DSH provider API: {api}")
        executable = find_dsh_bin()
        if executable is None:
            raise FileNotFoundError("DeepSeek Harness is unavailable. Install DSH or set GXWORKS_DSH_BIN.")
        if not _BRIDGE_PLUGIN.is_file():
            raise FileNotFoundError("The GXWorks DSH bridge plugin is missing.")
        sdk_compatibility = []
        if os.name == "nt" and Path(executable).name.lower() == _BUNDLED_EXE:
            if not _WINDOWS_SDK_PLUGIN.is_file():
                raise FileNotFoundError("The GXWorks DSH Windows SDK plugin is missing.")
            sdk_compatibility.append({"id": "gxworks-windows-sdk", "name": _WINDOWS_SDK_PLUGIN.as_uri()})
        base_url = str(config.get("base_url") or config.get("baseUrl") or "").rstrip("/")
        model = str(config.get("model") or "")
        if not base_url or not model:
            raise ValueError("DSH provider settings require base_url and model")

        request_parameters = dict(config.get("request_parameters") or {})
        request_headers = dict(request_parameters.pop("extra_headers", None) or {})
        request_parameters.update(request_parameters.pop("extra_body", None) or {})
        request_headers.update(request_parameters.pop("extra_headers", None) or {})
        reasoning = config.get("reasoning_effort")
        if reasoning:
            request_parameters.setdefault("reasoning_effort", reasoning)
        if api == "openai-responses":
            # The application request policy can retain an input-level effort
            # alongside an explicit native reasoning object.
            if isinstance(request_parameters.get("reasoning"), dict) and "effort" in request_parameters["reasoning"]:
                request_parameters.pop("reasoning_effort", None)
            request_parameters = encode_options(request_parameters)
        else:
            max_output_tokens = request_parameters.pop("max_output_tokens", None)
            if max_output_tokens is not None and "max_completion_tokens" not in request_parameters:
                request_parameters.setdefault("max_tokens", max_output_tokens)
        # These fields are assembled by the DSH loop and must retain its tool,
        # streaming and history semantics.
        for field in {"model", "messages", "tools", "stream"} | RESPONSE_CONTROLS:
            request_parameters.pop(field, None)
        max_tokens = next(
            (request_parameters[key] for key in ("max_completion_tokens", "max_output_tokens", "max_tokens")
             if key in request_parameters),
            None,
        )

        model_profile = {"id": model}
        if config.get("context_window"):
            model_profile["contextWindow"] = config["context_window"]
        if config.get("supports_images"):
            model_profile["input"] = ["text", "image"]
        provider = {
            "api": api,
            "apiKeyEnv": "GXWORKS_DSH_API_KEY",
            "baseURL": base_url,
            "models": [model_profile],
        }
        if api == "openai-completions":
            provider["compat"] = {"supportsStore": False}
            if isinstance(request_parameters.get("thinking"), dict):
                provider["compat"].update(
                    thinkingFormat="deepseek", requiresReasoningContentOnAssistantMessages=True,
                )
        from application.read_delivery import MAX_RESOLVED_TOKENS
        patch = directory / "gxworks.patch.yml"
        patch.write_text(json.dumps([
            *({"id": row, "disabled": True} for row in _DISABLED_ROWS),
            {"id": "sdk-jsonrpc-server", "inject": ["sdkAppStartup", "loader", "gxworksBridge"]},
            {"insert": [
                *sdk_compatibility,
                {"id": "gxworks-provider", "name": "@deepseek-ai/dsh-llm-pi-ai",
                 "config": {"providers": {_PROVIDER: provider}}},
                {"id": "spill-local", "name": "@deepseek-ai/dsh-spill-local",
                 "config": {"root": str(self._home / "spill"), "cleanupPeriodDays": 0}},
                {"id": "spill-policy", "name": "@deepseek-ai/dsh-spill-policy",
                 "config": {"maxInlineTokens": MAX_RESOLVED_TOKENS}},
                {"id": "gxworks-bridge", "name": _BRIDGE_PLUGIN.as_uri()},
            ]},
        ]), encoding="utf-8")
        env = {
            "DSH_HOME": str(self._home),
            "DSH_TELEMETRY_DISABLED": "1",
            "DSH_SYSTEM_PROMPT": str(config.get("system_prompt") or "You are a GXWorks engineering assistant."),
            "GXWORKS_DSH_API_KEY": str(config.get("api_key") or "gxworks-no-provider-key"),
            "GXWORKS_DSH_BRIDGE_URL": self._bridge_url,
            "GXWORKS_DSH_BRIDGE_TOKEN": self._bridge_token,
            "GXWORKS_DSH_MODEL_URL": base_url + ("/responses" if api == "openai-responses" else "/chat/completions"),
            "GXWORKS_DSH_REQUEST_PARAMETERS": json.dumps(request_parameters),
            "GXWORKS_DSH_REQUEST_HEADERS": json.dumps(request_headers),
        }
        launch_args = _desktop_launch_args(Path(executable), patch)
        if launch_args is not None:
            env["ELECTRON_RUN_AS_NODE"] = "1"
        return DeepSeekHarness(
            DeepSeekHarnessConfig(
                provider=_PROVIDER,
                model=model,
                max_tokens=max_tokens,
                cwd=str(self._cwd),
                dsh_bin=executable,
                profile="sdk-minimal",
                patches=(str(patch),),
                dsh_home=str(self._home),
                env=env,
            ),
            _launch_args=launch_args,
        )

    def start(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("The GXWorks DSH runtime is closed")
            if self._harness is not None:
                return
            self._home.mkdir(parents=True, exist_ok=True)
            launch_dir = tempfile.TemporaryDirectory(prefix="gxworks-dsh-")
            try:
                harness = self._create_harness(Path(launch_dir.name))
                try:
                    harness.start()
                except BaseException:
                    harness.close()
                    raise
            except BaseException:
                launch_dir.cleanup()
                raise
            self._launch_directory = launch_dir
            self._harness = harness

    def _started(self) -> DeepSeekHarness:
        self.start()
        harness = self._harness
        if harness is None:
            raise RuntimeError("The GXWorks DSH runtime is closed")
        return harness

    def resume(self, session_id: str) -> None:
        self._started().client.resume_session(session_id)

    def run(
        self,
        prompt: str | list[JsonObject],
        *,
        session_id: str,
        on_notification: Callable[[Notification], None] | None = None,
    ) -> RunResult:
        """Pass encoded image blocks to the SDK for attachment admission."""
        harness = self._started()
        result = Session(harness, session_id).run(prompt, on_notification=on_notification)
        with self._state_lock:
            if self._harness is harness:
                self._needs_durable_shutdown = True
        return result

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            harness, self._harness = self._harness, None
            launch_dir, self._launch_directory = self._launch_directory, None
            strict = self._needs_durable_shutdown
        try:
            if harness is not None:
                harness.close(strict=strict)
        finally:
            if launch_dir is not None:
                launch_dir.cleanup()
