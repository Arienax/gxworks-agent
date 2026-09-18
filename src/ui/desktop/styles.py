"""Styles."""


QSS_TEMPLATE = """
/* Industrial control workstation design system */
QFrame#MainBgFrame {
    background-color: #e8eef5;
    border: 1px solid #94a3b8;
    border-radius: 12px;
}

#CustomTitleBar {
    background-color: #0f172a;
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
}
#AppMark {
    background-color: #0f766e;
    color: #ffffff;
    border-radius: 7px;
    font-family: "Segoe UI", "Microsoft YaHei";
    font-size: 12px;
    font-weight: 700;
}
#WindowTitleLabel {
    color: #f8fafc;
    font-family: "Segoe UI", "Microsoft YaHei";
    font-size: 14px;
    font-weight: 600;
}
#WindowSubtitle {
    color: #94a3b8;
    font-family: "Segoe UI", "Microsoft YaHei";
    font-size: 11px;
}

QLabel {
    color: #334155;
    font-family: "Segoe UI", "Microsoft YaHei";
    font-size: 13px;
}
#HeaderTitle {
    color: #0f172a;
    font-size: 21px;
    font-weight: 700;
}
#HeaderDescription {
    color: #64748b;
    font-size: 12px;
}
#SectionLabel {
    color: #334155;
    font-size: 12px;
    font-weight: 600;
}
#CanvasTitle {
    color: #0f172a;
    font-size: 16px;
    font-weight: 700;
}
#FormatBadge, #StatusBadge {
    background-color: #ecfdf5;
    color: #047857;
    border: 1px solid #a7f3d0;
    border-radius: 10px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
}
#HelperText {
    color: #64748b;
    font-size: 11px;
}

#ControlCard, #CanvasCard {
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 12px;
}
#CanvasCard {
    background-color: #ffffff;
}
#ToolbarSurface {
    background-color: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 9px;
}

QTextEdit {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 9px;
    padding: 12px;
    selection-background-color: #99f6e4;
    selection-color: #0f172a;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei";
    font-size: 13px;
}
QTextEdit:hover {
    border-color: #94a3b8;
}
QTextEdit:focus {
    border: 2px solid #0f766e;
    padding: 11px;
}
QTextEdit:read-only {
    background-color: #f8fafc;
}

QPushButton {
    min-height: 36px;
    padding: 0 14px;
    color: #334155;
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    font-family: "Segoe UI", "Microsoft YaHei";
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover {
    color: #0f172a;
    background-color: #f1f5f9;
    border-color: #94a3b8;
}
QPushButton:pressed {
    background-color: #e2e8f0;
}
QPushButton:focus {
    border: 2px solid #14b8a6;
}
QPushButton:disabled {
    color: #94a3b8;
    background-color: #e2e8f0;
    border-color: #e2e8f0;
}
#PrimaryButton {
    color: #ffffff;
    background-color: #0f766e;
    border: 1px solid #0f766e;
    font-size: 14px;
    font-weight: 700;
}
#PrimaryButton:hover {
    background-color: #0d9488;
    border-color: #0d9488;
}
#PrimaryButton:pressed {
    background-color: #115e59;
    border-color: #115e59;
}
#ModeToggleBtn {
    min-height: 32px;
    padding: 0 11px;
    color: #475569;
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    font-size: 11px;
    font-weight: 600;
}
#ModeToggleBtn:hover {
    color: #0f766e;
    border-color: #5eead4;
}
#ModeToggleBtn:checked {
    color: #0f766e;
    background-color: #ccfbf1;
    border-color: #5eead4;
}
#OptionsBtn {
    min-height: 30px;
    color: #cbd5e1;
    background-color: transparent;
    border: 1px solid #334155;
}
#OptionsBtn:hover {
    color: #ffffff;
    background-color: #1e293b;
    border-color: #475569;
}
#OptionsBtn::menu-indicator {
    image: none;
    width: 0px;
}
#MinBtn, #MaxBtn, #CloseBtn {
    min-width: 36px;
    min-height: 32px;
    padding: 0;
    color: #cbd5e1;
    background-color: transparent;
    border: none;
    border-radius: 6px;
    font-family: "Segoe UI Symbol", "Microsoft YaHei";
    font-size: 15px;
}
#MinBtn:hover, #MaxBtn:hover {
    color: #ffffff;
    background-color: #334155;
}
#CloseBtn:hover {
    color: #ffffff;
    background-color: #dc2626;
}

QComboBox {
    min-height: 34px;
    padding: 0 30px 0 11px;
    color: #0f172a;
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    font-family: "Segoe UI", "Microsoft YaHei";
    font-size: 12px;
}
QComboBox:hover {
    border-color: #94a3b8;
}
QComboBox:focus {
    border: 2px solid #0f766e;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border: none;
}
QComboBox QAbstractItemView {
    padding: 5px;
    color: #0f172a;
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    selection-color: #ffffff;
    selection-background-color: #0f766e;
    outline: none;
}

QMenu {
    padding: 6px;
    color: #0f172a;
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
}
QMenu::item {
    padding: 8px 28px 8px 12px;
    border-radius: 5px;
}
QMenu::item:selected {
    color: #0f766e;
    background-color: #ccfbf1;
}
QMenu::separator {
    height: 1px;
    margin: 5px 8px;
    background-color: #e2e8f0;
}

QSplitter::handle {
    background-color: transparent;
}
QSplitter::handle:horizontal {
    width: 8px;
}
QSplitter::handle:horizontal:hover {
    background-color: #cbd5e1;
}

QScrollArea, QSvgWidget {
    background-color: #ffffff;
    border: none;
}
QScrollArea {
    border: 1px solid #e2e8f0;
    border-radius: 9px;
}
QScrollBar:vertical {
    width: 9px;
    margin: 2px;
    background: #f1f5f9;
    border: none;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    min-height: 28px;
    background: #94a3b8;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #64748b;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    height: 9px;
    margin: 2px;
    background: #f1f5f9;
    border: none;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    min-width: 28px;
    background: #94a3b8;
    border-radius: 4px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

#ThinkingPanel {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 9px;
}
#ThinkingPanelHeader {
    background-color: #f8fafc;
    border-radius: 8px;
}
#ThinkingPanelToggle {
    min-height: 34px;
    padding: 0 10px;
    color: #334155;
    background-color: transparent;
    border: none;
    text-align: left;
    font-size: 12px;
    font-weight: 600;
}
#ThinkingPanelToggle:hover {
    color: #0f766e;
    background-color: transparent;
}
#ThinkingStatus {
    color: #64748b;
    background-color: #e2e8f0;
    border-radius: 9px;
    padding: 2px 8px;
    font-size: 10px;
    font-weight: 600;
}
#ThinkingPanelContent {
    color: #dbeafe;
    background-color: #0f172a;
    border: none;
    border-top: 1px solid #1e293b;
    border-bottom-left-radius: 8px;
    border-bottom-right-radius: 8px;
    padding: 10px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei";
    font-size: 12px;
}

QMessageBox, QDialog {
    background-color: #f8fafc;
}
QMessageBox QLabel {
    min-width: 260px;
    color: #0f172a;
    font-size: 13px;
}
QMessageBox QPushButton {
    min-width: 88px;
}
QToolTip {
    padding: 6px 8px;
    color: #f8fafc;
    background-color: #0f172a;
    border: 1px solid #334155;
}
"""


