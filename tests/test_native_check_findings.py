# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""A rejected native check must reach the caller as devices, not as raw repetition."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from application.gxw_delivery import _native_failure_summary
from gxw.diagnostic_scope import (
    device_write_rungs,
    native_check_findings,
    summarize_check_reports,
)


def report(code, device=None, step=0, kind=2):
    entry = {"kind": kind, "code": code, "step": step}
    if isinstance(device, str):
        entry["arguments"] = [{"text": device, "is_null": False}]
    else:
        entry["arguments"] = []
    return entry


def ladder(*writes):
    """Build a submitted ladder from (rung_id, opcode, device) rows."""
    rungs = {}
    for rung_id, opcode, device in writes:
        rungs.setdefault(rung_id, {"rung_id": rung_id, "ordered_instructions": []})
        rungs[rung_id]["ordered_instructions"].append({"op": opcode, "args": [device]})
    return {"rungs": [rungs[key] for key in sorted(rungs)]}


def test_repeated_reports_collapse_per_device_and_keep_every_position():
    reports = [report(0x050C9300, "M120", step=300 + index) for index in range(12)]
    findings = summarize_check_reports(reports)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["code"] == "0x050c9300"
    assert finding["category"] == "duplicate_output"
    assert finding["device"] == "M120"
    assert finding["severity"] == "error"
    assert finding["occurrences"] == 12
    assert finding["steps"] == [300 + index for index in range(12)]


def test_distinct_devices_stay_separate_findings():
    reports = [report(0x050C9300, "M1", step=1), report(0x050C9300, "Y7", step=2),
               report(0x050C9300, "M1", step=3)]
    findings = summarize_check_reports(reports)
    assert [row["device"] for row in findings] == ["M1", "Y7"]
    assert [row["occurrences"] for row in findings] == [2, 1]


def test_writing_rungs_come_from_the_submitted_ladder():
    reports = [report(0x050C9300, "M5", step=10), report(0x050C9300, "M5", step=20)]
    writes = device_write_rungs(ladder((1, "RST", "M5"), (2, "LD", "X0"), (9, "SET", "M5")))
    findings = summarize_check_reports(reports, writes=writes)
    assert findings[0]["rung_ids"] == [1, 9]


def test_a_contact_is_not_a_writing_position():
    writes = device_write_rungs(ladder((1, "LD", "M5"), (2, "AND", "M5"), (3, "OUT", "M5")))
    assert writes == {"M5": [3]}


def test_a_device_the_ladder_does_not_write_gets_no_invented_rungs():
    writes = device_write_rungs(ladder((1, "OUT", "Y0")))
    findings = summarize_check_reports([report(0x050C9300, "M77")], writes=writes)
    assert findings[0]["device"] == "M77"
    assert "rung_ids" not in findings[0]


def test_uncollected_code_stays_visible_and_unclassified():
    findings = summarize_check_reports([report(0x0BADF00D, "D10", step=4)])
    assert findings[0]["code"] == "0x0badf00d"
    assert findings[0]["category"] == "unclassified_report"
    assert findings[0]["severity"] == "error"
    assert "0x0badf00d" in findings[0]["message"]


def test_reports_without_an_operand_are_kept():
    findings = summarize_check_reports([report(0x500C2020)])
    assert findings[0]["code"] == "0x500c2020"
    assert findings[0]["category"] == "floating_wire_segment"
    assert "device" not in findings[0]


def test_a_warning_keeps_its_severity_instead_of_becoming_an_error():
    findings = summarize_check_reports([report(0x050C9300, "M2", kind=3)])
    assert findings[0]["severity"] == "warning"
    assert findings[0]["fixable"] is False


def test_native_check_findings_without_retained_reports_returns_nothing():
    assert native_check_findings({"diagnostic_projection": {}}) == []
    assert native_check_findings(None) == []


def test_native_check_findings_reads_the_retained_reports():
    validation = {"diagnostic_projection": {"public_diagnostics": [report(0x050C9300, "M2", step=735)]}}
    findings = native_check_findings(validation, ladder((1, "RST", "M2"), (39, "SET", "M2")))
    assert findings[0]["device"] == "M2"
    assert findings[0]["rung_ids"] == [1, 39]


