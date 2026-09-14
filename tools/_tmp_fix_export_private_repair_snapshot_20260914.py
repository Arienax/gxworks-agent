from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def write(path, text):
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path, old, new):
    text = read(path)
    if text.count(old) != 1:
        raise SystemExit(f"{path}: marker mismatch")
    write(path, text.replace(old, new, 1))


path = "src/runtime_diagnostics.py"
old = '''def _export_repair_baseline_opcode(job):\n    snapshot = job.get("snapshot") if isinstance(job, dict) else None\n    if not isinstance(snapshot, dict) or not snapshot.get("repair_mode"):\n        return None\n'''
new = '''def _export_private_job_snapshot(state_dir, job_id):\n    """Read only the persisted job snapshot needed for an operator diagnostic export."""\n    try:\n        from application.workspace import contained, read_json, record_id\n        state = Path(state_dir).resolve()\n        directory = contained(state / "jobs", state)\n        path = contained(directory / (record_id(job_id, "job") + ".json"), directory)\n        if not path.is_file() or path.is_symlink():\n            return None\n        record = read_json(path)\n        if not isinstance(record, dict) or record.get("id") != job_id:\n            return None\n        snapshot = record.get("snapshot")\n        return snapshot if isinstance(snapshot, dict) else None\n    except (KeyError, ValueError, OSError, TypeError):\n        return None\n\n\ndef _export_job_snapshot(state_dir, job):\n    snapshot = job.get("snapshot") if isinstance(job, dict) else None\n    if isinstance(snapshot, dict):\n        return snapshot\n    job_id = job.get("id") if isinstance(job, dict) else None\n    if not isinstance(job_id, str):\n        return None\n    return _export_private_job_snapshot(state_dir, job_id)\n\n\ndef _export_repair_baseline_opcode(snapshot):\n    if not isinstance(snapshot, dict) or not snapshot.get("repair_mode"):\n        return None\n'''
replace_once(path, old, new)
replace_once(
    path,
    '''def _enrich_export_opcode_context(records, job):\n    baseline_opcode = _export_repair_baseline_opcode(job)\n    snapshot = job.get("snapshot") if isinstance(job, dict) else None\n''',
    '''def _enrich_export_opcode_context(records, state_dir, job):\n    snapshot = _export_job_snapshot(state_dir, job)\n    baseline_opcode = _export_repair_baseline_opcode(snapshot)\n''',
)
replace_once(
    path,
    '''    records = _enrich_export_opcode_context(records, job)\n''',
    '''    records = _enrich_export_opcode_context(records, state_dir, job)\n''',
)


test_path = "tests/test_runtime_diagnostics.py"
test = read(test_path)
old = '''    job = {\n        "id": "job_test", "status": "failed", "kind": "generation",\n        "snapshot": {\n            "repair_mode": True, "repair_baseline": baseline, "allowed_rung_ids": [1],\n            "project": {"plc_model": "FX3U"},\n        },\n    }\n    with zipfile.ZipFile(io.BytesIO(d.export_diagnostics(tmp_path, job))) as archive:\n'''
new = '''    private_record = {\n        "id": "job_test",\n        "snapshot": {\n            "repair_mode": True, "repair_baseline": baseline, "allowed_rung_ids": [1],\n            "project": {"plc_model": "FX3U"},\n        },\n    }\n    jobs_dir = tmp_path / "jobs"\n    jobs_dir.mkdir()\n    (jobs_dir / "job_test.json").write_text(json.dumps(private_record), encoding="utf-8")\n    # Match the real HTTP path: JobManager.get() returns a public job without snapshot.\n    job = {"id": "job_test", "status": "failed", "kind": "generation"}\n    with zipfile.ZipFile(io.BytesIO(d.export_diagnostics(tmp_path, job))) as archive:\n'''
if test.count(old) != 1:
    raise SystemExit("tests/test_runtime_diagnostics.py: public/private job fixture marker mismatch")
write(test_path, test.replace(old, new, 1))
