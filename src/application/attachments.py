"""Validate and store project image attachments."""
from __future__ import annotations
import base64
import tempfile
from pathlib import Path
from application.projects import contained, record_id
from application.workspace import atomic_json, read_json

class AttachmentService:
    """Managed image upload and model-attachment loading."""

    def __init__(self, *,
                 projects,
                 state_dir,
                 lock,
                 require_writable,
                 ):
        self.projects = projects
        self.state_dir = state_dir
        self.lock = lock
        self.require_writable = require_writable
        self.store = projects.store

    def upload(self, project_id, filename, data_base64):
        from storage.session import detect_image_media_type
        self.require_writable()
        self.projects.raw_project(project_id)
        data = base64.b64decode(data_base64, validate=True)
        if not data or len(data) > 30 * 1024 * 1024 or not detect_image_media_type(data):
            raise ValueError("请添加总计不超过 30 MiB 的 JPEG、PNG、GIF 或 WebP 图片。")
        with self.lock.thread_lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.state_dir, prefix="upload-") as directory:
                path = Path(directory) / (Path(filename.replace("\\", "/")).name or "image")
                path.write_bytes(data)
                record = self.store.import_image_attachments(project_id, [path])[0]
            atomic_json(self.state_dir / "attachments" / (record["attachment_id"] + ".json"),
                        {"project_id": project_id, "record": record})
            return {key: record[key] for key in ("attachment_id", "filename", "media_type", "size_bytes")}

    def load(self, project_id, ids):
        from model_runtime.provider import ImageAttachment
        attachments = []
        for attachment_id in ids:
            path = contained(self.state_dir / "attachments" / (record_id(attachment_id) + ".json"), self.state_dir)
            saved = read_json(path)
            if saved["project_id"] != project_id:
                raise ValueError("Attachment belongs to another project")
            record = saved["record"]
            data = self.store.load_image_attachment(project_id, record)
            attachments.append(ImageAttachment(record["filename"], record["media_type"], data))
        if sum(len(item.data) for item in attachments) > 30 * 1024 * 1024:
            raise ValueError("Attachments exceed 30 MiB")
        return attachments
