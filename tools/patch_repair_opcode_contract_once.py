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
    "src/api.py",
    "from hardware_profiles import ensure_hardware_questions\n",
    "from hardware_profiles import ensure_hardware_questions\nfrom instruction_registry import GENERATION_TYPED_OUTPUT_OPCODES, generation_app_instr_mnemonics\n",
    "instruction registry import",
)

replace_once(
    "src/api.py",
    "- `device_comments` may contain only addresses listed in `allowed_addresses`.\n- Do not output markdown, explanation, diagnostics, or a full ladder program.\n",
    "- `device_comments` may contain only addresses listed in `allowed_addresses`.\n- For every `APP_INSTR`, `opcode` MUST be one exact value from\n  `repair_contract.app_instr_opcode_enum`; never invent, translate, or alias a mnemonic.\n- Values in `repair_contract.app_instr_forbidden_typed_opcodes` must NOT be emitted as\n  `APP_INSTR`; represent them with the dedicated output types listed in\n  `repair_contract.dedicated_output_types`.\n- Do not output markdown, explanation, diagnostics, or a full ladder program.\n",
    "partial repair opcode rules",
)

replace_once(
    "src/api.py",
    "    if not isinstance(repair_payload, dict):\n        raise TypeError(\"repair_payload must be an object\")\n    system_prompt = (PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT\n",
    "    if not isinstance(repair_payload, dict):\n        raise TypeError(\"repair_payload must be an object\")\n    repair_payload = dict(repair_payload)\n    if mode == \"partial\":\n        plc_model = str(repair_payload.get(\"plc_model\") or \"FX3U\").strip().upper() or \"FX3U\"\n        repair_payload[\"repair_contract\"] = {\n            \"plc_model\": plc_model,\n            \"app_instr_opcode_enum\": list(generation_app_instr_mnemonics(plc_model)),\n            \"app_instr_forbidden_typed_opcodes\": sorted(GENERATION_TYPED_OUTPUT_OPCODES),\n            \"dedicated_output_types\": [\"COIL\", \"PLS\", \"PLF\", \"TIMER\", \"COUNTER\"],\n        }\n    system_prompt = (PARTIAL_LADDER_REPAIR_SYSTEM_PROMPT\n",
    "repair payload contract",
)

# Lock the actual workbench repair request to the same registry-backed contract.
replace_once(
    "tests/test_user_confirmed_generation_repair.py",
    "        assert repair_payload[\"repair_mode\"] == \"partial\"\n        assert repair_payload[\"allowed_rung_ids\"] == [1]\n",
    "        assert repair_payload[\"repair_mode\"] == \"partial\"\n        contract = repair_payload[\"repair_contract\"]\n        assert contract[\"plc_model\"] == \"FX3U\"\n        assert \"MOV\" in contract[\"app_instr_opcode_enum\"]\n        assert \"NOT_A_REAL_OPCODE\" not in contract[\"app_instr_opcode_enum\"]\n        assert not set(contract[\"app_instr_forbidden_typed_opcodes\"]) & set(contract[\"app_instr_opcode_enum\"])\n        assert set(contract[\"app_instr_forbidden_typed_opcodes\"]) == {\"OUT\", \"PLS\", \"PLF\", \"END\"}\n        assert len(json.dumps(contract, ensure_ascii=False, separators=(\",\", \":\"))) < 3000\n        assert repair_payload[\"allowed_rung_ids\"] == [1]\n",
    "explicit repair contract assertion",
)

replace_once(
    "tests/test_user_confirmed_generation_repair.py",
    "        assert followup[\"repair_mode\"]==\"partial\"\n        assert followup[\"allowed_rung_ids\"]==[1]\n",
    "        assert followup[\"repair_mode\"]==\"partial\"\n        assert \"NOT_A_REAL_OPCODE\" not in followup[\"repair_contract\"][\"app_instr_opcode_enum\"]\n        assert \"MOV\" in followup[\"repair_contract\"][\"app_instr_opcode_enum\"]\n        assert followup[\"allowed_rung_ids\"]==[1]\n",
    "cascade repair contract assertion",
)
