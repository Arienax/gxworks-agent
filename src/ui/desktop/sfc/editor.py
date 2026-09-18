"""SFC editor with alternating steps and transition conditions."""
from shared.i18n import tr
import json, re, random
from typing import Optional
from ui.desktop.qt import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QComboBox, QGraphicsView,
    QGraphicsScene, QGraphicsItem, QGraphicsRectItem,
    QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsTextItem,
    QMenu, QToolBar, QSplitter, QDialog, QFileDialog,
    QFormLayout, QLineEdit, QDialogButtonBox, QTextEdit, QGroupBox,
    QApplication,
)
from ui.desktop.qt import Qt, QRectF, QPointF, pyqtSignal, QMimeData
from ui.desktop.qt import (
    QPen, QBrush, QColor, QPainterPath, QFont,
    QPolygonF, QPainter, QDrag, QPixmap, QKeyEvent, QMouseEvent, QPalette,
    QDragEnterEvent, QDropEvent, QWheelEvent,
)
from ui.desktop.controls import BorderedComboBox
from ui.desktop.icons import codicon_icon, set_codicon
from ui.desktop.chrome import DialogTitleBar, prepare_frameless_dialog, window_chrome_qss
from ui.desktop.theme import ThemeMode, get_theme_manager, normalize_theme, theme_tokens

# ── Colors ──
CLR_DARK, CLR_MID, CLR_LIGHT, CLR_WHITE = QColor("#151c4b"), QColor("#5a7a9a"), QColor("#69bfef"), QColor("#ffffff")
CLR_PORT, CLR_CONNECTION = QColor("#69bfef"), QColor("#5a7a9a")
CLR_STEP_BG, CLR_TRANS_BG = QColor("#151c4b"), QColor("#5a7a9a")
PORT_R, BLOCK_W, BLOCK_H = 4.0, 140, 64
TRANS_W, TRANS_H = 110, 34
MIME_SFC = "application/x-sfc-block-type"

SFC_DIALOG_DARK_QSS = """
    QDialog { background: #1f1f1f; color: #cccccc; }
    QWidget#SFCDialogBody { background: #1f1f1f; color: #cccccc; }
    QLabel { color: #cccccc; background: transparent; }
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {
        min-height: 30px;
        color: #cccccc;
        background: #313131;
        border: 1px solid #454545;
        border-radius: 2px;
        padding: 4px 7px;
        selection-color: #ffffff;
        selection-background-color: #264f78;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
    QSpinBox:focus, QComboBox:focus { border-color: #0078d4; }
    QComboBox QAbstractItemView, QListView, QTreeView {
        color: #cccccc;
        background: #252526;
        border: 1px solid #454545;
        outline: none;
        selection-color: #ffffff;
        selection-background-color: #04395e;
    }
    QHeaderView::section {
        color: #cccccc;
        background: #2d2d2d;
        border: 1px solid #3c3c3c;
        padding: 5px;
    }
    QGroupBox {
        color: #cccccc;
        border: 1px solid #3c3c3c;
        border-radius: 3px;
        margin-top: 10px;
        padding-top: 14px;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 5px; }
    QPushButton {
        min-height: 30px;
        padding: 0 12px;
        color: #cccccc;
        background: #313131;
        border: 1px solid #454545;
        border-radius: 2px;
    }
    QPushButton:hover { color: #ffffff; background: #3c3c3c; }
    QPushButton:focus { border-color: #0078d4; }
    QPushButton#PrimaryButton {
        color: #ffffff;
        background: #0e639c;
        border-color: #0e639c;
    }
    QPushButton#PrimaryButton:hover { background: #1177bb; }
    QPushButton#DangerButton {
        color: #ffffff;
        background: #c42b1c;
        border-color: #c42b1c;
    }
    QToolTip { color: #f0f0f0; background: #252526; border: 1px solid #454545; }
"""

SFC_DIALOG_LIGHT_QSS = """
    QDialog { background: #f5f5f5; color: #1e1e1e; }
    QWidget#SFCDialogBody { background: #ffffff; color: #1e1e1e; }
    QLabel { color: #1e1e1e; background: transparent; }
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {
        min-height: 30px; color: #1e1e1e; background: #ffffff;
        border: 1px solid #cccedb; border-radius: 2px; padding: 4px 7px;
        selection-color: #1e1e1e; selection-background-color: #cde8ff;
    }
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #0078d4; }
    QComboBox QAbstractItemView, QListView, QTreeView { color: #1e1e1e; background: #ffffff; border: 1px solid #cccedb; outline: none; selection-color: #1e1e1e; selection-background-color: #cde8ff; }
    QHeaderView::section { color: #1e1e1e; background: #f3f3f3; border: 1px solid #cccedb; padding: 5px; }
    QGroupBox { color: #1e1e1e; border: 1px solid #cccedb; border-radius: 3px; margin-top: 10px; padding-top: 14px; }
    QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 5px; }
    QPushButton { min-height: 30px; padding: 0 12px; color: #1e1e1e; background: #f3f3f3; border: 1px solid #cccedb; border-radius: 2px; }
    QPushButton:hover { color: #1e1e1e; background: #e5f1fb; }
    QPushButton:focus { border-color: #0078d4; }
    QPushButton#PrimaryButton { color: #ffffff; background: #0078d4; border-color: #0078d4; }
    QPushButton#DangerButton { color: #ffffff; background: #c42b1c; border-color: #c42b1c; }
    QToolTip { color: #1e1e1e; background: #ffffff; border: 1px solid #cccedb; }
"""


def sfc_dialog_qss(mode):
    selected = normalize_theme(mode)
    base = SFC_DIALOG_DARK_QSS if selected == ThemeMode.DARK else SFC_DIALOG_LIGHT_QSS
    return base + window_chrome_qss(selected)


class SFCDialog(QDialog):
    """Frameless SFC dialog using the same chrome as the main workbench."""

    def __init__(self, title, parent=None, icon_name="circuit-board"):
        super().__init__(parent)
        self.setWindowTitle(title)
        prepare_frameless_dialog(self)
        self.setModal(True)
        self._theme = get_theme_manager().current_theme
        self.setStyleSheet(sfc_dialog_qss(self._theme))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.title_bar = DialogTitleBar(
            self,
            title,
            icon_name=icon_name,
            allow_minimize=False,
            allow_maximize=False,
        )
        outer.addWidget(self.title_bar)
        self.body = QWidget()
        self.body.setObjectName("SFCDialogBody")
        self.content_layout = QVBoxLayout(self.body)
        self.content_layout.setContentsMargins(16, 14, 16, 14)
        self.content_layout.setSpacing(10)
        outer.addWidget(self.body, 1)

    def apply_theme(self, mode):
        self._theme = normalize_theme(mode)
        self.setStyleSheet(sfc_dialog_qss(self._theme))


