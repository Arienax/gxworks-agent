#!/usr/bin/env python3
from pathlib import Path

path = Path("src/main.py")
text = path.read_text(encoding="utf-8")
old = '''        allowed_rung_ids=None,\n        allowed_addresses=None,\n        image_attachments=None,\n'''
new = '''        allowed_rung_ids=None,\n        allowed_addresses=None,\n        repair_plan=None,\n        image_attachments=None,\n'''
if text.count(old) != 1:
    raise SystemExit(f"signature anchor count={text.count(old)}")
text = text.replace(old, new, 1)
old = '''        self.allowed_addresses = {\n            str(item).strip().upper()\n            for item in (allowed_addresses or [])\n            if str(item).strip()\n        }\n        self.image_attachments = tuple(image_attachments or ())\n'''
new = '''        self.allowed_addresses = {\n            str(item).strip().upper()\n            for item in (allowed_addresses or [])\n            if str(item).strip()\n        }\n        self.repair_plan = copy.deepcopy(repair_plan) if isinstance(repair_plan, dict) else None\n        self.image_attachments = tuple(image_attachments or ())\n'''
if text.count(old) != 1:
    raise SystemExit(f"init anchor count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
