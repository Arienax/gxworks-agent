#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/main.py",
    "        repair_mode=False,\n        allowed_rung_ids=None,\n",
    "        repair_mode=False,\n        format_repair=False,\n        allowed_rung_ids=None,\n",
    "CompilerThread format_repair argument",
)
replace_once(
    "src/main.py",
    "        self.repair_mode = bool(repair_mode)\n        self.allowed_rung_ids = {\n",
    "        self.repair_mode = bool(repair_mode)\n        self.format_repair = bool(format_repair)\n        self.allowed_rung_ids = {\n",
    "CompilerThread format_repair state",
)

registry = Path("src/instruction_registry.py")
text = registry.read_text(encoding="utf-8")
anchor = "DEFAULT_INSTRUCTION_REGISTRY = load_default_instruction_registry()\n\n\ndef get_instruction_spec("
block = '''DEFAULT_INSTRUCTION_REGISTRY = load_default_instruction_registry()


GENERATION_TYPED_OUTPUT_OPCODES = frozenset({"OUT", "PLS", "PLF", "END"})
GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES = frozenset(
    {InstructionCategory.CONDITION, InstructionCategory.BRANCH_CONTROL}
)


def generation_app_instr_mnemonics(cpu=None):
    """Opcodes the model may emit as APP_INSTR for the selected CPU."""
    model = str(cpu or "").strip().upper() or None
    result = []
    for mnemonic in DEFAULT_INSTRUCTION_REGISTRY.known_mnemonics():
        spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic)
        if spec is None:
            continue
        if mnemonic in GENERATION_TYPED_OUTPUT_OPCODES:
            continue
        if spec.category in GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES:
            continue
        if model and not spec.supports_cpu(model):
            continue
        result.append(mnemonic)
    return tuple(sorted(result))


def get_instruction_spec('''
if text.count(anchor) != 1:
    raise SystemExit("instruction registry anchor mismatch")
text = text.replace(anchor, block, 1)
old_all = '    "DEFAULT_INSTRUCTION_REGISTRY",\n    "InstructionCategory",\n'
new_all = (
    '    "DEFAULT_INSTRUCTION_REGISTRY",\n'
    '    "GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES",\n'
    '    "GENERATION_TYPED_OUTPUT_OPCODES",\n'
    '    "generation_app_instr_mnemonics",\n'
    '    "InstructionCategory",\n'
)
if text.count(old_all) != 1:
    raise SystemExit("instruction registry __all__ anchor mismatch")
registry.write_text(text.replace(old_all, new_all, 1), encoding="utf-8")

validator = Path("src/plc_json_validator.py")
text = validator.read_text(encoding="utf-8")
old_import = "from instruction_registry import DEFAULT_INSTRUCTION_REGISTRY, InstructionCategory\n"
new_import = '''from instruction_registry import (
    DEFAULT_INSTRUCTION_REGISTRY,
    GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES,
    GENERATION_TYPED_OUTPUT_OPCODES,
    generation_app_instr_mnemonics,
)
'''
if text.count(old_import) != 1:
    raise SystemExit("validator import anchor mismatch")
text = text.replace(old_import, new_import, 1)
old = '''_APP_INSTR_TYPED_ONLY = frozenset({"OUT", "PLS", "PLF", "END"})
_APP_INSTR_FORBIDDEN_CATEGORIES = frozenset(
    {InstructionCategory.CONDITION, InstructionCategory.BRANCH_CONTROL}
)
APP_INSTR_WHITELIST = frozenset(
    mnemonic
    for mnemonic in DEFAULT_INSTRUCTION_REGISTRY.known_mnemonics()
    if mnemonic not in _APP_INSTR_TYPED_ONLY
    and DEFAULT_INSTRUCTION_REGISTRY.category_of(mnemonic)
    not in _APP_INSTR_FORBIDDEN_CATEGORIES
)
'''
new = '''_APP_INSTR_TYPED_ONLY = GENERATION_TYPED_OUTPUT_OPCODES
_APP_INSTR_FORBIDDEN_CATEGORIES = GENERATION_FORBIDDEN_APP_INSTR_CATEGORIES
APP_INSTR_WHITELIST = frozenset(generation_app_instr_mnemonics())
'''
if text.count(old) != 1:
    raise SystemExit("validator APP_INSTR block mismatch")
validator.write_text(text.replace(old, new, 1), encoding="utf-8")

contract = Path("src/plc_generation_contract.py")
text = contract.read_text(encoding="utf-8")
import_anchor = "from typing import Any, Mapping\n\n\nMAX_LABEL_LEN"
import_repl = (
    "from typing import Any, Mapping\n\n"
    "from instruction_registry import GENERATION_TYPED_OUTPUT_OPCODES, generation_app_instr_mnemonics\n\n\n"
    "MAX_LABEL_LEN"
)
if text.count(import_anchor) != 1:
    raise SystemExit("generation contract import anchor mismatch")
