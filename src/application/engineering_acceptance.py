"""Layered engineering result accounting without promoting absent evidence."""
from collections import Counter
from copy import deepcopy


LAYERS = ("identity_interface", "save_cold_read", "current_source_compile", "native_check_complete", "process_behavior")
STATUSES = {"passed", "failed", "unverified", "not_entered"}


def native_acceptance_results(observation, *, cpu, software_version=None):
    """Project existing native observations without repairing missing proof.

    A current-source compile can pass while the complete public check remains
    unverified. Raw backend progress or an empty diagnostic list cannot pass it.
    """
    compile_row = observation.get("compilation", {})
    check = observation.get("public_check", {})
    selection = observation.get("selection", {})
    projection = observation.get("diagnostic_projection", {})
    version = observation.get("native_version")
    bound = (observation.get("cpu") == cpu and bool(version) and
             (software_version is None or software_version == version) and
             selection.get("status") == "verified" and selection.get("source_bytes_current") is True and
             selection.get("configuration_bytes_current") is True and bool(selection.get("selected_sources")) and
             selection.get("empty_task") is False)
    compiled = (bound and compile_row.get("completed") is True and compile_row.get("diagnostics_complete") is True and
                compile_row.get("source_context", {}).get("status") == "current" and
                compile_row.get("diagnostic_conversion", {}).get("status") == "completed")
    compile_status = ("passed" if compile_row.get("acceptance") == "accepted" else
                      "failed" if compile_row.get("acceptance") == "rejected" else "unverified") if compiled else "unverified"
    checked = (compile_status == "passed" and check.get("completed") is True and observation.get("current_source_check") == "established" and
               projection.get("status") == "completed")
    check_status = ("passed" if check.get("status") == "passed" else
                    "failed" if check.get("status") == "failed" else "unverified") if checked else "unverified"
    compile_evidence = {key: deepcopy(compile_row.get(key)) for key in
                        ("completed", "acceptance", "diagnostics_complete", "diagnostic_conversion", "source_context")}
    return {"current_source_compile": {"status": compile_status, "evidence": compile_evidence,
                                       "reason": None if compile_status != "unverified" else "current_complete_compile_not_established"},
            "native_check_complete": {"status": check_status, "evidence": {
                "public_check": deepcopy(check), "current_source_check": observation.get("current_source_check"),
                "diagnostic_projection_status": projection.get("status"), "native_version": version, "cpu": observation.get("cpu")},
                "reason": None if check_status != "unverified" else "complete_current_public_check_not_established"}}


def acceptance_record(*, cpu, software_version=None, source=None, results=None):
    results = results or {}
    if set(results) - set(LAYERS):
        raise ValueError("unknown engineering acceptance layer")
    rows = []
    for layer in LAYERS:
        row = deepcopy(results.get(layer, {"status": "unverified", "reason": "not_executed"}))
        if row.get("status") not in STATUSES:
            raise ValueError("invalid engineering acceptance status")
        if row["status"] == "passed" and not row.get("evidence"):
            raise ValueError("a passed engineering layer needs its actual evidence")
        rows.append({"layer": layer, **row})
    return {"cpu": cpu, "software_version": software_version, "source": source, "layers": rows,
            "status": "passed" if all(r["status"] == "passed" for r in rows) else "failed" if any(r["status"] == "failed" for r in rows) else "unverified"}


def acceptance_summary(records):
    """Keep all-candidate and conditional denominators, including unknown runs."""
    result = {"candidates": len(records), "layers": []}
    cumulative = set(range(len(records)))
    for layer in LAYERS:
        rows = [next(r for r in record["layers"] if r["layer"] == layer) for record in records]
        counts = Counter(r["status"] for r in rows)
        entering = sum(r["status"] != "not_entered" for r in rows)
        passed = {index for index, row in enumerate(rows) if row["status"] == "passed"}
        cumulative.intersection_update(passed)
        result["layers"].append({"layer": layer, "counts": dict(counts), "entering": entering,
                                 "conditional_pass_rate": len(passed)/entering if entering else None,
                                 "all_candidate_pass_rate": len(passed)/len(rows) if rows else None,
                                 "cumulative_passed": len(cumulative)})
    return result
