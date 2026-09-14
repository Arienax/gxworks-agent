from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected 1 marker, got {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# A deterministic repair is still a minimal repair context even when no provider is needed.
replace_once(
    "src/application/workbench.py",
    '''            repair_context = "minimal" if command.get("repair_mode") or command.get("format_repair") else None
            snapshot["context_policy"] = resolve_context_policy(
                repair_context if requires_model else "legacy"
            ).snapshot()
''',
    '''            repair_context = "minimal" if command.get("repair_mode") or command.get("format_repair") else None
            policy_name = repair_context if repair_context else (None if requires_model else "legacy")
            snapshot["context_policy"] = resolve_context_policy(policy_name).snapshot()
''',
)

# Public generation error details may expose the same bounded opcode token already allowed in diagnostics.
path = "src/application/job_errors.py"
text = Path(path).read_text(encoding="utf-8")
marker = "\ndef public_error_details(value):\n"
helper = '''
def _diagnostic_opcode(value):
    if not isinstance(value, str):
        return None
    token = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_.$@+\\-]{1,64}", token):
        return None
    lowered = token.lower()
    if any(marker in lowered for marker in ("sk-", "bearer", "secret", "private", "api_key", "token", "password")):
        return None
    return token


'''
if marker not in text or "def _diagnostic_opcode" in text:
    raise SystemExit("job_errors helper marker mismatch")
Path(path).write_text(text.replace(marker, helper + marker, 1), encoding="utf-8")
replace_once(
    path,
    '''            reason = violation.get("reason")
            rows.append({"path": _diagnostic_path(violation.get("path")),
                         "reason": reason if isinstance(reason, str) and reason in _REASONS else "invalid_response"})
''',
    '''            reason = violation.get("reason")
            row = {"path": _diagnostic_path(violation.get("path")),
                   "reason": reason if isinstance(reason, str) and reason in _REASONS else "invalid_response"}
            observed_opcode = _diagnostic_opcode(violation.get("observed_opcode"))
            if observed_opcode is not None:
                row["observed_opcode"] = observed_opcode
            rows.append(row)
''',
)
