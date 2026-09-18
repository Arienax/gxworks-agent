from ui.desktop.qt import QPointF, QSize, Qt
from ui.desktop.qt import QColor, QPainter, QPalette, QPen
from ui.desktop.qt import QComboBox, QStyle, QStyledItemDelegate
from shared.display_names import naturalize_display_text


OPTION_VALUE_ROLE = int(Qt.ItemDataRole.UserRole)
OPTION_SUBTITLE_ROLE = OPTION_VALUE_ROLE + 1
_TOOLTIP_ROLE = getattr(Qt.ItemDataRole, "ToolTipRole", 3)


def split_option_card_text(value):
    """Split a canonical option into a compact title and supporting detail."""
    text = str(value or "").strip()
    for opening, closing in (("（", "）"), ("(", ")")):
        if not text.endswith(closing):
            continue
        opening_index = text.rfind(opening)
        if opening_index <= 0:
            continue
        title = text[:opening_index].strip()
        subtitle = text[opening_index + 1:-1].strip()
        if title and subtitle:
            return title, subtitle
    return text, ""


def _style_state(name):
    namespace = getattr(QStyle, "StateFlag", QStyle)
    return getattr(namespace, name)


class ComboOptionCardDelegate(QStyledItemDelegate):
    """Paint combo-box choices as compact two-line VS Code-style cards."""

    def __init__(self, combo, parent=None):
        super().__init__(parent or combo.view())
        self.combo = combo

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), 48)

    def paint(self, painter, option, index):
        painter.save()
        rect = option.rect.adjusted(4, 2, -4, -2)
        selected = bool(option.state & _style_state("State_Selected"))
        hovered = bool(option.state & _style_state("State_MouseOver"))
        dark_theme = bool(self.combo.property("darkTheme"))

        if dark_theme:
            background = "#04395e" if selected else ("#2a2d2e" if hovered else "#252526")
            title_color = "#ffffff" if selected else "#cccccc"
            subtitle_color = "#b9d9f3" if selected else "#9d9d9d"
        else:
            background = "#cde8ff" if selected else ("#e5f1fb" if hovered else "#ffffff")
            title_color = "#1e1e1e"
            subtitle_color = "#245b85" if selected else "#616161"

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(rect, 4, 4)
        if selected:
            accent_rect = rect.adjusted(0, 5, -(rect.width() - 3), -5)
            painter.setBrush(QColor("#0078d4"))
            painter.drawRoundedRect(accent_rect, 1.5, 1.5)

        title = str(index.data() or "")
        subtitle = str(index.data(OPTION_SUBTITLE_ROLE) or "")
        text_rect = rect.adjusted(12, 0, -10, 0)

        title_font = option.font
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(title_color))
        title_metrics = painter.fontMetrics()
        title = title_metrics.elidedText(
            title, Qt.TextElideMode.ElideRight, text_rect.width()
        )

        if subtitle:
            painter.drawText(
                text_rect.left(),
                rect.top() + 5 + title_metrics.ascent(),
                title,
            )
            subtitle_font = option.font
            if subtitle_font.pointSizeF() > 1:
                subtitle_font.setPointSizeF(max(8.0, subtitle_font.pointSizeF() - 1.0))
            subtitle_font.setBold(False)
            painter.setFont(subtitle_font)
            painter.setPen(QColor(subtitle_color))
            subtitle_metrics = painter.fontMetrics()
            subtitle = subtitle_metrics.elidedText(
                subtitle, Qt.TextElideMode.ElideRight, text_rect.width()
            )
            painter.drawText(
                text_rect.left(),
                rect.top() + 25 + subtitle_metrics.ascent(),
                subtitle,
            )
        else:
            baseline = (rect.height() + title_metrics.ascent() - title_metrics.descent()) // 2
            painter.drawText(text_rect.left(), rect.top() + baseline, title)
        painter.restore()