class SFCMessageDialog(SFCDialog):
    def __init__(self, title, message, kind="info", question=False, parent=None):
        icon_names = {"info": "info", "warning": "warning", "error": "error"}
        super().__init__(title, parent, icon_names.get(kind, "info"))
        self.setFixedWidth(440)
        message_label = QLabel(message)
        message_label.setWordWrap(True)
        message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.content_layout.addWidget(message_label)
        actions = QHBoxLayout()
        actions.addStretch()
        if question:
            cancel = QPushButton(tr('取消'))
            cancel.clicked.connect(self.reject)
            actions.addWidget(cancel)
        confirm = QPushButton(tr('确定'))
        confirm.setObjectName("DangerButton" if question else "PrimaryButton")
        confirm.clicked.connect(self.accept)
        actions.addWidget(confirm)
        self.content_layout.addLayout(actions)


def show_sfc_message(parent, title, message, kind="info", question=False):
    dialog = SFCMessageDialog(title, message, kind, question, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted


class SFCFileDialog(SFCDialog):
    def __init__(self, title, save=False, initial="", parent=None):
        super().__init__(title, parent, "save" if save else "folder-opened")
        self.resize(760, 520)
        self.picker = QFileDialog(self)
        self.picker.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.picker.setWindowFlags(Qt.WindowType.Widget)
        self.picker.setNameFilters([tr('SFC 文件 (*.sfc)'), tr('所有文件 (*)')])
        if save:
            self.picker.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            self.picker.selectFile(initial or "control_flow.sfc")
        else:
            self.picker.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
            self.picker.setFileMode(QFileDialog.FileMode.ExistingFile)
        self.picker.accepted.connect(self.accept)
        self.picker.rejected.connect(self.reject)
        self.content_layout.setContentsMargins(8, 8, 8, 8)
        self.content_layout.addWidget(self.picker)

    def selected_path(self):
        paths = self.picker.selectedFiles()
        return paths[0] if paths else ""

PALETTE_ITEMS = [
    {"type": "step",       "label": tr('步骤'),       "icon": "symbol-field", "hint": tr('执行动作。双击编辑名称，右键高级编辑')},
    {"type": "transition", "label": tr('转移条件'),   "icon": "arrow-right", "hint": tr('进入下一步的条件。双击编辑条件')},
]

# ═══════════════════ Port ═══════════════════
class SFCPort(QGraphicsEllipseItem):
    def __init__(self, parent_block, is_output: bool):
        bw, bh = parent_block.rect().width(), parent_block.rect().height()
        r2 = PORT_R * 2
        x = bw / 2 - PORT_R
        y = bh - PORT_R * 0.5 if is_output else -PORT_R * 1.5
        super().__init__(QRectF(x, y, r2, r2), parent_block)
        self.is_output = is_output
        self.block = parent_block
        self.setBrush(QBrush(CLR_PORT))
        self.setPen(QPen(CLR_MID, 1.5))
        self.setZValue(10)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
    def center_scene_pos(self): return self.mapToScene(self.rect().center())
    def hoverEnterEvent(self, e):
        self.setBrush(QBrush(CLR_WHITE)); self.setPen(QPen(CLR_WHITE, 2)); super().hoverEnterEvent(e)
    def hoverLeaveEvent(self, e):
        self.setBrush(QBrush(CLR_PORT)); self.setPen(QPen(CLR_MID, 1.5)); super().hoverLeaveEvent(e)

# ═══════════════════ Blocks ═══════════════════
class SFCBlock(QGraphicsRectItem):
    BLOCK_TYPE = "base"
    def __init__(self, label="", x=0, y=0):
        w, h = self._block_size()
        super().__init__(QRectF(0, 0, w, h))
        self.setPos(x, y)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setZValue(5)
        self.text_item = QGraphicsTextItem(self)
        self.text_item.setDefaultTextColor(CLR_WHITE)
        f = QFont("Microsoft YaHei", 9); f.setBold(True); self.text_item.setFont(f)
        self.set_label(label)
        self.in_port = SFCPort(self, False)
        self.out_port = SFCPort(self, True)
        self.properties: dict = {}
    def _block_size(self): return (BLOCK_W, BLOCK_H)
    def set_label(self, text: str):
        self.text_item.setPlainText(text)
        tw, th = self.text_item.boundingRect().width(), self.text_item.boundingRect().height()
        bw, bh = self.rect().width(), self.rect().height()
        self.text_item.setPos((bw - tw) / 2, (bh - th) / 2)
    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._border_pen()); painter.setBrush(self._fill_brush())
        painter.drawRoundedRect(self.rect(), 8, 8)
        if self.isSelected():
            painter.setPen(QPen(CLR_LIGHT, 2.5, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(-5, -5, 5, 5), 12, 12)
    def _border_pen(self): return QPen(CLR_MID, 2)
    def _fill_brush(self): return QBrush(CLR_STEP_BG)
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for conn in self._connections(): conn.update_path()
        return super().itemChange(change, value)
    def _connections(self):
        s = self.scene()
        if not s: return []
        return [i for i in s.items() if isinstance(i, SFCConnection) and (i.source_block is self or i.target_block is self)]

class SFCStepBlock(SFCBlock):
    BLOCK_TYPE = "step"

class SFCTransitionBlock(SFCBlock):
    BLOCK_TYPE = "transition"
    def _fill_brush(self): return QBrush(CLR_MID)

# ═══════════════════ Connection ═══════════════════
class SFCConnection(QGraphicsPathItem):
    ARROW_SIZE, H_GAP = 9.0, 25.0
    def __init__(self, source_block, target_block):
        super().__init__()
        self.source_block = source_block
        self.target_block = target_block
        self._color = CLR_CONNECTION
        self.setPen(QPen(self._color, 2.5, Qt.PenStyle.SolidLine))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))

    def set_color(self, color: QColor):
        self._color = color
        self.setPen(QPen(color, 2.5, Qt.PenStyle.SolidLine))
        self.setZValue(0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.update_path()
    def update_path(self):
        src = self.source_block.out_port.center_scene_pos()
        dst = self.target_block.in_port.center_scene_pos()
        path = QPainterPath(); path.moveTo(src)
        if dst.y() >= src.y():
            mid_y = (src.y() + dst.y()) / 2
            path.lineTo(src.x(), mid_y); path.lineTo(dst.x(), mid_y); path.lineTo(dst)
        else:
            sr = self.source_block.mapToScene(QPointF(self.source_block.rect().width(), 0)).x()
            dr = self.target_block.mapToScene(QPointF(self.target_block.rect().width(), 0)).x()
            rx = max(sr, dr) + self.H_GAP + 30
            path.lineTo(src.x(), src.y() + 20); path.lineTo(rx, src.y() + 20)
            path.lineTo(rx, dst.y() - 20); path.lineTo(dst.x(), dst.y() - 20); path.lineTo(dst)
        self.setPath(path)
    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dst = self.target_block.in_port.center_scene_pos()
        ld = self.mapFromScene(dst)
        painter.setPen(QPen(CLR_CONNECTION, 2)); painter.setBrush(QBrush(CLR_CONNECTION))
        a = QPolygonF(); a.append(ld)
        a.append(QPointF(ld.x() - self.ARROW_SIZE, ld.y() - self.ARROW_SIZE * 2.2))
        a.append(QPointF(ld.x() + self.ARROW_SIZE, ld.y() - self.ARROW_SIZE * 2.2))
        painter.drawPolygon(a)

# ═══════════════════ I/O Config Dialog ═══════════════════
class IOConfigDialog(SFCDialog):
    def __init__(self, io_config: dict, parent=None):
        super().__init__(tr('I/O 硬件映射配置'), parent, "settings-gear")
        self.setMinimumWidth(560)
        self.io_config = io_config
        layout = self.content_layout
        layout.addWidget(QLabel(tr('定义 PLC 项目的输入/输出信号。配置后编辑步骤时可下拉选择。')))
        self.input_editors, self.output_editors, self.reg_editors = [], [], []
        def make_group(title, editors, key, prefix, layout):
            g = QGroupBox(title); gl = QFormLayout(g)
            for addr, desc in io_config.get(key, {}).items():
                self._row(gl, addr, desc, prefix, editors)
            g.setLayout(gl); layout.addWidget(g)
            btn = QPushButton(tr('+ 添加{v0}', v0=prefix))
            btn.clicked.connect(lambda: self._add_new(gl, prefix, editors, key))
            layout.addWidget(btn)
            return gl
        make_group(tr('X 触点'), self.input_editors, "inputs", "X", layout)
        make_group(tr('Y 触点'), self.output_editors, "outputs", "Y", layout)
        make_group(tr('D/T 寄存器/定时器'), self.reg_editors, "registers", "D/T", layout)
        btns = QHBoxLayout()
        ok = QPushButton(tr('确定')); cancel = QPushButton(tr('取消'))
        ok.setObjectName("PrimaryButton")
        ok.clicked.connect(self._save); cancel.clicked.connect(self.reject)
        btns.addStretch(); btns.addWidget(ok); btns.addWidget(cancel); layout.addLayout(btns)

    def _row(self, layout, addr, desc, prefix, editors):
        ae = QLineEdit(addr); ae.setFixedWidth(80); de = QLineEdit(desc)
        w = QWidget(); rl = QHBoxLayout(w); rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(ae); rl.addWidget(de)
        btn_del = QPushButton(tr('删除'))
        btn_del.setObjectName("DangerButton")
        row_data = (ae, de)
        btn_del.clicked.connect(lambda checked, rw=w, rl=layout, rd=row_data, ed=editors: self._delete_row(rw, rl, rd, ed))
        rl.addWidget(btn_del)
        layout.addRow(f"{prefix}:", w)
        editors.append(row_data)

    def _delete_row(self, widget, layout, pair, editors):
        layout.removeRow(widget)
        if pair in editors: editors.remove(pair)

    def _next_addr(self, prefix, key, editors):
        # 已保存的配置
        d = self.io_config.get(key, {})
        nums = [int(k[len(prefix):]) for k in d if k.startswith(prefix) and k[len(prefix):].isdigit()]
        # 当前编辑器中未保存的行
        for ae, _ in editors:
            t = ae.text().strip()
            if t.startswith(prefix) and t[len(prefix):].isdigit():
                nums.append(int(t[len(prefix):]))
        return f"{prefix}{max([-1] + nums) + 1}"

    def _add_new(self, layout, prefix, editors, key):
        addr = self._next_addr(prefix, key, editors)
        self._row(layout, addr, "", prefix, editors)

    def _save(self):
        for key, editors in [("inputs", self.input_editors), ("outputs", self.output_editors), ("registers", self.reg_editors)]:
            d = {}
            for ae, de in editors:
                a = ae.text().strip()
                if a: d[a] = de.text().strip() or a
            self.io_config[key] = d
        self.accept()

# ═══════════════════ Scene ═══════════════════
class SFCScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(-3000, -3000, 6000, 9000)
        self._drag_port = None; self._tmp_line = None

    def _port_at(self, pos, is_out):
        for item in self.items(pos, Qt.ItemSelectionMode.IntersectsItemShape, Qt.SortOrder.DescendingOrder):
            if isinstance(item, SFCPort) and item.is_output == is_out: return item
            if isinstance(item, SFCBlock):
                p = item.out_port if is_out else item.in_port
                if p and p.contains(p.mapFromScene(pos)): return p
        return None

    def _conn_exists(self, s, d):
        return any(isinstance(i, SFCConnection) and i.source_block is s and i.target_block is d for i in self.items())

    def _find_editor(self):
        for v in self.views():
            p = v.parent()
            while p:
                if hasattr(p, '_edit_block'): return p
                p = p.parent()
        return None

    def mousePressEvent(self, e):
        p = self._port_at(e.scenePos(), True)
        if p and e.button() == Qt.MouseButton.LeftButton:
            self._drag_port = p
            self._tmp_line = QGraphicsPathItem()
            self._tmp_line.setPen(QPen(CLR_LIGHT, 2.5, Qt.PenStyle.DashLine)); self._tmp_line.setZValue(100)
            self.addItem(self._tmp_line); e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_port and self._tmp_line:
            src = self._drag_port.center_scene_pos(); dst = e.scenePos()
            path = QPainterPath(); path.moveTo(src)
            dy = max(abs(dst.y() - src.y()) * 0.5, 60)
            path.cubicTo(QPointF(src.x(), src.y() + dy), QPointF(dst.x(), dst.y() - dy), dst)
            self._tmp_line.setPath(path); e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_port:
            tp = self._port_at(e.scenePos(), False)
            if tp and tp.block is not self._drag_port.block:
                s, d = self._drag_port.block, tp.block
                if not self._conn_exists(s, d):
                    conn = SFCConnection(s, d)
                    self.addItem(conn)
                    conn.update_path()  # 立即刷新路径
                    self.update()
                    self._auto_split(s, d)
            if self._tmp_line: self.removeItem(self._tmp_line)
            self._drag_port = None; self._tmp_line = None; e.accept(); return
        super().mouseReleaseEvent(e)

    def _auto_split(self, new_src, new_dst):
        """仅当新连线是在已有唯一出线上插入时才分段。并行分支不触发。"""
        # 统计 new_src 的出线（不含刚创建的 new_src→new_dst）
        others = [i for i in self.items()
                  if isinstance(i, SFCConnection)
                  and i.source_block is new_src
                  and i.target_block is not new_dst]
        # 多于一条已有出线 = 并行分支，不做分割
        if len(others) != 1:
            return
        old_conn = others[0]
        old_dst = old_conn.target_block
        # 检查 new_dst 是否在 new_src 和 old_dst 之间（Y 轴 + X 轴都要接近连线路径）
        sx, sy = new_src.scenePos().x(), new_src.scenePos().y()
        dx, dy = new_dst.scenePos().x(), new_dst.scenePos().y()
        ox, oy = old_dst.scenePos().x(), old_dst.scenePos().y()
        # Y 在中间
        y_between = (sy < dy < oy) or (oy < dy < sy)
        # X 也在大致路径上（水平偏移不超过块宽度 * 2，避免把分支误判为插入）
        mid_x = (sx + ox) / 2
        x_near = abs(dx - mid_x) < BLOCK_W * 2
        if y_between and x_near:
            self.removeItem(old_conn)
            if not self._conn_exists(new_dst, old_dst):
                conn2 = SFCConnection(new_dst, old_dst)
                self.addItem(conn2)
                conn2.update_path()
                self.update()

    def mouseDoubleClickEvent(self, e):
        pos = e.scenePos()
        for item in self.items(pos, Qt.ItemSelectionMode.IntersectsItemShape, Qt.SortOrder.DescendingOrder):
            if isinstance(item, SFCBlock):
                ed = self._find_editor()
                if ed: ed._quick_edit(item)
                e.accept(); return
        super().mouseDoubleClickEvent(e)

# ═══════════════════ View ═══════════════════
class SFCView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setBackgroundBrush(QBrush(QColor("#fff")))
        self.setAcceptDrops(True)
        self._panning, self._last_pan = False, QPointF()
        self._editor_ref = None

    def keyPressEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            ed = self._editor_ref
            if ed is None:
                p = self.parent()
                while p:
                    if isinstance(p, SFCEditorWidget): ed = p; break
                    p = p.parent()
            if ed:
                if e.key() == Qt.Key.Key_C: ed._copy(); return
                if e.key() == Qt.Key.Key_X: ed._cut(); return
                if e.key() == Qt.Key.Key_V: ed._paste(); return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15; self.scale(f, f)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton or (
           e.button() == Qt.MouseButton.LeftButton and e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._panning = True; self._last_pan = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor); e.accept(); return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning:
            d = e.position() - self._last_pan; self._last_pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            e.accept(); return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._panning: self._panning = False; self.setCursor(Qt.CursorShape.ArrowCursor); e.accept(); return
        super().mouseReleaseEvent(e)

    def dragEnterEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasFormat(MIME_SFC) else e.ignore()
    def dragMoveEvent(self, e):
        e.acceptProposedAction() if e.mimeData().hasFormat(MIME_SFC) else e.ignore()
    def dropEvent(self, e):
        if e.mimeData().hasFormat(MIME_SFC):
            bt = e.mimeData().data(MIME_SFC).data().decode()
            ed = self._editor_ref
            if ed is None:
                p = self.parent()
                while p:
                    if isinstance(p, SFCEditorWidget): ed = p; break
                    p = p.parent()
            if ed: ed.add_block_at(bt, self.mapToScene(e.position().toPoint()).x(), self.mapToScene(e.position().toPoint()).y())
            e.acceptProposedAction()

# ═══════════════════ Palette ═══════════════════
class SFCBlockPalette(QListWidget):
    block_double_clicked = pyqtSignal(str, float, float)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(150); self.setDragEnabled(True)
        self.apply_theme(get_theme_manager().current_theme)
        for d in PALETTE_ITEMS:
            it = QListWidgetItem(codicon_icon(d["icon"]), d["label"]); it.setData(Qt.ItemDataRole.UserRole, d["type"]); it.setToolTip(d["hint"]); self.addItem(it)
        self.itemDoubleClicked.connect(lambda it: self.block_double_clicked.emit(it.data(Qt.ItemDataRole.UserRole), 0, 0))

    def apply_theme(self, mode):
        colors = theme_tokens(mode)
        self.setStyleSheet("""
            QListWidget { background: %(shell)s; border: none; border-right: 1px solid %(border)s; border-radius: 0; font-size: 13px; color: %(text)s; }
            QListWidget::item { min-height: 42px; padding: 6px 10px; border-bottom: 1px solid %(border)s; color: %(text)s; background: %(shell)s; }
            QListWidget::item:hover { background: %(hover)s; color: %(text_strong)s; }
            QListWidget::item:selected { background: %(selection)s; color: %(text_strong)s; }
        """ % colors)

    def startDrag(self, actions):
        it = self.currentItem()
        if it is None: return
        m = QMimeData(); m.setData(MIME_SFC, it.data(Qt.ItemDataRole.UserRole).encode())
        d = QDrag(self); d.setMimeData(m)
        pm = QPixmap(80, 40); pm.fill(CLR_DARK); d.setPixmap(pm); d.setHotSpot(QPointF(40, 20).toPoint())
        d.exec(Qt.DropAction.CopyAction)

# ═══════════════════ SFC -> Text ═══════════════════
def _clean(v):
    v = v.strip()
    return None if (not v or v == tr('（请先配置 I/O）')) else v

def sfc_to_text(scene: SFCScene, io_config: Optional[dict] = None) -> str:
    """将 SFC 流程图转换为结构化文本描述"""
    blocks = [i for i in scene.items() if isinstance(i, SFCBlock)]
    conns  = [i for i in scene.items() if isinstance(i, SFCConnection)]
    if not blocks: return ""

    def next_blocks(blk):
        return [c.target_block for c in conns if c.source_block is blk]
    def prev_blocks(blk):
        return [c.source_block for c in conns if c.target_block is blk]
    def label_of(blk):
        return blk.text_item.toPlainText().strip() or tr('（未命名）')

    # 找入口
    roots = [b for b in blocks if not prev_blocks(b)]
    if not roots: roots = blocks
    roots.sort(key=lambda b: b.scenePos().y())

    lines = []
    if io_config:
        for key, lbl in [("inputs","X"),("outputs","Y"),("registers","D/T")]:
            for addr,desc in io_config.get(key,{}).items():
                lines.append(f"  {lbl}{addr}" + (f"（{desc}）" if desc and desc!=addr else ""))
        if lines: lines.insert(0,tr('【硬件映射】')); lines.append("")

    visited, step_num = set(), [0]
    lines.append(tr('【步进控制逻辑】')); lines.append("")

    def traverse(start, indent=""):
        queue = [start]
        while queue:
            blk = queue.pop(0)
            if blk in visited: continue
            visited.add(blk)
            label = label_of(blk)
            if blk.BLOCK_TYPE == "step":
                step_num[0] += 1
                lines.append(f"{indent}S{step_num[0]}：{label}")
                nxt = next_blocks(blk)
                if len(nxt) > 1:
                    lines.append(tr('{v0}  [并行分支]', v0=indent))
                    for i,n in enumerate(nxt):
                        lines.append(tr('{v0}  子路径{v1}：', v0=indent, v1=i + 1))
                        sub_q = [n]; sub_d = 0
                        while sub_q and sub_d < 10:
                            cur = sub_q.pop(0)
                            if cur in visited: continue
                            visited.add(cur)
                            lbl = label_of(cur)
                            if cur.BLOCK_TYPE == "transition":
                                lines.append(f"{indent}    ├ {lbl}")
                            else:
                                step_num[0] += 1
                                lines.append(f"{indent}    S{step_num[0]}：{lbl}")
                            for nn in next_blocks(cur):
                                if nn not in visited: sub_q.append(nn)
                            sub_d += 1
                    lines.append(tr('{v0}  [汇合]', v0=indent))
                elif len(nxt) == 1:
                    queue.append(nxt[0])
            else:
                lines.append(tr('{v0}  → 条件：{v1}', v0=indent, v1=label))
                for n in next_blocks(blk):
                    if n not in visited: queue.append(n)

    for root in roots:
        if root not in visited: traverse(root)

    lines.append("")
    lines.append(tr('请根据以上步进控制逻辑生成完整的梯形图 JSON。'))
    return "\n".join(lines)
# ═══════════════════ Block Edit Dialog (Advanced) ═══════════════════
class BlockEditDialog(SFCDialog):
    def __init__(self, block: SFCBlock, io_config: dict, parent=None):
        super().__init__(tr('高级编辑'), parent, "tools")
        self.block = block; self.io_config = io_config
        self.setMinimumWidth(520)
        layout = self.content_layout
        layout.addWidget(QLabel(tr('名称：')))
        self.name_edit = QLineEdit(block.text_item.toPlainText().strip()); layout.addWidget(self.name_edit)

        layout.addWidget(QLabel(tr('动作描述：')))
        self.action_edit = QTextEdit(); self.action_edit.setMaximumHeight(80)
        desc = block.properties.get("action", "") or block.properties.get("condition", "")
        self.action_edit.setPlainText(desc); layout.addWidget(self.action_edit)
        io_row = QHBoxLayout()
        for title, key in [(tr('Y 触点'), "outputs"), (tr('T/D 定时器/寄存器'), "timers"), (tr('X 触点'), "inputs")]:
            g = QGroupBox(title); gl = QVBoxLayout(g)
            cb = BorderedComboBox(); cb.setProperty("darkTheme", self._theme == ThemeMode.DARK)
            cb.setEditable(True); cb.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            items = [f"{a} — {d}" if d and d != a else a for a, d in io_config.get(key, {}).items()]
            items.insert(0, "")  # 默认空选项
            cb.addItems(items)
            v = block.properties.get(key, "")
            if v:
                # 匹配 "Y0 — 电机" 或 "Y0"
                for it in items:
                    if it.startswith(v):
                        cb.setCurrentText(it)
                        break
            gl.addWidget(cb); g.setLayout(gl); io_row.addWidget(g)
            setattr(self, f"{key}_combo", cb)
        layout.addLayout(io_row)

        btns = QHBoxLayout()
        ok = QPushButton(tr('确定')); cancel = QPushButton(tr('取消'))
        ok.setObjectName("PrimaryButton")
        ok.clicked.connect(self._save); cancel.clicked.connect(self.reject)
        btns.addStretch(); btns.addWidget(ok); btns.addWidget(cancel); layout.addLayout(btns)

    def _save(self):
        b = self.block; n = self.name_edit.text().strip()
        if n: b.set_label(n)
        b.properties["action"] = self.action_edit.toPlainText().strip()
        # also set condition for transition blocks (used by sfc_to_text)
        if b.BLOCK_TYPE == "transition":
            b.properties["condition"] = n or self.action_edit.toPlainText().strip()
        for key in ["outputs", "timers", "inputs"]:
            v = getattr(self, f"{key}_combo").currentText().strip()
            if " — " in v: v = v.split(" — ")[0]
            b.properties[key] = "" if v in (tr('（请先配置 I/O）'), "") else v
        self.accept()

# ═══════════════════ Quick Edit Dialog ═══════════════════
class QuickEditDialog(SFCDialog):
    """简易编辑对话框 — 与主应用一致的配色。"""
    def __init__(self, block: SFCBlock, parent=None):
        super().__init__(tr('快速编辑'), parent, "edit")
        self.block = block
        self.setMinimumWidth(440)
        layout = self.content_layout
        cur = block.text_item.toPlainText().strip()
        defaults = {"新步骤": "", "转移条件": ""}
        cur = "" if cur in defaults else cur
        layout.addWidget(QLabel(tr('名称：')))
        self.name_edit = QLineEdit(cur); layout.addWidget(self.name_edit)
        layout.addWidget(QLabel(tr('描述（可选）：')))
        self.desc_edit = QTextEdit(); self.desc_edit.setMaximumHeight(60)
        self.desc_edit.setPlainText(block.properties.get("action", "") or block.properties.get("condition", ""))
        layout.addWidget(self.desc_edit)
        btns = QHBoxLayout()
        ok = QPushButton(tr('确定')); cancel = QPushButton(tr('取消'))
        ok.setObjectName("PrimaryButton")
        ok.clicked.connect(self._save); cancel.clicked.connect(self.reject)
        btns.addStretch(); btns.addWidget(ok); btns.addWidget(cancel); layout.addLayout(btns)

    def _save(self):
        b = self.block; n = self.name_edit.text().strip()
        if n: b.set_label(n)
        desc = self.desc_edit.toPlainText().strip()
        if b.BLOCK_TYPE == "transition":
            b.properties["condition"] = desc or n
        else:
            b.properties["action"] = desc
        self.accept()

# ═══════════════════ Editor Widget ═══════════════════
class SFCEditorWidget(QWidget):
    text_generated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.io_config = {"inputs": {}, "outputs": {}, "registers": {}}
        self.scene = SFCScene(); self.view = SFCView(self.scene); self.palette = SFCBlockPalette()
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(True)
        editor_palette = QApplication.palette()
        editor_palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
        editor_palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        self.setPalette(editor_palette)
        self.view.setAutoFillBackground(True)
        self.view.viewport().setAutoFillBackground(True)
        self.view.setStyleSheet(
            "QGraphicsView { background: #ffffff; border: 1px solid #cbd5e1; }"
        )

        tb = QToolBar()
        self.toolbar = tb
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.setStyleSheet(f"""
            QToolBar {{
                background: #181818;
                border: none;
                border-bottom: 1px solid #2b2b2b;
                border-radius: 0;
                padding: 2px;
            }}
            QToolBar QLabel {{ color: #9d9d9d; background: transparent; }}
            QToolBar QToolButton {{
                color: #cccccc;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 2px;
                padding: 5px 9px;
                margin: 1px;
                font-size: 12px;
            }}
            QToolBar QToolButton:hover {{ color: #ffffff; background: #2a2d2e; }}
            QToolBar QToolButton:pressed {{ background: #37373d; }}
            QToolBar::separator {{ background: #3c3c3c; width: 1px; margin: 4px; }}
        """)
        tb.addAction(
            codicon_icon("file-text"), tr('转为文本描述')
        ).triggered.connect(self._convert)
        tb.addSeparator()
        tb.addAction(
            codicon_icon("clear-all"), tr('清空画布')
        ).triggered.connect(self._clear)
        tb.addAction(
            codicon_icon("tools"), tr('I/O 配置')
        ).triggered.connect(self._io_config)
        tb.addSeparator()
        tb.addAction(
            codicon_icon("save"), tr('保存')
        ).triggered.connect(self._save_file)
        tb.addAction(
            codicon_icon("folder-opened"), tr('打开')
        ).triggered.connect(self._load_file)
        self._fs_act = tb.addAction(
            codicon_icon("screen-full"), tr('全屏绘制')
        )
        self._fs_act.triggered.connect(self._fs_toggle)
        tb.addSeparator()
        tb.addWidget(QLabel(tr('双击编辑名称 | 右键高级编辑 | 拖底部圆点连线 | Ctrl+拖拽平移 | 滚轮缩放')))

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(0)
        self._splitter.setStyleSheet(
            "QSplitter { background: #1f1f1f; }"
            "QSplitter::handle { background: #2b2b2b; border: none; }"
        )
        self._splitter.addWidget(self.palette); self._splitter.addWidget(self.view)
        self._splitter.setStretchFactor(0, 0); self._splitter.setStretchFactor(1, 1)

        ly = QVBoxLayout(self); ly.setContentsMargins(0, 0, 0, 0); ly.setSpacing(4)
        ly.addWidget(tb); ly.addWidget(self._splitter)
        self.palette.block_double_clicked.connect(self._add_block_to_center)
        self._fs_dialog = None
        self._clipboard = None  # {type, label, properties}
        # 快捷键
        from ui.desktop.qt import QShortcut, QKeySequence
        QShortcut(QKeySequence.StandardKey.Copy, self, activated=self._copy)
        QShortcut(QKeySequence.StandardKey.Cut, self, activated=self._cut)
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self._paste)
        self.apply_theme(get_theme_manager().current_theme)

    def apply_theme(self, mode):
        selected = normalize_theme(mode)
        colors = theme_tokens(selected)
        editor_palette = QApplication.palette()
        editor_palette.setColor(QPalette.ColorRole.Window, QColor(colors["surface"]))
        editor_palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface"]))
        self.setPalette(editor_palette)
        canvas = colors["surface"] if selected == ThemeMode.LIGHT else "#1e1e1e"
        self.view.setBackgroundBrush(QBrush(QColor(canvas)))
        self.view.setStyleSheet(
            f"QGraphicsView {{ background: {canvas}; border: 1px solid {colors['border']}; }}"
        )
        self.toolbar.setStyleSheet("""
            QToolBar { background: %(shell)s; border: none; border-bottom: 1px solid %(border)s; border-radius: 0; padding: 2px; }
            QToolBar QLabel { color: %(text_muted)s; background: transparent; }
            QToolBar QToolButton { color: %(text)s; background: transparent; border: 1px solid transparent; border-radius: 2px; padding: 5px 9px; margin: 1px; font-size: 12px; }
            QToolBar QToolButton:hover { color: %(text_strong)s; background: %(hover)s; }
            QToolBar QToolButton:pressed { background: %(selection)s; }
            QToolBar::separator { background: %(border)s; width: 1px; margin: 4px; }
        """ % colors)
        self._splitter.setStyleSheet(
            "QSplitter { background: %(surface)s; }"
            "QSplitter::handle { background: %(border)s; border: none; }" % colors
        )
        self.palette.apply_theme(selected)

    def showEvent(self, e):
        super().showEvent(e)
        self._splitter.setSizes([150, self._splitter.width() - 150])

    # ── Block management ──
    def add_block_at(self, bt, x, y):
        b = SFCStepBlock(tr('新步骤'), x, y) if bt == "step" else SFCTransitionBlock(tr('转移条件'), x, y)
        self.scene.clearSelection(); self.scene.addItem(b); b.setSelected(True)
        self._quick_edit(b)

    def _add_block_to_center(self, bt, x, y):
        cx = self.view.mapToScene(self.view.viewport().rect().center()).x() + random.randint(-60, 60)
        cy = self.view.mapToScene(self.view.viewport().rect().center()).y() + random.randint(-30, 30)
        self.add_block_at(bt, cx, cy)

    def _quick_edit(self, block: SFCBlock):
        """双击 → 快速编辑。"""
        QuickEditDialog(block, self).exec()


    def _edit_block(self, block: SFCBlock):
        """右键 → 高级编辑。"""
        BlockEditDialog(block, self.io_config, self).exec()

    def _del_block(self, b):
        for c in b._connections(): self.scene.removeItem(c)
        self.scene.removeItem(b)

    def _selected_block(self) -> Optional[SFCBlock]:
        for i in self.scene.selectedItems():
            if isinstance(i, SFCBlock): return i
        return None

    def _copy(self):
        b = self._selected_block()
        if b:
            self._clipboard = {"type": b.BLOCK_TYPE, "label": b.text_item.toPlainText().strip(),
                               "properties": dict(b.properties)}

    def _cut(self):
        self._copy()
        b = self._selected_block()
        if b: self._del_block(b)

    def _paste(self):
        if self._clipboard is None: return
        # 在最后右键位置附近粘贴（无右键记录时用视口中心）
        vp = self.view.viewport()
        cursor_pos = vp.mapFromGlobal(self.cursor().pos())
        if vp.rect().contains(cursor_pos):
            pos = self.view.mapToScene(cursor_pos)
        else:
            pos = self.view.mapToScene(vp.rect().center())
        cx, cy = pos.x() + random.randint(-20, 20), pos.y() + random.randint(-10, 10)
        bt = self._clipboard["type"]
        b = SFCStepBlock(self._clipboard["label"], cx, cy) if bt == "step" else SFCTransitionBlock(self._clipboard["label"], cx, cy)
        b.properties = dict(self._clipboard.get("properties", {}))
        self.scene.clearSelection(); self.scene.addItem(b); b.setSelected(True)

    def _change_conn_color(self, conn):
        from ui.desktop.qt import QColorDialog
        wrapper = SFCDialog(tr('选择连线颜色'), self, "paintcan")
        wrapper.resize(620, 500)
        dlg = QColorDialog(conn._color, wrapper)
        dlg.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        dlg.setWindowFlags(Qt.WindowType.Widget)
        wrapper.content_layout.setContentsMargins(8, 8, 8, 8)
        wrapper.content_layout.addWidget(dlg)
        bb = dlg.findChild(QDialogButtonBox)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr('确定'))
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText(tr('取消'))
        bb.button(QDialogButtonBox.StandardButton.Ok).setObjectName("PrimaryButton")
        MAP = {
            "&Pick Screen Color": tr('取屏幕颜色'),
            "Pick Screen Color": tr('取屏幕颜色'),
            "&Add to Custom Colors": tr('添加到自定义颜色'),
            "Add to Custom Colors": tr('添加到自定义颜色'),
            "&Reset": tr('重置'), "Reset": tr('重置'),
            "Basic &colors": tr('基本颜色'), "Basic colors": tr('基本颜色'),
            "&Custom colors": tr('自定义颜色'), "Custom colors": tr('自定义颜色'),
            "Hu&e:": tr('色调(&E):'), "Hue:": tr('色调:'),
            "&Sat:": tr('饱和度(&S):'), "Sat:": tr('饱和度:'),
            "&Val:": tr('亮度(&V):'), "Val:": tr('亮度:'),
            "&Red:": tr('红(&R):'), "Red:": tr('红:'),
            "&Green:": tr('绿(&G):'), "Green:": tr('绿:'),
            "&Blue:": tr('蓝(&B):'), "Blue:": tr('蓝:'),
            "A&lpha:": tr('透明度(&L):'), "Alpha:": tr('透明度:'),
            "&HTML:": "HTML(&H):", "HTML:": "HTML:",
        }
        for w in dlg.findChildren(QPushButton) + dlg.findChildren(QLabel):
            t = w.text().strip()
            if t in MAP:
                w.setText(MAP[t])
        dlg.accepted.connect(wrapper.accept)
        dlg.rejected.connect(wrapper.reject)
        if wrapper.exec() == QDialog.DialogCode.Accepted:
            conn.set_color(dlg.selectedColor())

    def _del_sel(self):
        for i in list(self.scene.selectedItems()):
            if isinstance(i, SFCBlock): self._del_block(i)
            elif isinstance(i, SFCConnection): self.scene.removeItem(i)

    def populate_flowchart(self, steps: list):
        """
        根据 AI 分析结果自动填充流程图，支持分支结构。
        steps: [
          {"type":"step","label":"初始化"},
          {"type":"transition","label":"X0启动"},
          {"type":"fork","label":"分两路"},           # 并行分支开始
          {"type":"step","label":"Y0运行","branch":0},
          {"type":"transition","label":"T0到","branch":0},
          {"type":"step","label":"Y1运行","branch":1},
          {"type":"transition","label":"T1到","branch":1},
          {"type":"join","label":"汇合"},              # 并行分支结束
          {"type":"step","label":"完成"},
        ]
        无 fork/join 时退化为单线流程。
        """
        self.scene.clear()
        if not steps:
            return
        COL_GAP = 200      # 分支列间距
        ROW_GAP = 90       # 块行间距
        CENTER_X = 200     # 主干 x 坐标

        def make_block(bt, label, x, y):
            if bt in ("step", "fork", "join"):
                b = SFCStepBlock(label, x, y)
            else:
                b = SFCTransitionBlock(label, x, y)
            b.set_label(label)
            self.scene.addItem(b)
            return b

        # ---- 解析分支 ----
        # 找出所有 fork/join 位置
        forks = {}   # index -> branch_count
        joins = set()
        max_branch = 0
        for i, s in enumerate(steps):
            if s.get("type") == "fork":
                forks[i] = 0
            elif s.get("type") == "join":
                joins.add(i)
            br = s.get("branch", -1)
            if isinstance(br, int) and br > max_branch:
                max_branch = br
        # 推算每个 fork 的分支数
        fork_indices = list(forks.keys())
        for fi, f_idx in enumerate(fork_indices):
            next_join = None
            for j in joins:
                if j > f_idx:
                    next_join = j
                    break
            if next_join:
                branches_in_range = set()
                for k in range(f_idx + 1, next_join):
                    br = steps[k].get("branch", -1)
                    if isinstance(br, int) and br >= 0:
                        branches_in_range.add(br)
                forks[f_idx] = max(branches_in_range) + 1 if branches_in_range else 2
            else:
                forks[f_idx] = max_branch + 1 if max_branch >= 0 else 2

        # ---- 布局 ----
        blocks = []          # 按 steps 顺序存放 (block, x, y)
        y = 20
        x = CENTER_X
        i = 0
        while i < len(steps):
            s = steps[i]
            bt = s.get("type", "step")
            label = s.get("label", "")

            if bt == "fork":
                b = make_block("step", label, x, y)
                blocks.append((b, x, y))
                fork_idx = i
                branch_count = forks.get(i, 2)
                y += ROW_GAP
                i += 1
                # 处理各分支
                branch_rows = [y] * branch_count
                branch_x = [x - (branch_count - 1) * COL_GAP / 2 + b * COL_GAP for b in range(branch_count)]
                branch_done = [False] * branch_count
                while i < len(steps):
                    s2 = steps[i]
                    if s2.get("type") == "join":
                        break
                    br = s2.get("branch", None)
                    if isinstance(br, int) and 0 <= br < branch_count:
                        b2 = make_block(s2.get("type", "step"), s2.get("label", ""), branch_x[br], branch_rows[br])
                        blocks.append((b2, branch_x[br], branch_rows[br]))
                        branch_rows[br] += ROW_GAP
                    i += 1
                # join 块放在分支最大 y 处
                y = max(branch_rows)
                continue

            elif bt == "join":
                b = make_block("step", label, x, y)
                blocks.append((b, x, y))
                y += ROW_GAP
                i += 1

            else:
                b = make_block(bt, label, x, y)
                blocks.append((b, x, y))
                y += ROW_GAP
                i += 1

        # ---- 连线 ----
        for idx in range(len(blocks) - 1):
            b1, x1, y1 = blocks[idx]
            b2, x2, y2 = blocks[idx + 1]
            # 跳过 fork→第一分支 和 末分支→join 的连接（跨列由分支处理）
            s1_type = steps[idx].get("type", "") if idx < len(steps) else ""
            s2_type = steps[idx + 1].get("type", "") if idx + 1 < len(steps) else ""
            # fork 块 → 各分支第一块
            if s1_type == "fork":
                fork_x, fork_y = x1, y1
                branch_count = forks.get(idx, 2)
                for br in range(branch_count):
                    # 找第一个 branch==br 的块
                    for k in range(idx + 1, len(steps)):
                        if steps[k].get("type") == "join":
                            break
                        kb = steps[k].get("branch", -1)
                        if isinstance(kb, int) and kb == br:
                            _, bx, by = blocks[k]  # 注意: blocks 和 steps 索引对齐
                            conn = SFCConnection(b1, blocks[k][0])
                            self.scene.addItem(conn)
                            break
                continue
            # 各分支末块 → join
            if s2_type == "join":
                join_x, join_y = x2, y2
                for k in range(idx, -1, -1):
                    if steps[k].get("type") == "fork":
                        break
                    if steps[k].get("type") not in ("join",):
                        conn = SFCConnection(blocks[k][0], b2)
                        self.scene.addItem(conn)
                # 只连分支末块——这里简化处理，每个 branch 末块都连
                continue
            # 普通顺序连接
            if s1_type != "fork" and s2_type != "join":
                conn = SFCConnection(b1, b2)
                self.scene.addItem(conn)

    def _clear(self):
        if show_sfc_message(
            self, tr('清空画布'), tr('确定清空当前画布吗？此操作无法撤销。'),
            kind="warning", question=True,
        ):
            self.scene.clear()

    def _save_file(self):
        dialog = SFCFileDialog(tr('保存流程图'), save=True, initial="control_flow.sfc", parent=self)
        path = dialog.selected_path() if dialog.exec() == QDialog.DialogCode.Accepted else ""
        if not path: return
        data = {"version": 1, "io_config": self.io_config, "blocks": [], "connections": []}
        id_map = {}
        for i, item in enumerate(self.scene.items()):
            if isinstance(item, SFCBlock):
                tid = len(id_map) + 1
                id_map[id(item)] = tid
                data["blocks"].append({
                    "temp_id": tid, "type": item.BLOCK_TYPE,
                    "x": item.scenePos().x(), "y": item.scenePos().y(),
                    "label": item.text_item.toPlainText().strip(),
                    "properties": item.properties,
                })
        for item in self.scene.items():
            if isinstance(item, SFCConnection):
                sid = id_map.get(id(item.source_block))
                tid = id_map.get(id(item.target_block))
                if sid and tid:
                    data["connections"].append({"source_id": sid, "target_id": tid})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        show_sfc_message(self, tr('保存成功'), tr('流程图已保存至：\n{v0}', v0=path))

    def _load_file(self):
        dialog = SFCFileDialog(tr('打开流程图'), parent=self)
        path = dialog.selected_path() if dialog.exec() == QDialog.DialogCode.Accepted else ""
        if not path: return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.scene.clear()
        self.io_config = data.get("io_config", {"inputs": {}, "outputs": {}, "registers": {}})
        id_to_block = {}
        for bd in data.get("blocks", []):
            b = SFCStepBlock(bd["label"], bd["x"], bd["y"]) if bd["type"] == "step" else SFCTransitionBlock(bd["label"], bd["x"], bd["y"])
            b.properties = bd.get("properties", {})
            self.scene.addItem(b)
            id_to_block[bd["temp_id"]] = b
        for cd in data.get("connections", []):
            s = id_to_block.get(cd["source_id"])
            t = id_to_block.get(cd["target_id"])
            if s and t:
                conn = SFCConnection(s, t)
                self.scene.addItem(conn)
                conn.update_path()
        self.scene.update()
        show_sfc_message(self, tr('打开成功'), tr('流程图已加载：\n{v0}', v0=path))

    def _io_config(self):
        IOConfigDialog(self.io_config, self).exec()

    def _convert(self):
        t = sfc_to_text(self.scene, self.io_config)
        if t.strip():
            self.text_generated.emit(t)
            show_sfc_message(self, tr('转换完成'), tr('已转入文本输入框。'))
        else:
            show_sfc_message(self, tr('画布为空'), tr('请先添加步骤和转移条件。'), "warning")

    # ── Fullscreen ──
    def _fs_toggle(self):
        if self._fs_dialog is None:
            colors = theme_tokens(get_theme_manager().current_theme)
            self._fs_dialog = QDialog(self.window(), Qt.WindowType.FramelessWindowHint)
            self._fs_dialog.setStyleSheet("""
                QDialog { background: %(surface)s; color: %(text)s; }
                QLabel { color: %(text)s; background: transparent; }
                QPushButton { color: #ffffff; background: %(accent_button)s; border: 1px solid %(accent)s; border-radius: 2px; padding: 8px 16px; }
                QPushButton:hover { background: %(accent)s; }
            """ % colors)
            self._fs_dialog.showFullScreen()
            cb = QPushButton(tr('退出全屏'))
            set_codicon(cb, "screen-normal", tr('退出全屏'), 10)
            cb.setObjectName("PrimaryButton")
            cb.clicked.connect(self._fs_toggle); cb.setFixedHeight(36)
            top = QHBoxLayout(); top.addWidget(QLabel(tr('  全屏绘制 | Ctrl+拖拽平移 | 拖底部圆点连线 | 双击编辑 | 右键高级'))); top.addStretch(); top.addWidget(cb)
            fsv = SFCView(self.scene); fsv._editor_ref = self; fsv.setAcceptDrops(True)
            fsv.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            fsv.customContextMenuRequested.connect(lambda qp: self._show_context_menu(fsv.mapToScene(qp), fsv))
            fsp = SFCBlockPalette(); fsp.block_double_clicked.connect(self._add_block_to_center)
            sp = QSplitter(Qt.Orientation.Horizontal); sp.setHandleWidth(0)
            sp.setStyleSheet("QSplitter::handle { background: transparent; border: none; }")
            sp.addWidget(fsp); sp.addWidget(fsv); sp.setStretchFactor(0, 0); sp.setStretchFactor(1, 1)
            fl = QVBoxLayout(self._fs_dialog); fl.setContentsMargins(8, 8, 8, 8); fl.setSpacing(4)
            fl.addLayout(top); fl.addWidget(sp, stretch=1)
            self._fs_act.setIcon(codicon_icon("screen-normal"))
            self._fs_act.setText(tr('退出全屏'))
        else:
            self._fs_dialog.close(); self._fs_dialog = None
            self._fs_act.setIcon(codicon_icon("screen-full"))
            self._fs_act.setText(tr('全屏绘制'))

    def _show_context_menu(self, scene_pos: QPointF, view: SFCView = None):
        """在场景坐标 scene_pos 处弹出右键菜单。view 用于坐标映射（默认主视图）。"""
        if view is None:
            view = self.view
        item = self.scene.itemAt(scene_pos, view.transform())
        blk = item
        while blk is not None and not isinstance(blk, SFCBlock):
            blk = blk.parentItem() if hasattr(blk, 'parentItem') else None
        conn = item
        while conn is not None and not isinstance(conn, SFCConnection):
            conn = conn.parentItem() if hasattr(conn, 'parentItem') else None
        menu = QMenu(self)
        colors = theme_tokens(get_theme_manager().current_theme)
        menu.setStyleSheet("""
            QMenu {
                padding: 4px;
                color: %(text)s;
                background: %(surface_alt)s;
                border: 1px solid %(border)s;
            }
            QMenu::item { min-width: 170px; padding: 7px 28px 7px 9px; }
            QMenu::item:selected { color: %(text_strong)s; background: %(selection)s; }
            QMenu::separator { height: 1px; margin: 4px 7px; background: %(border)s; }
        """ % colors)
        if blk:
            menu.addAction(codicon_icon("edit"), tr('编辑名称')).triggered.connect(lambda: self._quick_edit(blk))
            menu.addAction(codicon_icon("tools"), tr('高级编辑')).triggered.connect(lambda: self._edit_block(blk))
            menu.addSeparator()
            menu.addAction(codicon_icon("copy"), tr('复制\tCtrl+C')).triggered.connect(self._copy)
            menu.addAction(codicon_icon("edit"), tr('剪切\tCtrl+X')).triggered.connect(self._cut)
            menu.addSeparator()
            menu.addAction(codicon_icon("trash"), tr('删除')).triggered.connect(lambda: self._del_block(blk))
        elif conn:
            menu.addAction(codicon_icon("paintcan"), tr('改变颜色')).triggered.connect(lambda: self._change_conn_color(conn))
            menu.addAction(codicon_icon("trash"), tr('删除连线')).triggered.connect(lambda: self.scene.removeItem(conn))
        else:
            menu.addAction(codicon_icon("copy"), tr('粘贴\tCtrl+V')).triggered.connect(self._paste)
            menu.addSeparator()
            menu.addAction(codicon_icon("symbol-field"), tr('在此添加步骤')).triggered.connect(lambda: self.add_block_at("step", scene_pos.x(), scene_pos.y()))
            menu.addAction(codicon_icon("arrow-right"), tr('在此添加转移条件')).triggered.connect(lambda: self.add_block_at("transition", scene_pos.x(), scene_pos.y()))
        vp = view.mapFromScene(scene_pos)
        menu.exec(view.viewport().mapToGlobal(vp))

    def contextMenuEvent(self, e):
        vp = self.view.viewport().mapFrom(self, e.pos())
        self._show_context_menu(self.view.mapToScene(vp))
