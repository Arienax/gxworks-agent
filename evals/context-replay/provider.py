"""promptfoo adapter. No production provider, credentials, grader or model."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.context_replay import load_cases, run_case


def call_api(prompt, options, context):
    case_id = context.get("vars", {}).get("case_id", prompt)
    cases = {case["case_id"]: case for case in load_cases()}
    if case_id not in cases:
        return {"error": "Unknown bundled offline case"}
    return {"output": json.dumps(run_case(cases[case_id]), ensure_ascii=False), "cost": 0}
