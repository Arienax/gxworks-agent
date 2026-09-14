from pathlib import Path

path = Path("tests/test_model_provider.py")
text = path.read_text(encoding="utf-8")
old = '''            if isinstance(properties, dict):
                type_rule = properties.get("type")
                if isinstance(type_rule, dict) and type_rule.get("const") == "APP_INSTR":
                    return value
'''
new = '''            if isinstance(properties, dict):
                opcode_rule = properties.get("opcode")
                if isinstance(opcode_rule, dict) and isinstance(opcode_rule.get("enum"), list):
                    return value
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one APP_INSTR test locator, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
