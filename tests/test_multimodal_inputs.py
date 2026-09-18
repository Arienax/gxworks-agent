import base64
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import application.model_api as api
import ui.desktop.main_window as main
from model_runtime.provider import ImageAttachment, UserMessage
from ui.desktop.qt import QApplication
from storage.session import SessionStore


_APPLICATION = QApplication.instance() or QApplication([])
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_project_image_attachment_is_copied_and_can_be_reloaded(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("视觉需求")
    source = tmp_path / "接线图.renamed"
    source.write_bytes(_PNG_BYTES)

    records = store.import_image_attachments(project["id"], [source])
    source.unlink()

    assert len(records) == 1
    assert records[0]["filename"] == "接线图.renamed"
    assert records[0]["media_type"] == "image/png"
    assert not os.path.isabs(records[0]["stored_name"])
    assert store.load_image_attachment(project["id"], records[0]) == _PNG_BYTES


def test_image_attachment_rejects_unsupported_actual_file_content(tmp_path):
    store = SessionStore(base_dir=tmp_path / "workspace", legacy_dir=tmp_path)
    project = store.create_project("视觉需求")
    source = tmp_path / "伪装图片.png"
    source.write_bytes(b"not-an-image")

    try:
        store.import_image_attachments(project["id"], [source])
    except ValueError as error:
        assert "仅支持 JPEG、PNG、GIF、WebP" in str(error)
    else:
        raise AssertionError("invalid image content must be rejected")


def test_generation_preparation_attaches_images_only_to_current_user_message(
    monkeypatch,
):
    image = ImageAttachment("控制图.png", "image/png", _PNG_BYTES)
    monkeypatch.setattr(api, "_resolve_plc_model", lambda *_args, **_kwargs: "FX3U")
    monkeypatch.setattr(api, "_select_system_prompt", lambda *_args, **_kwargs: "system")
    monkeypatch.setattr(api, "_build_model_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(api, "_build_knowledge_context", lambda *_args, **_kwargs: "")

    messages, history, _persist = api._prepare_api_call(
        "根据图片生成控制方案",
        "fixture-model",
        "high",
        "ladder",
        conversation_history=[{"role": "assistant", "content": "上一轮"}],
        image_attachments=(image,),
    )

    assert isinstance(messages[-1], UserMessage)
    assert messages[-1].content == "根据图片生成控制方案"
    assert messages[-1].images == (image,)
    assert messages[0]["role"] == "system"
    assert isinstance(history[-1]["content"], str)
    assert "images" not in history[-1]


def test_workbench_shows_and_persists_selected_image(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    real_store = SessionStore(base_dir=workspace, legacy_dir=tmp_path)
    project = real_store.create_project("视觉需求")
    monkeypatch.setattr(
        main,
        "SessionStore",
        lambda *args, **kwargs: SessionStore(
            base_dir=workspace,
            legacy_dir=tmp_path,
        ),
    )
    monkeypatch.setattr(
        main,
        "get_model_profile",
        lambda *_args, **_kwargs: {
            "model": "deepseek-v4-flash-vision-exp",
            "capabilities": {"multimodal": True},
        },
    )
    source = tmp_path / "控制柜.png"
    source.write_bytes(_PNG_BYTES)

    window = main._IndustrialWorkbenchUI()
    window._refresh_projects(project["id"])
    assert window._add_composer_image_paths([source]) is True
    assert not window.image_attachment_scroll.isHidden()

    records, images = window._persist_composer_images(project["id"])

    assert records[0]["filename"] == "控制柜.png"
    assert images[0].media_type == "image/png"
    assert images[0].data == _PNG_BYTES
    window.close()