text = text.replace(import_anchor, import_repl, 1)
text = text.replace("def ladder_v1_schema() -> dict:", "def ladder_v1_schema(plc_model=None) -> dict:", 1)
old_application = '''    application = _object({
        "type": {"enum": ["APP_INSTR"]},
        "opcode": {
            "type": "string", "minLength": 1, "maxLength": 64,
            "pattern": APP_INSTR_OPCODE_PATTERN,
            "not": {"enum": ["OUT", "PLS", "PLF", "END"]},
            "description": "One catalogued application opcode supported by the context PLC; no prose or operands here.",
        },
        "operands": _array(token), "label": label,
    }, ["type", "opcode", "operands"])
'''
new_application = '''    opcode_rule = {
        "type": "string", "minLength": 1, "maxLength": 64,
        "pattern": APP_INSTR_OPCODE_PATTERN,
        "description": "One catalogued application opcode supported by the selected PLC; no prose or operands here.",
    }
    if plc_model:
        opcode_rule["enum"] = list(generation_app_instr_mnemonics(plc_model))
    else:
        opcode_rule["not"] = {"enum": sorted(GENERATION_TYPED_OUTPUT_OPCODES)}
    application = _object({
        "type": {"enum": ["APP_INSTR"]},
        "opcode": opcode_rule,
        "operands": _array(token), "label": label,
    }, ["type", "opcode", "operands"])
'''
if text.count(old_application) != 1:
    raise SystemExit("generation contract application anchor mismatch")
text = text.replace(old_application, new_application, 1)
text = text.replace(
    "def generation_output_contract(*, allow_partial=False) -> dict:",
    "def generation_output_contract(*, allow_partial=False, plc_model=None) -> dict:",
    1,
)
text = text.replace(
    '"schema": ladder_response_schema(allow_partial=allow_partial),',
    '"schema": ladder_response_schema(allow_partial=allow_partial, plc_model=plc_model),',
    1,
)
text = text.replace(
    'def ladder_response_schema(*, allow_partial=False):\n    """Share the same generation schema with API prompts and external tools."""\n    full = ladder_v1_schema()\n',
    'def ladder_response_schema(*, allow_partial=False, plc_model=None):\n    """Share the same generation schema with API prompts and external tools."""\n    full = ladder_v1_schema(plc_model=plc_model)\n',
    1,
)
contract.write_text(text, encoding="utf-8")

replace_once(
    "src/plc_generation_context.py",
    "json.dumps(ladder_response_schema(allow_partial=is_edit_mode), ensure_ascii=False,\n",
    "json.dumps(ladder_response_schema(allow_partial=is_edit_mode, plc_model=selected_vendor), ensure_ascii=False,\n",
    "model-specific ladder schema",
)

Path("tests/test_generation_opcode_contract.py").write_text(
    '''from instruction_registry import generation_app_instr_mnemonics
from plc_generation_contract import ladder_response_schema
from plc_generation_context import _select_system_prompt


def _opcode_rule(schema):
    full = schema["oneOf"][0] if "oneOf" in schema else schema
    return (
        full["properties"]["rungs"]["items"]["properties"]["branches"]["items"]
        ["properties"]["outputs"]["items"]["oneOf"][2]["properties"]["opcode"]
    )


def test_model_specific_app_instr_enum_matches_registry():
    fx3 = tuple(generation_app_instr_mnemonics("FX3U"))
    fx5 = tuple(generation_app_instr_mnemonics("FX5U"))
    assert fx3 and fx5
    assert _opcode_rule(ladder_response_schema(plc_model="FX3U"))["enum"] == list(fx3)
    assert _opcode_rule(ladder_response_schema(plc_model="FX5U"))["enum"] == list(fx5)
    for forbidden in ("OUT", "PLS", "PLF", "END", "NOT_A_REAL_OPCODE"):
        assert forbidden not in fx3
        assert forbidden not in fx5
    assert set(fx3) != set(fx5), "CPU-specific catalogue must constrain at least one opcode"


def test_normal_generation_prompt_exposes_only_catalogued_fx3u_opcodes():
    prompt = _select_system_prompt("ladder", plc_model="FX3U")
    rule = _opcode_rule(ladder_response_schema(plc_model="FX3U"))
    assert '"enum":[' in prompt
    assert "NOT_A_REAL_OPCODE" not in prompt
    assert "OUT" not in rule["enum"]
    assert len(rule["enum"]) >= 10
''',
    encoding="utf-8",
)
