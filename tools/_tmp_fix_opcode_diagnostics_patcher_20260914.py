from pathlib import Path

path = Path("src/plc_json_validator.py")
text = path.read_text(encoding="utf-8")
old = '''        def fail_opcode(message):\n            fail_opcode(message, observed_opcode=opcode)\n'''
new = '''        def fail_opcode(message):\n            _fail(f"{path}.opcode", message, observed_opcode=opcode)\n'''
if text.count(old) != 1:
    raise SystemExit(f"expected one recursive helper produced by staging patch, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