WORKBENCH_LIGHT_QSS = """
QMainWindow, QWidget#WorkbenchRoot {
    background: #eef2f6;
    color: #18212f;
    font-family: "Segoe UI", "Microsoft YaHei";
    font-size: 13px;
}
QFrame#TopBar, QFrame#Sidebar, QFrame#ConversationPane, QFrame#ArtifactPane {
    background: #ffffff;
    border: 1px solid #cbd5e1;
}
QFrame#TopBar { border-width: 0 0 1px 0; }
QFrame#Sidebar { border-radius: 8px; }
QFrame#ConversationPane, QFrame#ArtifactPane { border-radius: 8px; }
QWidget#ConversationContent { background: #f8fafc; }
QLabel#ProjectTitle { font-size: 17px; font-weight: 700; color: #0f172a; }
QLabel#PaneTitle { font-size: 14px; font-weight: 700; color: #0f172a; }
QLabel#SectionCaption, QLabel#MessageMeta {
    color: #64748b;
    font-size: 11px;
}
QPushButton {
    min-height: 32px;
    padding: 0 12px;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    background: #ffffff;
    color: #334155;
    font-weight: 600;
}
QPushButton:hover { background: #f1f5f9; border-color: #94a3b8; }
QPushButton:pressed { background: #e2e8f0; }
QPushButton:disabled { color: #94a3b8; background: #e2e8f0; }
QPushButton#PrimaryButton {
    color: #ffffff;
    background: #2563eb;
    border-color: #2563eb;
}
QPushButton#PrimaryButton:hover { background: #1d4ed8; }
QPushButton#SecondaryButton { background: #f8fafc; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
    color: #0f172a;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    padding: 6px 8px;
    selection-background-color: #bfdbfe;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 2px solid #2563eb;
}
QComboBox {
    min-height: 32px;
    padding: 0 40px 0 11px;
}
QComboBox:hover {
    border-color: #94a3b8;
    background: #ffffff;
}
QComboBox:focus,
QComboBox[popupOpen="true"] {
    border: 2px solid #2563eb;
    padding-left: 10px;
}
QComboBox:disabled {
    color: #94a3b8;
    background: #f1f5f9;
    border-color: #e2e8f0;
}
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 32px;
    margin: 0;
    background: transparent;
    border: none;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QComboBox QAbstractItemView {
    color: #1f2937;
    background: #ffffff;
    border: 1px solid #cbd5e1;
    outline: none;
    padding: 4px;
    selection-color: #1d4ed8;
    selection-background-color: #dbeafe;
}
QComboBox QAbstractItemView::item {
    min-height: 30px;
    padding: 3px 9px;
    color: #1f2937;
    background: #ffffff;
}
QComboBox QAbstractItemView::item:selected {
    color: #1d4ed8;
    background: #dbeafe;
}
QListWidget {
    background: transparent;
    color: #1f2937;
    border: none;
    outline: none;
}
QListWidget::item {
    min-height: 34px;
    padding: 4px 8px;
    border-radius: 5px;
    color: #1f2937;
    background: transparent;
}
QListWidget::item:hover { background: #f1f5f9; color: #0f172a; }
QListWidget::item:selected { background: #dbeafe; color: #1d4ed8; }
QTabWidget::pane {
    border: 1px solid #cbd5e1;
    background: #ffffff;
}
QScrollArea#LadderPreview,
QScrollArea#LadderPreview QWidget#qt_scrollarea_viewport,
QSvgWidget#LadderCanvas {
    background: #ffffff;
}
QTabBar::tab {
    min-width: 62px;
    padding: 7px 10px;
    color: #64748b;
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-bottom: none;
}
QTabBar::tab:selected {
    color: #1d4ed8;
    background: #ffffff;
    font-weight: 700;
}
QFrame#UserMessage {
    background: #e8f1ff;
    border: 1px solid #bfdbfe;
    border-radius: 8px;
    margin-left: 54px;
}
QFrame#AssistantMessage {
    background: #ffffff;
    border: 1px solid #d9e1ea;
    border-radius: 8px;
    margin-right: 54px;
}
QLabel#MessageAuthor { color: #475569; font-size: 11px; font-weight: 700; }
QLabel#MessageBody { color: #172033; line-height: 1.45; }
QFrame#ReviewCard {
    background: #fffdf5;
    border: 1px solid #e7c65f;
    border-radius: 8px;
}
QLabel#ReviewTitle { color: #713f12; font-size: 14px; font-weight: 700; }
QLabel#ReviewBadge {
    color: #854d0e;
    background: #fef3c7;
    border: 1px solid #fde68a;
    border-radius: 8px;
    padding: 2px 7px;
    font-size: 10px;
}
QLabel#ReviewSummary {
    color: #422006;
    background: #fffbeb;
    border-radius: 5px;
    padding: 8px;
}
QLabel#LockedSpec {
    color: #475569;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 5px;
    padding: 7px;
}
QGroupBox {
    color: #334155;
    font-weight: 700;
    border: 1px solid #d8dee8;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 12px;
}
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 5px; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:horizontal:hover { background: #cbd5e1; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical {
    width: 9px; background: #f1f5f9; border: none;
}
QScrollBar::handle:vertical {
    background: #94a3b8; min-height: 28px; border-radius: 4px;
}
#ThinkingPanelHeader {
    background: #172033;
    border-radius: 5px;
}
#ThinkingPanelToggle {
    color: #dbeafe;
    background: transparent;
    border: none;
    text-align: left;
}
#ThinkingStatus { color: #93c5fd; font-size: 11px; }
#ThinkingPanelContent {
    color: #d7e0ee;
    background: #111827;
    border: 1px solid #334155;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei";
    font-size: 11px;
}
"""


