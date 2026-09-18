import copy
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ui.desktop.dialogs.config as config_dialog
from storage.config import DEFAULT_MODEL_PROFILES
from storage.credentials import credential_target_for_profile
from ui.desktop.qt import QApplication, QDialog, QMessageBox


_APPLICATION = QApplication.instance() or QApplication([])


def _config_with(*extra_profiles, active="deepseek-default"):
    return {
        "activeModelProfileId": active,
        "modelProfiles": [
            *copy.deepcopy(list(DEFAULT_MODEL_PROFILES)),
            *copy.deepcopy(list(extra_profiles)),
        ],
    }


def test_user_can_add_and_save_custom_openai_compatible_profile(monkeypatch):
    saved = []
    written_keys = []
    monkeypatch.setattr(
        config_dialog, "load_full_config", lambda: _config_with()
    )
    monkeypatch.setattr(config_dialog, "get_api_key", lambda *_args: "")
    monkeypatch.setattr(
        config_dialog.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("本地模型", True),
    )
    monkeypatch.setattr(
        config_dialog, "save_config", lambda value: saved.append(copy.deepcopy(value))
    )
    monkeypatch.setattr(
        config_dialog,
        "write_api_key",
        lambda value, target: written_keys.append((target, value)),
    )

    dialog = config_dialog.RequestTemplateConfigDialog()
    dialog._add_custom_profile()

    assert dialog._current_profile_id == "custom-1"
    assert dialog._profile_drafts["custom-1"]["name"] == "本地模型"
    assert dialog.delete_profile_btn.isEnabled()
    dialog.url_input.setText("https://models.example.invalid/v1")
    assert dialog.provider_combo.currentData() == "custom"
    assert dialog.preset_combo.isEditable()
    dialog.preset_combo.setEditText("example-model")
    dialog.api_key_input.setText("custom-key")
    dialog._api_key_edited()
    dialog._on_save()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert saved[0]["activeModelProfileId"] == "custom-1"
    custom = next(
        item for item in saved[0]["modelProfiles"] if item["id"] == "custom-1"
    )
    assert custom["baseUrl"] == "https://models.example.invalid/v1"
    assert custom["model"] == "example-model"
    assert written_keys == [
        (credential_target_for_profile("custom-1"), "custom-key")
    ]


def test_user_can_delete_only_custom_profile_and_its_saved_key(monkeypatch):
    custom = {
        "id": "custom-7",
        "name": "待删除模型",
        "adapter": "openai_compatible",
        "baseUrl": "https://models.example.invalid/v1",
        "model": "old-model",
        "capabilities": {},
        "generationDefaults": {},
        "requestOverrides": {},
        "credentialTarget": credential_target_for_profile("custom-7"),
    }
    saved = []
    deleted_targets = []
    monkeypatch.setattr(
        config_dialog,
        "load_full_config",
        lambda: _config_with(custom, active="custom-7"),
    )
    monkeypatch.setattr(config_dialog, "get_api_key", lambda *_args: "stored-key")
    monkeypatch.setattr(
        config_dialog.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        config_dialog, "save_config", lambda value: saved.append(copy.deepcopy(value))
    )
    monkeypatch.setattr(
        config_dialog,
        "delete_api_key",
        lambda target: deleted_targets.append(target),
    )

    dialog = config_dialog.RequestTemplateConfigDialog()
    assert dialog.delete_profile_btn.isEnabled()
    dialog._delete_current_profile()

    assert "custom-7" not in dialog._profile_drafts
    assert dialog._current_profile_id in {
        profile["id"] for profile in DEFAULT_MODEL_PROFILES
    }
    assert not dialog.delete_profile_btn.isEnabled()
    dialog._on_save()

    assert all(
        profile["id"] != "custom-7" for profile in saved[0]["modelProfiles"]
    )
    assert deleted_targets == [credential_target_for_profile("custom-7")]


def test_basic_settings_split_provider_and_concrete_model(monkeypatch):
    monkeypatch.setattr(
        config_dialog, "load_full_config", lambda: _config_with()
    )
    monkeypatch.setattr(config_dialog, "get_api_key", lambda *_args: "")

    dialog = config_dialog.RequestTemplateConfigDialog()

    assert [
        dialog.provider_combo.itemData(index)
        for index in range(dialog.provider_combo.count())
    ] == ["deepseek", "zhipu"]
    assert dialog.preset_combo.currentText() == "deepseek-v4-pro"
    assert [
        dialog.preset_combo.itemText(index)
        for index in range(dialog.preset_combo.count())
    ] == [
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "deepseek-v4-flash-vision-exp",
    ]
    dialog.preset_combo.setCurrentIndex(
        dialog.preset_combo.findData("deepseek-v4-flash-vision-exp")
    )
    assert "支持文字与图片输入" in dialog.provider_label.text()

    dialog.provider_combo.setCurrentIndex(
        dialog.provider_combo.findData("zhipu")
    )
    assert [
        dialog.preset_combo.itemText(index)
        for index in range(dialog.preset_combo.count())
    ] == ["glm-5.3-flash", "glm-5.3", "glm-5.2"]
    dialog.preset_combo.setCurrentIndex(
        dialog.preset_combo.findData("zhipu-glm-5.2")
    )
    assert dialog._current_profile_id == "zhipu-glm-5.2"
    assert not dialog.preset_combo.isEditable()


def test_advanced_sliders_save_generation_defaults_for_selected_profile(
    monkeypatch,
):
    saved = []
    monkeypatch.setattr(
        config_dialog,
        "load_full_config",
        lambda: _config_with(active="zhipu-glm-5.3-flash"),
    )
    monkeypatch.setattr(
        config_dialog, "get_api_key", lambda *_args: "stored-key"
    )
    monkeypatch.setattr(
        config_dialog,
        "save_config",
        lambda value: saved.append(copy.deepcopy(value)),
    )

    dialog = config_dialog.RequestTemplateConfigDialog()
    assert dialog.temperature_value_label.text() == "1.00"
    assert dialog.top_p_value_label.text() == "0.95"
    assert dialog.reasoning_effort_value_label.text() == "最高"

    dialog.temperature_slider.setValue(7)
    dialog.top_p_slider.setValue(82)
    dialog.reasoning_effort_slider.setValue(3)
    dialog._on_save()

    selected = next(
        profile
        for profile in saved[0]["modelProfiles"]
        if profile["id"] == "zhipu-glm-5.3-flash"
    )
    defaults = selected["generationDefaults"]
    assert defaults["temperature"] == 0.35
    assert defaults["top_p"] == 0.82
    assert defaults["reasoning_effort"] == "high"
    assert defaults["response_format"] == {"type": "json_object"}


def test_main_window_no_longer_overrides_profile_reasoning_effort():
    source = (Path(__file__).parents[1] / "src" / "ui/desktop/main_window.py").read_text(
        encoding="utf-8"
    )
    assert "effort_combo" not in source
    assert "mode_btn" not in source
    assert 'project.get("effort"' not in source
