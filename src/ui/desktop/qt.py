"""Small PyQt6/PyQt5 compatibility surface for Win10+ and Win7 builds."""

from types import SimpleNamespace


try:
    from PyQt6.QtCore import *
    from PyQt6.QtGui import *
    from PyQt6.QtWidgets import *
    from PyQt6.QtSvgWidgets import QSvgWidget

    USING_QT6 = True
except ImportError:
    from PyQt5.QtCore import *
    from PyQt5.QtGui import *
    from PyQt5.QtWidgets import *
    from PyQt5.QtSvg import QSvgWidget

    USING_QT6 = False


def _namespace(owner, name, **values):
    if not hasattr(owner, name):
        setattr(owner, name, SimpleNamespace(**values))


if not USING_QT6:
    _namespace(QAbstractItemView, "SelectionBehavior", SelectRows=QAbstractItemView.SelectRows)
    _namespace(QAbstractItemView, "SelectionMode", SingleSelection=QAbstractItemView.SingleSelection)
    _namespace(QColorDialog, "ColorDialogOption", DontUseNativeDialog=QColorDialog.DontUseNativeDialog)
    _namespace(QComboBox, "InsertPolicy", NoInsert=QComboBox.NoInsert)
    _namespace(QDialog, "DialogCode", Accepted=QDialog.Accepted, Rejected=QDialog.Rejected)
    _namespace(QDialogButtonBox, "StandardButton", Ok=QDialogButtonBox.Ok, Cancel=QDialogButtonBox.Cancel)
    _namespace(QEvent, "Type", Resize=QEvent.Resize, Show=QEvent.Show, WindowStateChange=QEvent.WindowStateChange)
    _namespace(QFileDialog, "AcceptMode", AcceptOpen=QFileDialog.AcceptOpen, AcceptSave=QFileDialog.AcceptSave)
    _namespace(QFileDialog, "FileMode", ExistingFile=QFileDialog.ExistingFile)
    _namespace(QFileDialog, "Option", DontUseNativeDialog=QFileDialog.DontUseNativeDialog)
    _namespace(QGraphicsItem, "GraphicsItemChange", ItemPositionHasChanged=QGraphicsItem.ItemPositionHasChanged)
    _namespace(
        QGraphicsItem,
        "GraphicsItemFlag",
        ItemIsMovable=QGraphicsItem.ItemIsMovable,
        ItemIsSelectable=QGraphicsItem.ItemIsSelectable,
        ItemSendsGeometryChanges=QGraphicsItem.ItemSendsGeometryChanges,
    )
    _namespace(QGraphicsView, "DragMode", NoDrag=QGraphicsView.NoDrag)
    _namespace(QGraphicsView, "ViewportUpdateMode", FullViewportUpdate=QGraphicsView.FullViewportUpdate)
    _namespace(QHeaderView, "ResizeMode", Interactive=QHeaderView.Interactive, Stretch=QHeaderView.Stretch)
    _namespace(QIcon, "Mode", Active=QIcon.Active, Disabled=QIcon.Disabled, Selected=QIcon.Selected)
    _namespace(QKeySequence, "StandardKey", Copy=QKeySequence.Copy, Cut=QKeySequence.Cut, Paste=QKeySequence.Paste)
    _namespace(
        QLineEdit,
        "EchoMode",
        Normal=QLineEdit.Normal,
        Password=QLineEdit.Password,
    )
    _namespace(QMessageBox, "StandardButton", Yes=QMessageBox.Yes, No=QMessageBox.No)
    _namespace(QPainter, "RenderHint", Antialiasing=QPainter.Antialiasing)
    _namespace(
        QPalette,
        "ColorRole",
        AlternateBase=QPalette.AlternateBase,
        Base=QPalette.Base,
        Button=QPalette.Button,
        ButtonText=QPalette.ButtonText,
        Highlight=QPalette.Highlight,
        HighlightedText=QPalette.HighlightedText,
        Text=QPalette.Text,
        ToolTipBase=QPalette.ToolTipBase,
        ToolTipText=QPalette.ToolTipText,
        Window=QPalette.Window,
        WindowText=QPalette.WindowText,
    )
    _namespace(QPlainTextEdit, "LineWrapMode", NoWrap=QPlainTextEdit.NoWrap)
    _namespace(
        QSizePolicy,
        "Policy",
        Expanding=QSizePolicy.Expanding,
        Fixed=QSizePolicy.Fixed,
        Maximum=QSizePolicy.Maximum,
        Minimum=QSizePolicy.Minimum,
        Preferred=QSizePolicy.Preferred,
    )
    _namespace(QStandardPaths, "StandardLocation", AppDataLocation=QStandardPaths.AppDataLocation)
    _namespace(
        QTextCursor,
        "MoveOperation",
        Start=QTextCursor.Start,
        End=QTextCursor.End,
    )

    Qt = SimpleNamespace(
        AlignmentFlag=SimpleNamespace(
            AlignCenter=Qt.AlignCenter,
            AlignHCenter=Qt.AlignHCenter,
            AlignRight=Qt.AlignRight,
            AlignTop=Qt.AlignTop,
            AlignVCenter=Qt.AlignVCenter,
        ),
        AspectRatioMode=SimpleNamespace(KeepAspectRatio=Qt.KeepAspectRatio),
        BrushStyle=SimpleNamespace(NoBrush=Qt.NoBrush),
        ContextMenuPolicy=SimpleNamespace(CustomContextMenu=Qt.CustomContextMenu),
        CursorShape=SimpleNamespace(
            ArrowCursor=Qt.ArrowCursor,
            ClosedHandCursor=Qt.ClosedHandCursor,
            CrossCursor=Qt.CrossCursor,
            PointingHandCursor=Qt.PointingHandCursor,
            SizeBDiagCursor=Qt.SizeBDiagCursor,
            SizeFDiagCursor=Qt.SizeFDiagCursor,
            SizeHorCursor=Qt.SizeHorCursor,
            SizeVerCursor=Qt.SizeVerCursor,
        ),
        DropAction=SimpleNamespace(CopyAction=Qt.CopyAction),
        Edge=SimpleNamespace(
            BottomEdge=Qt.BottomEdge,
            LeftEdge=Qt.LeftEdge,
            RightEdge=Qt.RightEdge,
            TopEdge=Qt.TopEdge,
        ),
        FocusPolicy=SimpleNamespace(StrongFocus=Qt.StrongFocus),
        GlobalColor=SimpleNamespace(transparent=Qt.transparent),
        ItemDataRole=SimpleNamespace(UserRole=Qt.UserRole),
        ItemFlag=SimpleNamespace(ItemIsEditable=Qt.ItemIsEditable),
        ItemSelectionMode=SimpleNamespace(IntersectsItemShape=Qt.IntersectsItemShape),
        Key=SimpleNamespace(Key_C=Qt.Key_C, Key_V=Qt.Key_V, Key_X=Qt.Key_X),
        KeyboardModifier=SimpleNamespace(ControlModifier=Qt.ControlModifier),
        MouseButton=SimpleNamespace(LeftButton=Qt.LeftButton, MiddleButton=Qt.MiddleButton),
        Orientation=SimpleNamespace(Horizontal=Qt.Horizontal),
        PenCapStyle=SimpleNamespace(RoundCap=Qt.RoundCap),
        PenJoinStyle=SimpleNamespace(RoundJoin=Qt.RoundJoin),
        PenStyle=SimpleNamespace(
            DashLine=Qt.DashLine,
            NoPen=Qt.NoPen,
            SolidLine=Qt.SolidLine,
        ),
        ScrollBarPolicy=SimpleNamespace(
            ScrollBarAlwaysOff=Qt.ScrollBarAlwaysOff,
            ScrollBarAsNeeded=Qt.ScrollBarAsNeeded,
        ),
        SortOrder=SimpleNamespace(DescendingOrder=Qt.DescendingOrder),
        TextElideMode=SimpleNamespace(ElideRight=Qt.ElideRight),
        TextInteractionFlag=SimpleNamespace(
            TextSelectableByKeyboard=Qt.TextSelectableByKeyboard,
            TextSelectableByMouse=Qt.TextSelectableByMouse,
        ),
        ToolButtonStyle=SimpleNamespace(ToolButtonTextBesideIcon=Qt.ToolButtonTextBesideIcon),
        TransformationMode=SimpleNamespace(
            SmoothTransformation=Qt.SmoothTransformation
        ),
        WidgetAttribute=SimpleNamespace(
            WA_StyledBackground=Qt.WA_StyledBackground,
            WA_TranslucentBackground=Qt.WA_TranslucentBackground,
        ),
        WindowType=SimpleNamespace(
            FramelessWindowHint=Qt.FramelessWindowHint,
            Widget=Qt.Widget,
            WindowContextHelpButtonHint=Qt.WindowContextHelpButtonHint,
            WindowMaximizeButtonHint=Qt.WindowMaximizeButtonHint,
            WindowMinimizeButtonHint=Qt.WindowMinimizeButtonHint,
            WindowSystemMenuHint=Qt.WindowSystemMenuHint,
        ),
    )

    for cls in (QApplication, QDialog, QMenu, QDrag):
        if not hasattr(cls, "exec") and hasattr(cls, "exec_"):
            cls.exec = cls.exec_

    for cls in (QMouseEvent, QDropEvent, QDragEnterEvent, QWheelEvent):
        if not hasattr(cls, "position") and hasattr(cls, "pos"):
            cls.position = lambda self: QPointF(self.pos())
        if not hasattr(cls, "globalPosition") and hasattr(cls, "globalPos"):
            cls.globalPosition = lambda self: QPointF(self.globalPos())


from ui.desktop.i18n import install_qt_i18n

install_qt_i18n(globals())
