"""Source Web entrypoint contract without running npm, pip, or GX software."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_build_web_double_click_wrapper_is_thin_and_pauses_on_failure():
    text = (ROOT / "build-web.bat").read_text(encoding="utf-8")
    assert "scripts\\build_web_source.ps1" in text
    assert 'if "%~1"=="" goto :args_done' in text
    assert "for %%A in (%*)" not in text
    assert "--frontend-only" in text
    assert "--no-pause" in text
    assert "build-web.log" in text
    assert "pause" in text.lower()


def test_source_build_script_prepares_python_runtime_by_default():
    text = (ROOT / "scripts" / "build_web_source.ps1").read_text(encoding="utf-8-sig")
    assert ".venv\\Scripts\\python.exe" in text
    assert '"-3"' in text
    assert "-m venv" in text
    assert "requirements\\web.txt" in text
    assert "-m pip install -r" in text
    assert "import fastapi, uvicorn, openai, numpy, mcp" in text
    assert "npm ci failed" in text
    assert "npm run types failed" in text
    assert "npm run build failed" in text
    assert "Source Web runtime is ready. You can now run start-web.cmd." in text


def test_start_web_resolves_source_or_locally_built_package():
    text = (ROOT / "scripts" / "start_web.ps1").read_text(encoding="utf-8-sig")
    assert '.venv\\Scripts\\python.exe' in text
    assert 'dist\\GXWorks-Agent-Web\\GXWorks-Agent-Web.exe' in text
    assert '$backendKind = "source"' in text
    assert '$backendKind = "built-package"' in text
    assert '请先运行根目录 build-web.bat' in text


def test_packaged_sdk_diagnostic_never_starts_web_or_gx(monkeypatch):
    from scripts import web_entry
    import model_runtime.provider as provider
    monkeypatch.setattr(web_entry.sys, "argv", ["GXWorks-Agent-Web.exe", "--self-test-openai-sdk"])
    calls = []
    monkeypatch.setattr(provider, "sdk_runtime_self_test", lambda: calls.append("sdk") or True)
    assert web_entry.run() == 0
    assert calls == ["sdk"]



def test_source_build_checks_knowledge_in_the_venv_not_global_python():
    text = (ROOT/"scripts/build_web_source.ps1").read_text(encoding="utf-8-sig")
    assert '& $venvPython (Join-Path $root "scripts\\web_entry.py") --self-test-knowledge' in text


def test_knowledge_self_test_is_not_a_server_or_model_call(monkeypatch, capsys):
    from scripts import web_entry
    import knowledge.scope as scope
    import model_runtime.provider as provider
    monkeypatch.setattr(web_entry.sys, "argv", ["web", "--self-test-knowledge"])
    monkeypatch.setattr(scope, "runtime_status", lambda:{"status":"available", "engine":"fixture"})
    monkeypatch.setattr(provider, "get_active_provider", lambda: (_ for _ in ()).throw(AssertionError("no provider")))
    assert web_entry.run() == 0
    assert '"available"' in capsys.readouterr().out
