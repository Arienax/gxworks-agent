import json

def test_audit_operand_order_prompt_conflicts():
    from tools.audit_operand_order_prompt_conflicts import build_report
    report = build_report()
    # Temporary audit test: keep the complete report in CI failure output.
    raise AssertionError(json.dumps(report, ensure_ascii=False, indent=2))