WORKBENCH_LIGHT_QSS += """
QMainWindow, QWidget#WorkbenchRoot { background: #f5f5f5; color: #1e1e1e; }
QFrame#TopBar { background: #f3f3f3; border: none; border-bottom: 1px solid #cccedb; }
QFrame#Sidebar { background: #f3f3f3; border: none; border-right: 1px solid #cccedb; border-radius: 0; }
QFrame#ConversationPane, QFrame#ArtifactPane { background: #ffffff; border: none; border-radius: 0; }
QWidget#ConversationContent { background: #ffffff; }
QLabel { color: #1e1e1e; }
QLabel#ProjectTitle, QLabel#PaneTitle { color: #1e1e1e; }
QLabel#TopBarLabel, QLabel#SectionCaption, QLabel#MessageMeta { color: #616161; }
QPushButton { color: #1e1e1e; background: #f3f3f3; border: 1px solid #cccedb; border-radius: 2px; font-weight: 400; }
QPushButton:hover { background: #e5f1fb; border-color: #9cc2e5; }
QPushButton:pressed { background: #cde8ff; }
QPushButton#PrimaryButton { color: #ffffff; background: #0078d4; border-color: #0078d4; }
QPushButton#ToolbarButton, QPushButton#ThemeButton { background: transparent; border-color: transparent; }
QPushButton#ToolbarButton:hover, QPushButton#ThemeButton:hover { background: #e5f1fb; border-color: transparent; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox { color: #1e1e1e; background: #ffffff; border: 1px solid #cccedb; border-radius: 2px; selection-color: #1e1e1e; selection-background-color: #cde8ff; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #0078d4; }
QListWidget::item:hover { background: #e5f1fb; color: #1e1e1e; }
QListWidget::item:selected { background: #cde8ff; color: #1e1e1e; }
QTabWidget::pane { border: none; border-top: 1px solid #cccedb; background: #ffffff; }
QTabBar::tab { color: #616161; background: #f3f3f3; border-color: #cccedb; }
QTabBar::tab:selected { color: #1e1e1e; background: #ffffff; border-top: 1px solid #0078d4; }
QFrame#UserMessage { background: #e5f1fb; border-color: #9cc2e5; border-radius: 4px; }
QFrame#AssistantMessage { background: #ffffff; border-color: #cccedb; border-radius: 4px; }
QScrollArea#ImageAttachmentStrip { background: #f7fbff; border: 1px solid #9cc2e5; }
QWidget#ImageAttachmentContent { background: #f7fbff; }
QFrame#ImageAttachmentCard { background: #ffffff; border: 1px solid #cccedb; border-radius: 3px; }
QLabel#ImageAttachmentPreview { background: #f3f3f3; border: 1px solid #e1e1e1; }
QLabel#ImageAttachmentName { color: #333333; font-size: 10px; }
QPushButton#ImageAttachmentRemove { color: #616161; background: transparent; border: none; padding: 0; font-size: 15px; }
QPushButton#ImageAttachmentRemove:hover { color: #ffffff; background: #c42b1c; }
QLabel#MessageBody { color: #1e1e1e; }
#ThinkingPanelHeader { background: #f3f3f3; border-top: 1px solid #cccedb; border-radius: 0; }
#ThinkingPanelToggle { color: #1e1e1e; }
#ThinkingStatus { color: #0066b8; }
#ThinkingPanelContent { color: #1e1e1e; background: #ffffff; border: 1px solid #cccedb; }
QStatusBar { min-height: 22px; max-height: 22px; color: #ffffff; background: #0078d4; border: none; }
QStatusBar QLabel { color: #ffffff; padding: 0 8px; font-size: 11px; }
QToolTip { color: #1e1e1e; background: #ffffff; border: 1px solid #cccedb; padding: 4px 6px; }
QMenu { padding: 4px; color: #1e1e1e; background: #ffffff; border: 1px solid #cccedb; }
QMenu::item { min-width: 150px; padding: 7px 28px 7px 9px; }
QMenu::item:selected { color: #1e1e1e; background: #cde8ff; }
QMenu::separator { height: 1px; margin: 4px 7px; background: #cccedb; }
QLabel#AppIcon { color: #0078d4; padding: 0 2px; }
QPushButton#WindowMinButton, QPushButton#WindowMaxButton, QPushButton#WindowCloseButton { min-width: 46px; max-width: 46px; min-height: 35px; max-height: 35px; padding: 0; color: #1e1e1e; background: transparent; border: none; border-radius: 0; }
QPushButton#WindowMinButton:hover, QPushButton#WindowMaxButton:hover { background: #e5f1fb; }
QPushButton#WindowCloseButton:hover { color: #ffffff; background: #c42b1c; }
QFrame#SimulationProgressPanel { background: #f7fbff; border: 1px solid #9cc2e5; }
QLabel#SimulationProgressTitle { color: #1e1e1e; font-weight: 600; }
QLabel#SimulationProgressCurrent { color: #333333; font-size: 11px; }
QProgressBar { min-height: 4px; max-height: 4px; background: #d9e7f5; border: none; }
QProgressBar::chunk { background: #0078d4; }
QPlainTextEdit#SimulationProgressLog {
    color: #333333;
    background: #ffffff;
    border: 1px solid #cccedb;
    padding: 4px 6px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei";
    font-size: 10px;
}
"""