class BorderedComboBox(QComboBox):
    """Workbench combo box with a crisp, theme-independent chevron."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_open = False
        self._option_card_width = None
        self._option_card_delegate = None
        self.setProperty("popupOpen", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        popup_palette = self.view().palette()
        popup_palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        popup_palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
        popup_palette.setColor(QPalette.ColorRole.Text, QColor("#1f2937"))
        popup_palette.setColor(QPalette.ColorRole.WindowText, QColor("#1f2937"))
        popup_palette.setColor(QPalette.ColorRole.Highlight, QColor("#dbeafe"))
        popup_palette.setColor(
            QPalette.ColorRole.HighlightedText, QColor("#1d4ed8")
        )
        self.view().setPalette(popup_palette)

    def enableOptionCards(self, width=360):
        """Use a restrained two-line popup without changing canonical values."""
        self._option_card_width = max(280, int(width))
        self._option_card_delegate = ComboOptionCardDelegate(self, self.view())
        self.view().setItemDelegate(self._option_card_delegate)
        self.view().setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        if hasattr(self.view(), "setSpacing"):
            self.view().setSpacing(1)

    def addOptionCard(self, value, title=None, subtitle=None):
        canonical_value = str(value or "").strip()
        if title is None or subtitle is None:
            parsed_title, parsed_subtitle = split_option_card_text(canonical_value)
            title = parsed_title if title is None else title
            subtitle = parsed_subtitle if subtitle is None else subtitle
        display_title = naturalize_display_text(title or canonical_value)
        display_subtitle = naturalize_display_text(subtitle or "")
        self.addItem(display_title)
        index = self.count() - 1
        self.setItemData(index, canonical_value, OPTION_VALUE_ROLE)
        self.setItemData(index, display_subtitle, OPTION_SUBTITLE_ROLE)
        self.setItemData(index, canonical_value, _TOOLTIP_ROLE)

    def setCanonicalText(self, value):
        canonical_value = str(value or "").strip()
        for index in range(self.count()):
            if str(self.itemData(index, OPTION_VALUE_ROLE) or "") == canonical_value:
                self.setCurrentIndex(index)
                return
        self.setCurrentIndex(-1)
        if self.isEditable():
            self.setEditText(canonical_value)

    def canonicalText(self):
        index = self.currentIndex()
        if index >= 0 and self.currentText() == self.itemText(index):
            value = self.itemData(index, OPTION_VALUE_ROLE)
            if value is not None:
                return str(value).strip()
        return self.currentText().strip()

    def showPopup(self):
        self._popup_open = True
        self.setProperty("popupOpen", True)
        self._refresh_style()
        if self._option_card_width:
            screen = self.screen()
            available_width = (
                screen.availableGeometry().width() if screen is not None else 0
            )
            maximum_width = max(self.width(), available_width - 24) if available_width else self._option_card_width
            popup_width = min(max(self.width(), self._option_card_width), maximum_width)
            self.view().setMinimumWidth(popup_width)
            self.view().setMaximumWidth(popup_width)
        super().showPopup()

    def hidePopup(self):
        # Hide the native popup surface before changing the dynamic style.
        # Re-polishing a still-visible popup can expose an empty backing surface
        # for one frame on Windows.
        super().hidePopup()
        self._popup_open = False
        self.setProperty("popupOpen", False)
        self._refresh_style()

    def _refresh_style(self):
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        dark_theme = bool(self.property("darkTheme"))
        if dark_theme:
            divider_color = "#0078d4" if self._popup_open else "#4a4a4a"
            arrow_color = (
                "#75beff"
                if self._popup_open or self.hasFocus()
                else "#cccccc"
            )
        else:
            divider_color = "#bfdbfe" if self._popup_open else "#e2e8f0"
            arrow_color = (
                "#2563eb"
                if self._popup_open or self.hasFocus()
                else "#475569"
            )
        divider_pen = QPen(QColor(divider_color), 1)
        painter.setPen(divider_pen)
        divider_x = self.width() - 32
        painter.drawLine(
            QPointF(divider_x, 7),
            QPointF(divider_x, self.height() - 7),
        )

        arrow_pen = QPen(QColor(arrow_color), 1.8)
        arrow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        arrow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(arrow_pen)

        center_x = self.width() - 16
        center_y = self.height() / 2
        if self._popup_open:
            painter.drawLine(
                QPointF(center_x - 4, center_y + 2),
                QPointF(center_x, center_y - 2),
            )
            painter.drawLine(
                QPointF(center_x, center_y - 2),
                QPointF(center_x + 4, center_y + 2),
            )
        else:
            painter.drawLine(
                QPointF(center_x - 4, center_y - 2),
                QPointF(center_x, center_y + 2),
            )
            painter.drawLine(
                QPointF(center_x, center_y + 2),
                QPointF(center_x + 4, center_y - 2),
            )
        painter.end()
