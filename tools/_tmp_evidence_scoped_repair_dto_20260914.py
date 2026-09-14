from pathlib import Path

path = Path("src/integrations/web/responses.py")
text = path.read_text(encoding="utf-8")
old = '''class ResponseViolation(PublicResource):
    path: str
    reason: Literal["unsupported_script", "non_english_script", "japanese_script", "latin_prose",
                    "ambiguous_han_only", "invalid_prose_field", "invalid_json_object", "invalid_code_field", "invalid_response",
                    "invalid_shared_input", "invalid_ladder_structure", "field_too_long", "repair_base_invalid",
                    "repair_identity_invalid", "repair_shape_invalid", "repair_scope_violation", "repair_no_progress"]
'''
new = '''class ResponseViolation(PublicResource):
    path: str
    reason: Literal["unsupported_script", "non_english_script", "japanese_script", "latin_prose",
                    "ambiguous_han_only", "invalid_prose_field", "invalid_json_object", "invalid_code_field", "invalid_response",
                    "invalid_shared_input", "invalid_ladder_structure", "field_too_long", "repair_base_invalid",
                    "repair_identity_invalid", "repair_shape_invalid", "repair_scope_violation", "repair_no_progress"]
    observed_opcode: str | None = Field(
        default=None, max_length=64, pattern=r"^[A-Z0-9_.$@+\\-]+$"
    )
'''
if text.count(old) != 1:
    raise SystemExit("ResponseViolation marker mismatch")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