WORKBENCH_DARK_QSS = """
QMainWindow, QWidget#WorkbenchRoot {
    background: #181818;
    color: #cccccc;
    font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 13px;
}
QFrame#TopBar {
    background: #181818;
    border: none;
    border-bottom: 1px solid #2b2b2b;
}
QFrame#Sidebar {
    background: #181818;
    border: none;
    border-right: 1px solid #2b2b2b;
    border-radius: 0;
}
QFrame#ConversationPane, QFrame#ArtifactPane {
    background: #1f1f1f;
    border: none;
    border-radius: 0;
}
QWidget#ConversationContent { background: #1f1f1f; }
QLabel { color: #cccccc; background: transparent; }
QLabel#ProjectTitle { font-size: 13px; font-weight: 600; color: #f0f0f0; }
QLabel#TopBarLabel { color: #9d9d9d; font-size: 11px; font-weight: 600; }
QLabel#PaneTitle { font-size: 11px; font-weight: 700; color: #cccccc; }
QLabel#SectionCaption, QLabel#MessageMeta { color: #9d9d9d; font-size: 11px; }
QPushButton {
    min-height: 28px;
    padding: 0 10px;
    border: 1px solid #3c3c3c;
    border-radius: 2px;
    background: #313131;
    color: #cccccc;
    font-weight: 400;
}
QPushButton:hover { background: #3c3c3c; border-color: #5a5a5a; }
QPushButton:pressed { background: #454545; }
QPushButton:focus { border: 1px solid #0078d4; }
QPushButton:disabled { color: #656565; background: #252525; border-color: #333333; }
QPushButton#PrimaryButton { color: #ffffff; background: #0e639c; border-color: #0e639c; }
QPushButton#PrimaryButton:hover { background: #1177bb; }
QPushButton#SecondaryButton { background: #313131; }
QPushButton#ToolbarButton, QPushButton#ThemeButton {
    min-height: 28px;
    padding: 0 9px;
    background: transparent;
    border-color: transparent;
}
QPushButton#ToolbarButton:hover, QPushButton#ThemeButton:hover { background: #2a2d2e; border-color: transparent; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
    color: #cccccc;
    background: #313131;
    border: 1px solid #3c3c3c;
    border-radius: 2px;
    padding: 6px 8px;
    selection-color: #ffffff;
    selection-background-color: #264f78;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
    border: 1px solid #0078d4;
}
QComboBox { min-height: 28px; padding: 0 36px 0 9px; }
QComboBox:hover { border-color: #6a6a6a; background: #353535; }
QComboBox:focus, QComboBox[popupOpen="true"] { border: 1px solid #0078d4; }
QComboBox:disabled { color: #656565; background: #252525; border-color: #333333; }
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 32px;
    margin: 0;
    background: transparent;
    border: none;
}
QComboBox::down-arrow { image: none; width: 0; height: 0; }
QComboBox QAbstractItemView {
    color: #cccccc;
    background: #252526;
    border: 1px solid #454545;
    outline: none;
    padding: 4px;
    selection-color: #ffffff;
    selection-background-color: #04395e;
}
QComboBox QAbstractItemView::item {
    min-height: 30px;
    padding: 3px 9px;
    color: #cccccc;
    background: #252526;
}
QComboBox QAbstractItemView::item:selected { color: #ffffff; background: #04395e; }
QListWidget { background: transparent; color: #cccccc; border: none; outline: none; }
QListWidget::item {
    min-height: 32px;
    padding: 3px 7px;
    border-radius: 0;
    color: #cccccc;
    background: transparent;
}
QListWidget::item:hover { background: #2a2d2e; color: #ffffff; }
QListWidget::item:selected { background: #37373d; color: #ffffff; }
QTabWidget::pane {
    border: none;
    border-top: 1px solid #2b2b2b;
    background: #1f1f1f;
}
QScrollArea#LadderPreview,
QScrollArea#LadderPreview QWidget#qt_scrollarea_viewport,
QSvgWidget#LadderCanvas { background: #181818; }
QTabBar::tab {
    min-width: 72px;
    min-height: 32px;
    padding: 0 12px;
    color: #9d9d9d;
    background: #181818;
    border: none;
    border-right: 1px solid #2b2b2b;
    border-top: 1px solid transparent;
}
QTabBar::tab:hover { color: #ffffff; background: #252526; }
QTabBar::tab:selected {
    color: #ffffff;
    background: #1f1f1f;
    border-top: 1px solid #0078d4;
}
QFrame#UserMessage {
    background: #20384d;
    border: 1px solid #315b7d;
    border-radius: 4px;
    margin-left: 42px;
}
QFrame#AssistantMessage {
    background: #252526;
    border: 1px solid #3c3c3c;
    border-radius: 4px;
    margin-right: 42px;
}
QScrollArea#ImageAttachmentStrip { background: #181818; border: 1px solid #3c3c3c; }
QWidget#ImageAttachmentContent { background: #181818; }
QFrame#ImageAttachmentCard { background: #252526; border: 1px solid #454545; border-radius: 3px; }
QLabel#ImageAttachmentPreview { background: #1e1e1e; border: 1px solid #3c3c3c; }
QLabel#ImageAttachmentName { color: #cccccc; font-size: 10px; }
QPushButton#ImageAttachmentRemove { color: #9d9d9d; background: transparent; border: none; padding: 0; font-size: 15px; }
QPushButton#ImageAttachmentRemove:hover { color: #ffffff; background: #c42b1c; }
QLabel#MessageAuthor { color: #9cdcfe; font-size: 11px; font-weight: 600; }
QLabel#MessageBody { color: #d4d4d4; }
QFrame#ReviewCard { background: #252526; border: 1px solid #cca700; border-radius: 4px; }
QLabel#ReviewTitle { color: #f0f0f0; font-size: 13px; font-weight: 600; }
QLabel#ReviewBadge {
    color: #f0d97a;
    background: #3d3318;
    border: 1px solid #6b5717;
    border-radius: 9px;
    padding: 2px 7px;
    font-size: 10px;
}
QLabel#ReviewSummary { color: #d4d4d4; background: #1f1f1f; border-radius: 2px; padding: 8px; }
QLabel#LockedSpec {
    color: #b8b8b8;
    background: #1f1f1f;
    border: 1px solid #3c3c3c;
    border-radius: 2px;
    padding: 7px;
}
QGroupBox {
    color: #cccccc;
    font-weight: 600;
    border: 1px solid #3c3c3c;
    border-radius: 2px;
    margin-top: 8px;
    padding-top: 12px;
}
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 5px; }
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 4px; }
QSplitter::handle:horizontal:hover { background: #0078d4; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { width: 10px; background: transparent; border: none; }
QScrollBar::handle:vertical { background: #424242; min-height: 28px; border-radius: 0; }
QScrollBar::handle:vertical:hover { background: #555555; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
#ThinkingPanelHeader {
    background: #181818;
    border-top: 1px solid #2b2b2b;
    border-radius: 0;
}
#ThinkingPanelToggle { color: #cccccc; background: transparent; border: none; text-align: left; }
#ThinkingPanelToggle:hover { color: #ffffff; background: #2a2d2e; }
#ThinkingStatus { color: #9cdcfe; font-size: 11px; }
#ThinkingPanelContent {
    color: #d4d4d4;
    background: #1e1e1e;
    border: none;
    border-top: 1px solid #2b2b2b;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei";
    font-size: 11px;
}
QStatusBar {
    min-height: 22px;
    max-height: 22px;
    color: #ffffff;
    background: #0078d4;
    border: none;
}
QStatusBar QLabel { color: #ffffff; padding: 0 8px; font-size: 11px; }
QStatusBar::item { border: none; }
QToolTip {
    color: #f0f0f0;
    background: #252526;
    border: 1px solid #454545;
    padding: 4px 6px;
}
QMenu {
    padding: 4px;
    color: #cccccc;
    background: #252526;
    border: 1px solid #454545;
}
QMenu::item {
    min-width: 150px;
    padding: 7px 28px 7px 9px;
    border-radius: 2px;
}
QMenu::item:selected {
    color: #ffffff;
    background: #04395e;
}
QMenu::separator {
    height: 1px;
    margin: 4px 7px;
    background: #454545;
}
QFrame#TopBar {
    background: #181818;
    border: 1px solid #2b2b2b;
    border-width: 0 0 1px 0;
}
QLabel#AppIcon {
    color: #23a8f2;
    padding: 0 2px;
}
QPushButton#WindowMinButton,
QPushButton#WindowMaxButton,
QPushButton#WindowCloseButton {
    min-width: 46px;
    max-width: 46px;
    min-height: 35px;
    max-height: 35px;
    padding: 0;
    color: #cccccc;
    background: transparent;
    border: none;
    border-radius: 0;
}
QPushButton#WindowMinButton:hover,
QPushButton#WindowMaxButton:hover {
    color: #ffffff;
    background: #2a2d2e;
}
QPushButton#WindowCloseButton:hover {
    color: #ffffff;
    background: #c42b1c;
}
QFrame#SimulationProgressPanel { background: #181818; border: 1px solid #3c3c3c; }
QLabel#SimulationProgressTitle { color: #f0f0f0; font-weight: 600; }
QLabel#SimulationProgressCurrent { color: #cccccc; font-size: 11px; }
QProgressBar { min-height: 4px; max-height: 4px; background: #333333; border: none; }
QProgressBar::chunk { background: #0078d4; }
QPlainTextEdit#SimulationProgressLog {
    color: #cccccc;
    background: #1e1e1e;
    border: 1px solid #3c3c3c;
    padding: 4px 6px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei";
    font-size: 10px;
}
"""