def test_summary_names_the_device_and_stays_silent_when_unclassified():
    headline = _native_failure_summary([{"severity": "error", "category": "duplicate_output",
                                         "device": "M2", "code": "0x050c9300"}])
    assert headline is not None and "M2" in headline and "双线圈" in headline
    assert _native_failure_summary([]) is None
    warnings_only = [{"severity": "warning", "category": "duplicate_output", "device": "M2"}]
    assert _native_failure_summary(warnings_only) is None


def test_a_rejected_native_check_reaches_the_operator_with_a_repair_baseline(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    import application.gxw_delivery as delivery
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError

    submitted = ladder((1, "RST", "M42"), (7, "SET", "M42"), (8, "OUT", "Y3"))
    submitted["device_comments"] = {"M42": "槽占用"}
    (tmp_path / "ladder.json").write_text(json.dumps(submitted, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "program.gxw").write_bytes(b"candidate")
    metadata = {"target_mode": "ladder", "plc_model": "FX3U", "delivery_requested": True,
                "artifacts": {"json": "ladder.json", "gxw": "program.gxw"}, "validation": {}}

    monkeypatch.setattr(delivery, "installation", lambda: Path("E:/gppw2/GD2.exe"))

    def compile_project(self, raw):
        error = WorkspaceSaveError("公共检查拒绝", {"status": "completed"})
        error.validation = {"diagnostic_projection": {"public_diagnostics": [
            report(0x050C9300, "M42", step=900 + index) for index in range(5)]}}
        raise error

    monkeypatch.setattr(NativeWorkspaceSourceSave, "compile_project", compile_project)
    delivery.finalize_saved(tmp_path, metadata)

    validation = metadata["validation"]
    assert validation["gx_compile"] == "failed"
    findings = validation["native_compile_findings"]
    assert len(findings) == 1
    assert findings[0]["device"] == "M42"
    assert findings[0]["occurrences"] == 5
    assert findings[0]["rung_ids"] == [1, 7]
    # The operator sees the device and the category; the adapter tokens stay in detail.
    assert "M42" in validation["native_compile_message"]
    assert "双线圈" in validation["native_compile_message"]
    assert validation["native_compile_detail"] == "公共检查拒绝"
    assert metadata["activation_blocked"] is True
    assert metadata["lifecycle_status"] == "diagnostic"
    # The rejected ladder stays available as the repair baseline.
    assert metadata["artifacts"]["repair_candidate"] == "repair_candidate.json"
    kept = json.loads((tmp_path / "repair_candidate.json").read_text(encoding="utf-8"))
    assert kept["rungs"] == submitted["rungs"]


@pytest.mark.parametrize(('case', 'deliverable'), [
    ('warning', True), ('SET', True), ('RST', True), ('OUT', False),
    ('unbound_step', False), ('other_error', False), ('compile_failed', False),
])
def test_native_check_advisories_do_not_block_gxw_delivery(tmp_path, monkeypatch, case, deliverable):
    import json
    import application.gxw_delivery as delivery
    from gxworks2.workspace_adapter import NativeWorkspaceSourceSave, WorkspaceSaveError

    opcode = case if case in {'SET', 'RST', 'OUT'} else 'SET'
    submitted = ladder((1, opcode, 'M0'), (2, opcode, 'M0'))
    for step, rung in zip((10, 20), submitted['rungs']):
        rung['ordered_instructions'][0].update(kind='instruction', step=step)
    (tmp_path / 'ladder.json').write_text(json.dumps(submitted), encoding='utf-8')
    (tmp_path / 'program.gxw').write_bytes(b'candidate')
    metadata = {'target_mode': 'ladder', 'plc_model': 'Q02/Q02H', 'program_name': 'MAIN.Program.pou',
                'delivery_requested': True, 'artifacts': {'json': 'ladder.json', 'gxw': 'program.gxw'},
                'validation': {'compile_state': 'stale'}}

    steps = (10, 99) if case == 'unbound_step' else (10, 20)
    kind = 3 if case in {'warning', 'compile_failed'} else 2
    reports = [{**report(0x050C9300, 'M0', step=step, kind=kind), 'name': {'text': 'MAIN'}} for step in steps]
    backend = [*reports, report(0x500C2020, step=30)] if case == 'other_error' else reports
    native = {'compilation': {'completed': True, 'acceptance': 'rejected' if case == 'compile_failed' else 'accepted'},
              'public_check': {'completed': True, 'status': 'rejected'},
              'selection': {'status': 'verified', 'source_bytes_current': True, 'selected_sources': ['MAIN']},
              'diagnostic_projection': {'public_diagnostics': reports, 'backend_diagnostics': backend}}

    def compile_project(self, raw):
        error = WorkspaceSaveError('Public check rejected')
        error.validation = native
        raise error

    monkeypatch.setattr(delivery, 'installation', lambda: Path('E:/gppw2/GD2.exe'))
    monkeypatch.setattr(NativeWorkspaceSourceSave, 'compile_project', compile_project)
    delivery.finalize_saved(tmp_path, metadata)
    assert (tmp_path / 'program.gxw').read_bytes() == b'candidate'
    diagnostics = json.loads((tmp_path / 'native_diagnostics.json').read_bytes())
    assert diagnostics['validation'] == native
    assert bool(metadata.get('activation_blocked')) is not deliverable
    if deliverable:
        validation = metadata['validation']
        assert validation['native_check_advisory'] is True
        assert validation['gx_compile'] == validation['native_compile'] == 'passed'
        assert validation['public_check'] == 'completed-rejected'
        assert validation['native_save'] == validation['cold_reopen'] == 'not_run'
        assert validation['compile_state'] == 'stale'
        assert 'repair_candidate' not in metadata['artifacts']
        finding = validation['native_compile_findings'][0]
        assert finding['severity'] == 'warning' and finding['fixable'] is False
        if case in {'SET', 'RST'}:
            assert finding['category'] == 'repeated_latch_write'
            assert diagnostics['findings'][0]['severity'] == 'error'
    else:
        assert metadata['validation']['gx_compile'] == 'failed'
        assert metadata['artifacts']['repair_candidate'] == 'repair_candidate.json'


def test_a_native_diagnostic_version_can_be_repaired_from_its_saved_ladder(tmp_path):
    import json
    import threading
    from types import SimpleNamespace

    from application.generation_repair_workflow import RepairServices, repair_generation

    job_id = "job_" + "a" * 32
    baseline = {"device_comments": {"M42": "槽占用"}, "rungs": [
        {"rung_id": 1, "ordered_instructions": [{"op": "RST", "args": ["M42"]}]},
        {"rung_id": 7, "ordered_instructions": [{"op": "SET", "args": ["M42"]}]}]}
    baseline_path = tmp_path / "repair_candidate.json"
    baseline_path.write_text(json.dumps(baseline, ensure_ascii=False), encoding="utf-8")
    record = {"id": job_id, "kind": "direct_generation", "status": "completed",
              "result": {"status": "saved_native_diagnostic", "version_id": "v0001"},
              "snapshot": {"project_id": "p", "version_id": "v0001", "raw_requirement": "分拣",
                           "response_language": "zh-CN",
                           "project": {"plc_model": "FX3U", "target_mode": "ladder",
                                       "confirmed_spec": None}},
              "error_details": None}
    submitted = []
    services = RepairServices(
        writable=lambda: None,
        lock=SimpleNamespace(thread_lock=threading.RLock()),
        jobs=SimpleNamespace(record=lambda _id: record),
        projects=SimpleNamespace(
            raw_project=lambda _id: {"active_version_id": "v0001", "confirmed_spec": None,
                                     "target_mode": "ladder", "plc_model": "FX3U"},
            artifact=lambda *_args: baseline_path),
        state_dir=tmp_path,
        validate_snapshot=lambda _snapshot: None,
        output=lambda _id: {"generation": {"validation": {"native_compile_findings": [
            {"device": "M42", "rung_ids": [1, 7], "message": "M42: duplicate output"}]}}},
        direct_user_fact_text=lambda _snapshot: "分拣",
        submit=lambda command: submitted.append(command) or {"id": "repair"})

    repair_generation(services, job_id, "request_" + "b" * 24)

    command = submitted[0]
    assert command["repair_origin_job_id"] == job_id
    assert command["repair_mode"] is True
    assert command["kind"] == "generation"
    # The repair prompt names the reported device and its writing rungs.
    assert "M42" in command["text"]
    assert "1, 7" in command["text"]
