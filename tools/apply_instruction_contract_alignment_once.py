#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new, label):
    p=Path(path); text=p.read_text(encoding='utf-8'); count=text.count(old)
    if count != 1: raise SystemExit(f'{label}: expected 1 anchor, found {count}')
    p.write_text(text.replace(old,new,1),encoding='utf-8')

# Explicitly distinguish complete semantic contracts from opcode-identity-only
# coverage derived from manual evidence.
replace_once('src/instruction_registry.py',
'''    cpu_support: frozenset[str] = field(default_factory=frozenset)\n    notes: str = ""\n''',
'''    cpu_support: frozenset[str] = field(default_factory=frozenset)\n    contract_level: str = "full"\n    notes: str = ""\n''','registry contract field')
replace_once('src/instruction_registry.py',
'''        try:\n            semantic_kind = SemanticKind(semantic_text)\n        except ValueError as exc:\n            raise ValueError(\n                f"{mnemonic}: invalid semantic kind {semantic_text!r}"\n            ) from exc\n\n        arity = payload.get("arity") or {}\n''',
'''        try:\n            semantic_kind = SemanticKind(semantic_text)\n        except ValueError as exc:\n            raise ValueError(\n                f"{mnemonic}: invalid semantic kind {semantic_text!r}"\n            ) from exc\n        contract_level = str(payload.get("contract_level") or "full").strip().lower()\n        if contract_level not in {"full", "opcode_only"}:\n            raise ValueError(f"{mnemonic}: invalid contract_level {contract_level!r}")\n\n        arity = payload.get("arity") or {}\n''','registry contract parse')
replace_once('src/instruction_registry.py',
'''            cpu_support=frozenset(\n                str(item).strip().upper()\n                for item in (payload.get("cpu_support") or [])\n                if str(item).strip()\n            ),\n            notes=str(payload.get("notes") or "").strip(),\n''',
'''            cpu_support=frozenset(\n                str(item).strip().upper()\n                for item in (payload.get("cpu_support") or [])\n                if str(item).strip()\n            ),\n            contract_level=contract_level,\n            notes=str(payload.get("notes") or "").strip(),\n''','registry contract construct')
replace_once('src/instruction_registry.py',
'''def load_default_instruction_registry() -> InstructionRegistry:\n    required = ("common.json", "fx3u.json", "fx5u.json")\n    for directory in _candidate_catalog_directories():\n        paths = tuple(directory / name for name in required)\n        if all(path.is_file() for path in paths):\n            return InstructionRegistry.from_files(paths)\n''',
'''def load_default_instruction_registry() -> InstructionRegistry:\n    required = ("common.json", "fx3u.json", "fx5u.json")\n    for directory in _candidate_catalog_directories():\n        paths = tuple(directory / name for name in required)\n        if all(path.is_file() for path in paths):\n            verified = directory / "fx3u_verified_opcodes.json"\n            if verified.is_file():\n                paths = paths + (verified,)\n            return InstructionRegistry.from_files(paths)\n''','registry overlay loading')

# A field-level repair must not guess a replacement from an opcode-only entry,
# because its operand signature has intentionally not been hardened.
replace_once('src/application/field_repair.py',
'''            spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic)\n            if spec is not None and spec.accepts_arity(len(operands)):\n                allowed.append(mnemonic)\n''',
'''            spec = DEFAULT_INSTRUCTION_REGISTRY.resolve(mnemonic)\n            if (\n                spec is not None\n                and spec.contract_level == "full"\n                and spec.accepts_arity(len(operands))\n            ):\n                allowed.append(mnemonic)\n''','field repair full contract only')

# Only the cross-manual contradictions found by the audited DB/registry check
# receive a precedence rule.  Do not globally rerank structured instructions:
# that would disturb hybrid BM25/vector/debug retrieval for unrelated opcodes.
replace_once('src/knowledge_retriever_core.py',
'''    candidates.sort(\n        key=lambda item: (\n            -float(item.get("score", 0.0)),\n            -int(item.get("manual_priority", 0) or 0),\n            int(item.get("pdf_page", 0) or 0),\n            str(item.get("id", "")),\n        )\n    )\n    return _select_with_budget(candidates, top_k, char_budget)\n''',
'''    candidates.sort(\n        key=lambda item: (\n            -float(item.get("score", 0.0)),\n            -int(item.get("manual_priority", 0) or 0),\n            int(item.get("pdf_page", 0) or 0),\n            str(item.get("id", "")),\n        )\n    )\n\n    audited_instruction_precedence = {\n        "drva": "fx3_positioning_k",\n        "drvi": "fx3_positioning_k",\n        "dvit": "fx3_positioning_k",\n        "plsv": "fx3_positioning_k",\n        "zrn": "fx3_positioning_k",\n        "tbl": "fx3_programming_r",\n    }\n    if query_term_set & set(audited_instruction_precedence):\n        preferred = {}\n        for candidate in candidates:\n            opcode = _normalize_text(candidate.get("instruction_opcode", "")).casefold()\n            chunk_type = _normalize_text(candidate.get("chunk_type", "")).casefold()\n            if chunk_type != "instruction" or opcode not in query_term_set:\n                continue\n            expected_manual = audited_instruction_precedence.get(opcode)\n            if expected_manual and candidate.get("manual_id") == expected_manual and opcode not in preferred:\n                preferred[opcode] = candidate\n\n        if preferred:\n            deduped = []\n            emitted = set()\n            for candidate in candidates:\n                opcode = _normalize_text(candidate.get("instruction_opcode", "")).casefold()\n                chunk_type = _normalize_text(candidate.get("chunk_type", "")).casefold()\n                if opcode in preferred and chunk_type == "instruction":\n                    if opcode in emitted:\n                        continue\n                    deduped.append(preferred[opcode])\n                    emitted.add(opcode)\n                    continue\n                deduped.append(candidate)\n            candidates = deduped\n\n    return _select_with_budget(candidates, top_k, char_budget)\n''','retriever audited opcode precedence')
