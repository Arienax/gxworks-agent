from pathlib import Path
path = Path("scripts/native_ladder_acceptance.py")
text = path.read_text(encoding="utf-8")

# 1) PI声明 from the table the repository reader already parsed (Tab separated)
old = '''    _, records = _read_program_rows(path)
    rows = []
    for text in _pi_declaration_cells(path):
        rows.append({"kind": "pi_declaration", "text": text})'''
new = '''    table, records = _read_program_rows(path)
    rows = []
    # PI声明 sits in a column the record reader does not model, so take it from the
    # same parsed table instead of reading the file a second time.
    for row in table[3:]:
        if len(row) > 5 and row[5].strip():
            rows.append({"kind": "pi_declaration", "text": row[5].strip()})'''
assert text.count(old) == 1
text = text.replace(old, new, 1)

dead = '''def _pi_declaration_cells(path):
    """PI声明 lives in a column the program-row reader does not model; keep it visible."""
    import csv
    try:
        table = list(csv.reader(Path(path).read_text(encoding="utf-16", errors="replace").splitlines()))
    except OSError:
        return []
    return [row[5].strip() for row in table if len(row) > 5 and row[5].strip()]


'''
assert text.count(dead) == 1
text = text.replace(dead, "", 1)

# 2) the component returns source_bodies under validation
old_bodies = '''        return {"status": _compile_status(report),
                "component": "native_workspace_adapter",
                "command": "compile_project",
                "native_workspace_status": workspace.get("status"),
                "native_compile": workspace.get("compile"),
                "source_bodies": workspace.get("source_bodies"),'''
new_bodies = '''        validation = (report or {}).get("validation") or {}
        source_bodies = validation.get("source_bodies")
        return {"status": _compile_status(report),
                "component": "native_workspace_adapter",
                "command": "compile_project",
                "native_workspace_status": workspace.get("status"),
                "native_compile": workspace.get("compile"),
                "source_bodies": source_bodies,
                # The component compares the extracted program body before and after
                # the rebuild; the POU stream changing does not contradict that.
                "program_body_verified_equal": None if source_bodies is None
                else source_bodies == "matched",'''
assert text.count(old_bodies) == 1
text = text.replace(old_bodies, new_bodies, 1)

old_status = '''    if workspace.get("source_bodies") not in (None, "matched"):
        return "unverified"'''
new_status = '''    source_bodies = ((report.get("validation") or {}).get("source_bodies"))
    if source_bodies not in (None, "matched"):
        return "unverified"'''
assert text.count(old_status) == 1
text = text.replace(old_status, new_status, 1)

# 3) an exception observation is the component payload itself, not a wrapped report
old_obs = '''            workspace = ((observation or {}).get("native_workspace")
                         if isinstance(observation, dict) else None) or {}'''
new_obs = '''            workspace = ((observation.get("native_workspace") or observation)
                         if isinstance(observation, dict) else {})'''
assert text.count(old_obs) == 1
text = text.replace(old_obs, new_obs, 1)

# 4) stop implying the body changes
old_note = '''                # The whole container is expected to change; only the per-stream
                # comparison says that a program stream was reserialised.'''
new_note = '''                # The container and the POU stream change; the program body itself is
                # compared by the component and reported above.'''
assert text.count(old_note) == 1
text = text.replace(old_note, new_note, 1)

path.write_text(text, encoding="utf-8")
import py_compile
py_compile.compile("scripts/native_ladder_acceptance.py", doraise=True)
print("four corrections applied")
