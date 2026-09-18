import pytest

from ui.desktop.main_window import _is_regenerate_locked_spec_request


@pytest.mark.parametrize(
    "text",
    [
        "重新生成",
        "请重新生成程序",
        "再次生成方案",
        "重试生成一次！",
        "重新按当前已确认规格生成",
    ],
)
def test_plain_regenerate_commands_reuse_locked_spec(text):
    assert _is_regenerate_locked_spec_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "重新生成，并把X3改成急停",
        "重新分析需求",
        "生成一个新的多段速方案",
        "把控制方式改成RS-485后重新生成",
    ],
)
def test_requirement_changes_are_not_treated_as_plain_regenerate_commands(text):
    assert not _is_regenerate_locked_spec_request(text)
