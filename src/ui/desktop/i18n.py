"""Bind explicitly marked application text to Qt presentation properties.

Plain strings (user text, addresses, paths, editor contents and item data) are
untouched. Binding at the shared Qt boundary also covers retained legacy panels.
"""
from __future__ import annotations

import weakref
from pathlib import Path

from shared.i18n import LocalizedText, get_language, on_language_changed


_bindings = {}
_installed = False


def _bind(owner, key, setter, args):
    identity = id(owner)
    if identity not in _bindings:
        _bindings[identity] = (weakref.ref(owner, lambda _ref: _bindings.pop(identity, None)), {})
    _bindings[identity][1][key] = (setter, args)


def _render(value):
    if isinstance(value, LocalizedText):
        return str(value)
    if isinstance(value, (list, tuple)):
        return type(value)(_render(item) for item in value)
    return value


def _marked(value):
    return isinstance(value, LocalizedText) or isinstance(value, (list, tuple)) and any(_marked(item) for item in value)


def retranslate_widgets(_language=None):
    for identity, (reference, bindings) in list(_bindings.items()):
        owner = reference()
        if owner is None:
            _bindings.pop(identity, None)
            continue
        try:
            signal_owner = owner
            if not hasattr(signal_owner, "blockSignals"):
                # Item text changes emit signals from the owning view. A locale
                # switch must not be handled as an edit to the user's draft.
                parent_view = getattr(owner, "tableWidget", None) or getattr(owner, "listWidget", None)
                signal_owner = parent_view() if parent_view else None
            previous = signal_owner.blockSignals(True) if signal_owner is not None and hasattr(signal_owner, "blockSignals") else None
            try:
                for setter, args in list(bindings.values()):
                    setter(owner, *(_render(arg) for arg in args))
            finally:
                if previous is not None:
                    signal_owner.blockSignals(previous)
        except RuntimeError:
            # A deferred Qt deletion may precede collection of its Python wrapper.
            _bindings.pop(identity, None)


