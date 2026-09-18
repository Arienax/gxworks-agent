"""Human-field declarations for model workflows, independent of any adapter.

These selectors describe the human slots of the existing workflow schemas;
protocol and source fields are deliberately excluded. This is not full schema
validation. Add a declaration when a schema or renderer introduces a visible
model field. No data is translated or normalized by these contracts.
"""

from model_runtime.responses import ResponseContract


_LADDER = ("device_comments.*", "rungs.**.label", "rungs.*.debug_note", "rungs.*.comment")
LADDER_RESPONSE = ResponseContract("ladder", "json", _LADDER, annotation_paths=_LADDER)
FIELD_PATCH_RESPONSE = ResponseContract("field_patch", "json", ())
ST_RESPONSE = ResponseContract("st", "json", st_paths=("st_code",))
ANALYSIS_RESPONSE = ResponseContract("analysis", "json", (
    "summary", "approaches.*.name", "approaches.*.description", "approaches.*.pros",
    "approaches.*.cons", "approaches.*.generation_guide", "missing_info.*.question",
    "assumptions", "format_diagnostics.*", "format_diagnostics.*.message",
    "flowchart_steps.*.label",
    *(f"suggested_io.{kind}.*" for kind in ("X", "Y", "M", "D", "T", "C", "S", "SM", "SD", "special_relays", "special_registers")),
), structured_paths=("format_diagnostics.*",))
# control_type is a legacy Chinese enum. options/default/required_when are
# interdependent comparison values. execution_semantics.evidence is user text.
# None may be translated or treated as a newly authored prose field.
DEBUG_RESPONSE = ResponseContract("debug", "json", (
    "summary", "possible_causes", "recommended_changes", "fix_instruction",
))
DIAGNOSIS_RESPONSE = ResponseContract("diagnosis", "json", ("root_cause", "recommended_change"))
_PATCH = ("device_comments.*", "operations.*.ladder.**.label",
          "operations.*.ladder.debug_note", "operations.*.ladder.comment")
PATCH_RESPONSE = ResponseContract("patch", "json", _PATCH, annotation_paths=_PATCH)
_FINDING_TEXT = (
    "title", "message", "description", "summary", "suggestion", "recommendation",
    "recommended_change", "recommended_changes", "fix_instruction", "repair_instruction",
)
_CHECK_TEXT = ("instruction", "check", "description", "condition", "reason", "expected", "observed")
_REPORT_ROWS = {
    "findings": _FINDING_TEXT, "local_findings": _FINDING_TEXT,
    "online_checks": _CHECK_TEXT, "verification_steps": _CHECK_TEXT,
}
# The inspection normalizer accepts legacy aliases and singleton/string rows.
# They are the same visible fields, so they must not bypass the response policy.
INSPECTION_RESPONSE = ResponseContract("inspection", "json", (
    "summary", "followup_questions", "possible_causes", "recommended_changes",
    *(root for root in _REPORT_ROWS),
    *(f"{root}.{key}" for root, keys in _REPORT_ROWS.items() for key in keys),
    *(f"{root}.*.{key}" for root, keys in _REPORT_ROWS.items() for key in keys),
), structured_paths=tuple(path for root in _REPORT_ROWS for path in (root, root + ".*")))
TEST_SUITE_RESPONSE = ResponseContract("test_suite", "json", (
    "description", "display_name", "tests.*.description", "tests.*.display_name",
))
# Suite/test names and step IDs are evidence identities, not mutable labels.


def tool_argument_contract(name):
    if name == "create_program_candidate":
        paths = tuple("ladder." + path for path in _LADDER)
        return ResponseContract("tool_candidate", "json", paths, annotation_paths=paths)
    if name == "patch_program":
        paths = tuple("patch." + path for path in _PATCH)
        return ResponseContract("tool_patch", "json", paths, annotation_paths=paths)
    return None
