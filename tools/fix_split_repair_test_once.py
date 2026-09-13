#!/usr/bin/env python3
from pathlib import Path
p = Path('tests/test_user_confirmed_generation_repair.py')
text = p.read_text(encoding='utf-8')
old = '''        prompt = str(provider.requests[2].messages[-1].content)
        assert "上一次局部修复回复仍未通过校验" in prompt
        assert "上一次失败的局部 patch" in prompt
        assert 'mode="partial"' in prompt
        assert service.projects.project(project)["version_count"] == 1
'''
new = '''        system_prompt = str(provider.requests[2].messages[0].content)
        retry_payload = json.loads(str(provider.requests[2].messages[-1].content))
        assert "PLC ladder local structural repair" in system_prompt
        assert retry_payload["repair_mode"] == "partial"
        assert retry_payload["allowed_rung_ids"] == [1]
        assert "上一次局部修复回复仍未通过校验" in retry_payload["instruction"]
        assert "上一次失败的局部 patch" in retry_payload["instruction"]
        assert service.projects.project(project)["version_count"] == 1
'''
if text.count(old) != 1:
    raise SystemExit('retry assertion anchor missing or non-unique')
p.write_text(text.replace(old, new, 1), encoding='utf-8')
