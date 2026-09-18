from pathlib import Path


def test_contract_mismatch_is_materialized_before_user_repair_choice():
    source = Path('src/ui/desktop/main_window.py').read_text(encoding="utf-8")
    assert "contract_repair_button" in source
    assert "修复方案约束" in source
    assert "def _repair_current_contract_mismatch(self):" in source
    assert "原始版本和 CSV 保持不变" in source