def install_qt_i18n(namespace):
    global _installed
    if _installed:
        return
    _installed = True

    def property_method(cls, name, indexed=False):
        original = getattr(cls, name, None)
        if original is None:
            return

        def wrapped(self, *args, **kwargs):
            key = (name, args[0]) if indexed and args else name
            if any(_marked(arg) for arg in args):
                _bind(self, key, original, args)
            elif id(self) in _bindings:
                _bindings[id(self)][1].pop(key, None)
            return original(self, *(_render(arg) for arg in args), **kwargs)

        setattr(cls, name, wrapped)

    properties = {
        "QWidget": ("setWindowTitle", "setToolTip", "setStatusTip", "setAccessibleName", "setAccessibleDescription"),
        "QLabel": ("setText",),
        "QAbstractButton": ("setText",),
        "QGroupBox": ("setTitle",),
        "QLineEdit": ("setText", "setPlaceholderText"),
        "QTextEdit": ("setPlaceholderText", "setPlainText", "setHtml"),
        "QPlainTextEdit": ("setPlaceholderText", "setPlainText"),
        "QAction": ("setText", "setToolTip", "setStatusTip"),
        "QMenu": ("setTitle",),
        "QListWidgetItem": ("setText", "setToolTip"),
        "QTableWidgetItem": ("setText", "setToolTip"),
        "QGraphicsTextItem": ("setPlainText", "setHtml"),
        "QGraphicsSimpleTextItem": ("setText",),
        "QTableWidget": ("setHorizontalHeaderLabels", "setVerticalHeaderLabels"),
        "QProgressBar": ("setFormat",),
    }
    for class_name, names in properties.items():
        cls = namespace.get(class_name)
        if cls is not None:
            for name in names:
                property_method(cls, name)
    for class_name, methods in {
        "QTabWidget": ("setTabText", "setTabToolTip"),
        "QComboBox": ("setItemText",),
    }.items():
        for name in methods:
            property_method(namespace[class_name], name, indexed=True)

    def constructor(cls, setter_name):
        original = cls.__init__
        def wrapped(self, *args, **kwargs):
            original(self, *(_render(arg) for arg in args), **kwargs)
            for arg in args:
                if isinstance(arg, LocalizedText):
                    getattr(self, setter_name)(arg)
                    break
        cls.__init__ = wrapped

    for class_name, setter in {
        "QLabel": "setText", "QPushButton": "setText", "QCheckBox": "setText",
        "QRadioButton": "setText", "QGroupBox": "setTitle", "QAction": "setText",
        "QMenu": "setTitle", "QListWidgetItem": "setText", "QTableWidgetItem": "setText",
    }.items():
        constructor(namespace[class_name], setter)

    def insertion(cls, name, setter_name, index_from_result=False):
        original = getattr(cls, name)
        def wrapped(self, *args, **kwargs):
            result = original(self, *(_render(arg) for arg in args), **kwargs)
            index = result if index_from_result else self.count() - 1
            for arg in args:
                if isinstance(arg, LocalizedText):
                    getattr(self, setter_name)(index, arg)
                    break
            return result
        setattr(cls, name, wrapped)

    insertion(namespace["QComboBox"], "addItem", "setItemText")
    insertion(namespace["QTabWidget"], "addTab", "setTabText", True)
    combo = namespace["QComboBox"]
    def add_items(self, values):
        for value in values:
            self.addItem(value)
    combo.addItems = add_items
    original_clear = combo.clear
    def clear(self):
        if id(self) in _bindings:
            reference, bindings = _bindings[id(self)]
            _bindings[id(self)] = (reference, {key: value for key, value in bindings.items() if not isinstance(key, tuple)})
        return original_clear(self)
    combo.clear = clear

    # QListWidget's string overload constructs its item in C++, bypassing the
    # marked-text constructor. Create a Python item so its source stays bound.
    list_widget = namespace["QListWidget"]
    original_list_add = list_widget.addItem
    def add_list_item(self, value):
        if isinstance(value, LocalizedText):
            value = namespace["QListWidgetItem"](value)
        return original_list_add(self, value)
    list_widget.addItem = add_list_item
    def add_list_items(self, values):
        for value in values:
            self.addItem(value)
    list_widget.addItems = add_list_items

    # C++ convenience APIs create actions/labels internally, bypassing Python
    # constructors. Retain the marked source on their resulting widgets too.
    for method_name in ("addAction", "addMenu"):
        original = getattr(namespace["QMenu"], method_name)
        def menu_add(self, *args, _original=original, **kwargs):
            result = _original(self, *(_render(arg) for arg in args), **kwargs)
            for arg in args:
                if isinstance(arg, LocalizedText):
                    if hasattr(result, "setTitle"):
                        result.setTitle(arg)
                    elif hasattr(result, "setText"):
                        result.setText(arg)
                    break
            return result
        setattr(namespace["QMenu"], method_name, menu_add)

    form = namespace["QFormLayout"]
    original_row = form.addRow
    def add_row(self, *args):
        if args and isinstance(args[0], LocalizedText):
            args = (namespace["QLabel"](args[0]), *args[1:])
        return original_row(self, *args)
    form.addRow = add_row

    class StandardButtonTranslator(namespace["QTranslator"]):
        def __init__(self, parent):
            super().__init__(parent)
            info = namespace["QLibraryInfo"]
            directory = (info.path(info.LibraryPath.TranslationsPath)
                         if hasattr(info, "LibraryPath") else info.location(info.TranslationsPath))
            self.catalogs = {}
            for language, suffix in (("zh-CN", "zh_CN"), ("ja", "ja")):
                translator = namespace["QTranslator"](self)
                if translator.load(str(Path(directory) / ("qtbase_" + suffix + ".qm"))):
                    self.catalogs[language] = translator

        labels = {
            "OK": ("确定", "OK"), "Cancel": ("取消", "キャンセル"),
            "Yes": ("是", "はい"), "No": ("否", "いいえ"),
            "Close": ("关闭", "閉じる"), "Save": ("保存", "保存"),
            "Open": ("打开", "開く"), "Apply": ("应用", "適用"),
            "Reset": ("重置", "リセット"), "Retry": ("重试", "再試行"),
            "Abort": ("中止", "中止"), "Ignore": ("忽略", "無視"),
            "Discard": ("放弃", "破棄"), "Help": ("帮助", "ヘルプ"),
            "Cut": ("剪切", "切り取り"), "Copy": ("复制", "コピー"),
            "Paste": ("粘贴", "貼り付け"), "Delete": ("删除", "削除"),
            "Select All": ("全选", "すべて選択"), "Undo": ("撤销", "元に戻す"),
            "Redo": ("重做", "やり直す"),
            "Look in:": ("查找范围：", "場所："),
            "Files of type:": ("文件类型：", "ファイルの種類："),
            "File name:": ("文件名：", "ファイル名："),
            "Save in:": ("保存位置：", "保存先："),
        }
        def translate(self, context, source, disambiguation=None, n=-1):
            selected = get_language()
            translator = self.catalogs.get(selected)
            if translator is not None:
                translated = translator.translate(context, source, disambiguation, n)
                if translated:
                    return translated
            labels = self.labels.get(source.replace("&", ""))
            if selected == "en" or labels is None:
                return None
            return labels[0 if selected == "zh-CN" else 1]

    application = namespace["QApplication"]
    original_app_init = application.__init__
    def app_init(self, *args, **kwargs):
        original_app_init(self, *args, **kwargs)
        self._i18n_translator = StandardButtonTranslator(self)
        self.installTranslator(self._i18n_translator)
    application.__init__ = app_init
    # A native OS file picker follows Windows' language, not the app preference.
    # Use Qt's translated picker consistently, including image attachment dialogs.
    file_dialog = namespace["QFileDialog"]
    for name in ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"):
        original = getattr(file_dialog, name)
        option_index = 3 if name == "getExistingDirectory" else 5
        def choose_file(*args, _original=original, _option_index=option_index, **kwargs):
            flag = file_dialog.Option.DontUseNativeDialog
            if len(args) > _option_index:
                args = list(args)
                args[_option_index] |= flag
            else:
                kwargs["options"] = kwargs.get("options", flag) | flag
            return _original(*args, **kwargs)
        setattr(file_dialog, name, staticmethod(choose_file))
    on_language_changed(retranslate_widgets)
