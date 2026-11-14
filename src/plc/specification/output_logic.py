"""Design output decisions, not user constraints or a proof of PLC behavior."""
from jsonschema import Draft202012Validator


OUTPUT_LOGIC_FIELDS = (
    "target", "state", "activation", "update", "release", "interruption", "selection",
)
OUTPUT_LOGIC_SCHEMA = {
    "type": "array", "minItems": 1,
    "items": {
        "type": "object",
        "properties": {key: {"type": "string", "pattern": r"\S"} for key in OUTPUT_LOGIC_FIELDS},
        "required": list(OUTPUT_LOGIC_FIELDS), "additionalProperties": False,
    },
}
_VALIDATOR = Draft202012Validator(OUTPUT_LOGIC_SCHEMA)


def output_logic_details(payload, *, required=False):
    """Check presence/shape only; never guess engineering decisions from prose."""
    details = []
    approaches = payload.get("approaches")
    for index, approach in enumerate(approaches if isinstance(approaches, list) else []):
        if not isinstance(approach, dict):
            continue
        if "output_logic" not in approach and not required:
            continue
        errors = list(_VALIDATOR.iter_errors(approach.get("output_logic")))
        if errors:
            details.append({
                "path": f"$.approaches[{index}].output_logic",
                "code": "incomplete_output_logic",
                "message": "模型须在确认前补齐输出实现，不转交用户选择技术方法："
                           + "; ".join(f"{error.json_path}: {error.message}" for error in errors),
            })
    return details
