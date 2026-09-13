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
    "resources/instructions/mitsubishi/common.json",
    '''    {"mnemonic":"SET","canonical_op":"SET","category":"action","semantic_kind":"function","operands":[{"name":"destination","role":"write"}]},\n    {"mnemonic":"RST","canonical_op":"RESET","category":"action","semantic_kind":"function","operands":[{"name":"destination","role":"write"}]},\n''',
    '''    {"mnemonic":"SET","canonical_op":"SET","category":"action","semantic_kind":"function","arity":{"min":1,"max":1},"operands":[{"name":"destination","role":"write"}]},\n    {"mnemonic":"RST","canonical_op":"RESET","category":"action","semantic_kind":"function","arity":{"min":1,"max":1},"operands":[{"name":"destination","role":"write"}]},\n''',
    "SET/RST arity",
)

replace_once(
    "resources/instructions/mitsubishi/fx3u.json",
    '''      "notes": "FX5U projects should use the model-appropriate homing instruction such as DSZR."\n    }\n''',
    '''      "notes": "FX5U projects should use the model-appropriate homing instruction such as DSZR."\n    },\n    {\n      "mnemonic": "ZRST",\n      "canonical_op": "ZONE_RESET",\n      "category": "action",\n      "semantic_kind": "function",\n      "cpu_support": ["FX3U"],\n      "arity": {"min": 2, "max": 2},\n      "operands": [\n        {"name": "start", "role": "write", "device_prefixes": ["Y", "M", "S", "T", "C", "D"]},\n        {"name": "end", "role": "write", "device_prefixes": ["Y", "M", "S", "T", "C", "D"]}\n      ],\n      "notes": "FNC 40 zone reset. Resets the inclusive range between two like devices."\n    }\n''',
    "FX3U ZRST entry",
)