WORKBENCH_GEOMETRY_QSS = """
QMainWindow, QWidget#WorkbenchRoot {
    font-family: "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 13px;
}
QFrame#Sidebar, QFrame#ConversationPane, QFrame#ArtifactPane { border-radius: 0; }
QLabel#ProjectTitle { font-size: 13px; font-weight: 600; }
QLabel#TopBarLabel { font-size: 11px; font-weight: 600; }
QLabel#PaneTitle { font-size: 11px; font-weight: 700; }
QLabel#SectionCaption, QLabel#MessageMeta { font-size: 11px; }
QPushButton {
    min-height: 28px;
    padding: 0 10px;
    border-width: 1px;
    border-radius: 2px;
    font-weight: 400;
}
QPushButton#ToolbarButton, QPushButton#ThemeButton {
    min-height: 28px;
    padding: 0 9px;
    background: transparent;
    border-color: transparent;
}
QPushButton#ThemeButton {
    min-width: 32px;
    max-width: 32px;
    padding: 0;
    font-family: "Segoe UI Symbol";
    font-size: 16px;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
    border-width: 1px;
    border-radius: 2px;
    padding: 6px 8px;
}
QComboBox { min-height: 28px; padding: 0 36px 0 9px; }
QListWidget::item { min-height: 32px; padding: 3px 7px; border-radius: 0; }
QTabBar::tab { min-width: 72px; min-height: 32px; padding: 0 12px; }
QFrame#UserMessage { margin-left: 42px; border-radius: 4px; }
QFrame#AssistantMessage { margin-right: 42px; border-radius: 4px; }
QSplitter::handle:horizontal { width: 4px; }
QScrollBar:vertical { width: 10px; border-radius: 0; }
QScrollBar::handle:vertical { min-height: 28px; border-radius: 0; }
"""


WORKBENCH_LIGHT_QSS += WORKBENCH_GEOMETRY_QSS


WORKBENCH_DARK_QSS += WORKBENCH_GEOMETRY_QSS

