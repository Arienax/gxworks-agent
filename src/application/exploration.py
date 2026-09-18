"""Read-only, version-bound navigation and review cards for the workbench."""
from .projects import public
from plc.explorer import explore_program, issue_cards


def public_exploration(program, *, theme="dark"):
    result = explore_program(program, theme=theme)
    # SVG is a rendered engineering artifact, just like the existing SVG route.
    # Applying metadata's Windows-path regex corrupts the http:// XML namespace.
    svg = result.pop("svg")
    return {**public(result), "svg": svg}


def exploration(projects, project_id, version_id, *, theme="dark"):
    version = projects.raw_version(project_id, version_id)
    if version.get("target_mode") != "ladder":
        raise ValueError("Interactive ladder navigation requires a ladder version")
    program = projects.verified_program(project_id, version_id)
    return public_exploration(program, theme=theme)


def issues(projects, project_id, version_id, report_id=None):
    version = projects.raw_version(project_id, version_id)
    program = projects.verified_program(project_id, version_id)
    if report_id:
        report = projects.report(project_id, report_id)
        if report.get("base_version_id") != version_id:
            raise ValueError("The report belongs to a different version")
        if report.get("base_json_hash"):
            from inspection.models import hash_ladder_json
            from plc.ir import ir_to_ladder
            from application.workspace import ConflictError
            if not program or hash_ladder_json(ir_to_ladder(program)) != report["base_json_hash"]:
                raise ConflictError("The report no longer matches the program content")
        findings = report.get("findings") or []
    else:
        findings = (program or {}).get("analysis", {}).get("findings", [])
    cards = issue_cards(program, findings, report_id=report_id)
    by_id = {card["id"]: card for card in cards}
    for card in cards:
        card["tests"] = []
    # Read through the managed plan boundary, which verifies the saved suite
    # and its version binding. Index entries alone are not evidence of a link.
    for record in reversed(version.get("simulator_test_plans") or []):
        if not isinstance(record, dict):
            continue
        try:
            plan = projects.plan(project_id, version_id, record["plan_id"])
        except (KeyError, ValueError):
            continue
        binding = plan.get("binding") or {}
        if binding.get("project_id") != project_id or binding.get("version_id") != version_id:
            continue
        names = {}
        for test in plan["suite"]["tests"]:
            metadata = (test.get("metadata") or {}).get("workbench") or {}
            if not isinstance(metadata, dict) or metadata.get("project_id") != project_id or metadata.get("version_id") != version_id:
                continue
            linked_ids = metadata.get("issue_ids")
            if not isinstance(linked_ids, list):
                continue
            for issue_id in linked_ids:
                if isinstance(issue_id, str) and issue_id in by_id:
                    tests = names.setdefault(issue_id, [])
                    if test["name"] not in tests:
                        tests.append(test["name"])
        for issue_id, test_names in names.items():
            by_id[issue_id]["tests"].append({"plan_id": binding["plan_id"], "test_names": test_names})
    return public({"version_id": version_id, "report_id": report_id, "issues": cards})
