"""Messages."""
from shared.i18n import tr
from ui.desktop.qt import Qt
from ui.desktop.qt import QFrame, QLabel, QVBoxLayout
from shared.display_names import naturalize_display_text, version_display_name

class MessageBubble(QFrame):
    def __init__(self, role, content, kind="message", metadata=None, parent=None):
        super().__init__(parent)
        self.setObjectName(
            "UserMessage" if role == "user" else "AssistantMessage"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(5)

        title_text = tr('你') if role == "user" else "PLC AI"
        if kind == "system":
            title_text = tr('系统')
        elif kind == "agent":
            title_text = tr('PLC AI · 工具')
        title = QLabel(title_text)
        title.setObjectName("MessageAuthor")
        # User text and accepted model text are evidence. Only application-owned
        # system messages may use the operator-friendly identifier renderer.
        body = QLabel(naturalize_display_text(content) if kind == "system" else str(content or ""))
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setObjectName("MessageBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(title)
        layout.addWidget(body)

        metadata = metadata or {}
        image_attachments = [
            item
            for item in (metadata.get("image_attachments") or [])
            if isinstance(item, dict)
        ]
        if image_attachments:
            names = [
                str(item.get("filename") or tr('图片'))
                for item in image_attachments
            ]
            attachment_label = QLabel(
                tr('已附加 {v0} 张图片：', v0=len(names)) + "、".join(names)
            )
            attachment_label.setObjectName("MessageMeta")
            attachment_label.setWordWrap(True)
            attachment_label.setToolTip("\n".join(names))
            layout.addWidget(attachment_label)
        version_id = metadata.get("version_id")
        if version_id:
            version = QLabel(tr('生成{v0}', v0=version_display_name(version_id)))
            version.setObjectName("MessageMeta")
            layout.addWidget(version)

