"""Activity."""
from shared.i18n import runtime_text, tr
from ui.desktop.qt import QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel, QFrame
from ui.desktop.qt import Qt
from ui.desktop.qt import QFont, QTextCursor
from ui.desktop.icons import set_codicon

class ThinkingPanel(QFrame):
    """可折叠面板，实时显示厂商无关的工程推理摘要。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ThinkingPanel")
        self._expanded = False

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # ---- 头部栏 ----
        header_frame = QFrame()
        header_frame.setObjectName("ThinkingPanelHeader")
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(4, 2, 8, 2)

        self.toggle_btn = QPushButton(tr('推理详情'))
        self.toggle_btn.setObjectName("ThinkingPanelToggle")
        set_codicon(self.toggle_btn, "chevron-right", tr('推理详情'), 10)
        self.toggle_btn.setFixedHeight(34)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setToolTip(tr('展开或收起模型推理与生成日志'))
        self.toggle_btn.clicked.connect(self._toggle)

        self.status_label = QLabel(tr('等待中'))
        self.status_label.setObjectName("ThinkingStatus")

        header_layout.addWidget(self.toggle_btn)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)

        # ---- 内容区 ----
        self.content_edit = QTextEdit()
        self.content_edit.setObjectName("ThinkingPanelContent")
        self.content_edit.setReadOnly(True)
        self.content_edit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        font = QFont("Consolas", 10)
        font.setFamilies(["Consolas", "Courier New", "Microsoft YaHei"])
        self.content_edit.setFont(font)
        self.content_edit.setMinimumHeight(80)
        self.content_edit.setMaximumHeight(260)

        self.main_layout.addWidget(header_frame)
        self.main_layout.addWidget(self.content_edit)

        # 初始折叠
        self.content_edit.setVisible(False)
        self._expanded = False

    # ---------- 公开方法 ----------

    def append_reasoning(self, token: str):
        """追加推理文本片段并自动滚屏。"""
        from ui.desktop.qt import QTextCursor
        self.content_edit.moveCursor(QTextCursor.MoveOperation.End)
        self.content_edit.insertPlainText(token)
        scrollbar = self.content_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def append_content(self, token: str):
        """追加输出内容片段（与推理区分，灰色前缀）。"""
        from ui.desktop.qt import QTextCursor
        self.content_edit.moveCursor(QTextCursor.MoveOperation.End)
        self.content_edit.insertPlainText(token)
        scrollbar = self.content_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_status(self, text: str):
        self.flush_display()
        self.status_label.setText(runtime_text(text))

    def flush_display(self):
        # Model content has already passed the shared acceptance boundary.
        pass

    def reset(self):
        """清空内容、重置标题与状态。"""
        self.content_edit.clear()
        self.status_label.setText(tr('思考中...'))
        set_codicon(self.toggle_btn, "chevron-down", tr('推理详情'), 10)
        if not self._expanded:
            self._expand()

    def show_error(self, msg: str):
        """追加错误信息（红色提示）。"""
        from ui.desktop.qt import QTextCursor
        self.content_edit.moveCursor(QTextCursor.MoveOperation.End)
        self.flush_display()
        self.content_edit.insertPlainText(f"\n---\n⚠ {runtime_text(msg)}\n")
        self.status_label.setText(tr('出错'))

    # ---------- 折叠控制 ----------

    def _toggle(self):
        if self._expanded:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        self.content_edit.setVisible(True)
        set_codicon(self.toggle_btn, "chevron-down", tr('推理详情'), 10)
        self._expanded = True

    def _collapse(self):
        self.content_edit.setVisible(False)
        set_codicon(self.toggle_btn, "chevron-right", tr('推理详情'), 10)
        self._expanded = False

