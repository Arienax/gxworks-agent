import json

import shared.diagnostics as diagnostics
from application.jobs import JobContext


class _Manager:
    def emit(self, job_id, event_type, data):
        return {"job_id": job_id, "type": event_type, "data": data}


def test_context_audit_is_mirrored_as_bounded_diagnostic_metadata(tmp_path):
    report = {
        "policy": {"name": "legacy", "version": 1},
        "request_index": 1,
        "message_text_chars": 66000,
        "messages": [
            {"role": "system", "text_chars": 50000, "images": 0},
            {"role": "user", "text_chars": 16000, "images": 0},
        ],
        "sections": [
            {"section": "manual_chunk:1688", "status": "included", "chars": 4200},
            {"section": "manual_chunk:703", "status": "excluded", "chars": 3000},
            {"section": "base_prompt", "status": "included", "chars": 18000},
        ],
        "dropped_sections": 0,
    }
    with diagnostics.diagnostic_scope(tmp_path, "job_test", kind="generation", policy="legacy"):
        JobContext(_Manager(), "job_test").emit("context_audit", report)

    rows = [
        json.loads(line)
        for line in (tmp_path / "diagnostics" / "job_test.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    event = next(row for row in rows if row["event"] == "context_audit")
    assert event["policy"] == "legacy"
    assert event["message_chars"] == 66000
    assert event["context_section_count"] == 3
    assert event["included_section_count"] == 2
    assert event["excluded_section_count"] == 1
    assert event["retrieval_section_count"] == 2
    assert event["retrieval_context_chars"] == 4200
    assert event["included_section_chars"] == 22200
    assert event["excluded_section_chars"] == 3000
