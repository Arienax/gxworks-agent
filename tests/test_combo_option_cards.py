import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.desktop.qt import QApplication
from ui.desktop.controls import (
    BorderedComboBox,
    OPTION_SUBTITLE_ROLE,
    split_option_card_text,
)


_APPLICATION = QApplication.instance() or QApplication([])


def _app():
    return _APPLICATION


def test_option_text_is_split_into_title_and_detail():
    assert split_option_card_text(
        "模拟量输出（0-10V或4-20mA）"
    ) == ("模拟量输出", "0-10V或4-20mA")
    assert split_option_card_text(
        "RS485通讯（Modbus）"
    ) == ("RS485通讯", "Modbus")
    assert split_option_card_text("用户自定义") == ("用户自定义", "")


def test_card_combo_displays_short_title_but_keeps_canonical_value():
    _app()
    combo = BorderedComboBox()
    combo.setEditable(True)
    combo.enableOptionCards(360)
    canonical = "高速脉冲频率给定（需晶体管输出及变频器支持）"
    combo.addOptionCard(canonical)
    combo.setCanonicalText(canonical)

    assert combo.currentText() == "高速脉冲频率给定"
    assert combo.canonicalText() == canonical
    assert combo.itemData(0, OPTION_SUBTITLE_ROLE) == "需晶体管输出及变频器支持"


def test_card_combo_preserves_custom_manual_values():
    _app()
    combo = BorderedComboBox()
    combo.setEditable(True)
    combo.enableOptionCards(360)
    combo.addOptionCard("RS485通讯（Modbus）")
    combo.setCanonicalText("用户指定的通讯方式")

    assert combo.currentIndex() == -1
    assert combo.currentText() == "用户指定的通讯方式"
    assert combo.canonicalText() == "用户指定的通讯方式"


def test_card_popup_uses_restrained_width():
    app = _app()
    combo = BorderedComboBox()
    combo.resize(180, 30)
    combo.enableOptionCards(360)
    combo.addOptionCard("Y输出多段速端子（STF/RH/RM/RL，固定档位优先）")
    combo.show()
    combo.showPopup()
    app.processEvents()

    assert combo.view().width() == 360
    combo.hidePopup()
    combo.close()
