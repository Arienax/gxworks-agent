from pathlib import Path
import re
path = Path("scripts/native_ladder_acceptance.py")
text = path.read_text(encoding="utf-8")

old_rows = '''    _, records = _read_program_rows(path)
    rows = []
    for record in records:'''
new_rows = '''    _, records = _read_program_rows(path)
    rows = []
    for text in _pi_declaration_cells(path):
        rows.append({"kind": "pi_declaration", "text": text})
    for record in records:'''
assert text.count(old_rows) == 1
text = text.replace(old_rows, new_rows, 1)

anchor = '''def _csv_target_matches(path, cpu):'''
helper = '''def _pi_declaration_cells(path):
    """PI声明 lives in a column the program-row reader does not model; keep it visible."""
    import csv
    try:
        table = list(csv.reader(Path(path).read_text(encoding="utf-16", errors="replace").splitlines()))
    except OSError:
        return []
    return [row[5].strip() for row in table if len(row) > 5 and row[5].strip()]


def _program_streams(raw):
    """The program streams alone, so a whole-file change cannot stand in for a body change."""
    from gxw.offline_project import read_offline_project
    project = read_offline_project(raw)
    return {name: data for name, data in project.streams.items()
            if name.endswith(".Program.pou")}


def _csv_target_matches(path, cpu):'''
assert text.count(anchor) == 1
text = text.replace(anchor, helper, 1)

old_status = '''def _compile_status(report):
    """Judge the rebuilt project by what the native component actually reported."""
    report = report or {}
    workspace = report.get("native_workspace", {})
    failures = [row for row in report.get("steps", []) if row.get("code")]
    if workspace.get("status") != "observed" or failures:
        return "unverified"
    return "passed" if workspace.get("compile") == "completed_accepted" else "failed"'''
new_status = '''def _compile_status(report):
    """Judge the rebuilt project by what the native component actually reported."""
    report = report or {}
    workspace = report.get("native_workspace") or {}
    failed_calls = [call for call in workspace.get("calls", [])
                    if call.get("code") or call.get("hresult")]
    if workspace.get("status") != "observed" or failed_calls:
        return "unverified"
    if workspace.get("source_bodies") not in (None, "matched"):
        return "unverified"
    return "passed" if workspace.get("compile") == "completed_accepted" else "failed"'''
assert text.count(old_status) == 1
text = text.replace(old_status, new_status, 1)

start = text.find("    # The native component rebuilds the saved copy without a visible IDE")
end = text.find("        try:\n            stage(\"open\")")
assert 0 < start < end
replacement = '''    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave

    def compile_record(report, project, before, after):
        workspace = (report or {}).get("native_workspace") or {}
        failed_calls = [call for call in workspace.get("calls", [])
                        if call.get("code") or call.get("hresult")]
        try:
            reserialised = _program_streams(before) != _program_streams(after)
        except Exception:
            reserialised = None
        return {"status": _compile_status(report),
                "component": "native_workspace_adapter",
                "command": "compile_project",
                "native_workspace_status": workspace.get("status"),
                "native_compile": workspace.get("compile"),
                "source_bodies": workspace.get("source_bodies"),
                "failed_calls": failed_calls[:5],
                # The whole container is expected to change; only the per-stream
                # comparison says that a program stream was reserialised.
                "project_rewritten_by_native_compile": after != before,
                "program_stream_reserialised": reserialised,
                "project": project}

    with DesktopResourceLock():
        # Compiling the saved copy is a native GX operation and holds the same lock as
        # the window work, so another operation cannot interleave with it.
        stage("compile")
        before = path.read_bytes()
        try:
            compiled = NativeWorkspaceSourceSave().compile_project(
                before, timeout=options.timeout)
        except Exception as error:
            observation = getattr(error, "observation", None)
            workspace = ((observation or {}).get("native_workspace")
                         if isinstance(observation, dict) else None) or {}
            native_compile = workspace.get("compile")
            # A rejection is still a result: it must not be filed as "never ran".
            stages["compile"] = {
                "status": "failed" if native_compile == "completed_rejected" else "unverified",
                "component": "native_workspace_adapter",
                "command": "compile_project",
                "raised": type(error).__name__,
                "native_workspace_status": workspace.get("status"),
                "native_compile": native_compile,
                "observation_keys": sorted(observation) if isinstance(observation, dict) else None,
                "project": _file_record(path)}
            _write_receipt(receipt_path, receipt)
            raise
        path.write_bytes(compiled.data)
        stages["compile"] = compile_record(compiled.report, _file_record(path), before, compiled.data)
        _write_receipt(receipt_path, receipt)
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
'''
text = text[:start] + replacement + text[end + len("        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)\n"):]

old_dup = '''            stage("compile")
            stages["compile"] = compile_stage
'''
assert text.count(old_dup) == 1
text = text.replace(old_dup, "", 1)

text = re.sub(r'"stages": \{name: \{"status": "not_run"\} for name in\s*\((?:[^)]*)\)\}',
              '"stages": {name: {"status": "not_run"} for name in\n'
              '                       ("compile", "open", "save", "cold_reopen", "native_export")}',
              text, count=1)
Path("scripts/native_ladder_acceptance.py").write_text(text, encoding="utf-8")
print("patched 5 issues")
