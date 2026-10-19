"""Bind an IDE language to its actually loaded native resource modules."""
from __future__ import annotations

from pathlib import Path
import re

from gxw.models import GXWFormatError
from gxw.native_locale import ui_text


class NativeLanguageNotReadyError(GXWFormatError):
    """A coherent startup snapshot is still missing required satellites."""


def language_from_satellites(paths, executable):
    directory = str(Path(executable).resolve().parent).casefold()
    required = {'gd2', 'gd2mainfrmnavigation', 'gd2shared'}
    # Optional pages load lazily. They must agree with the frame as soon as
    # loaded; a coherent initial frame does not make a later mixed page safe.
    operational = required | {'gd2datamng', 'gd2ctrl', 'gd2wndoprheader',
        'gd2comuiproject', 'gd2comuidata', 'gd2comuidevrange', 'gd2comuifbinstance',
        'gd2comuiapp', 'gd2comuioption', 'gd2mainfrmcompileresult', 'gd2mainfrmcrossref',
        'ed_st', 'ed_fbd', 'basiclib'}
    languages = {1252: 'en', 932: 'ja', 936: 'zh-CN'}
    observed = {}
    for path in paths:
        path = Path(path)
        if str(path.resolve().parent).casefold() != directory:
            continue
        match = re.fullmatch(r'(.+)_rc_([0-9]+)\.dll', path.name, re.IGNORECASE)
        if match and match[1].lower() in operational:
            language = languages.get(int(match[2]))
            if language is None:
                raise GXWFormatError('Loaded native resource language has no verified vocabulary')
            previous = observed.setdefault(match[1].lower(), language)
            if previous != language:
                raise GXWFormatError('Multiple native UI resource languages are loaded')
    if len(set(observed.values())) > 1:
        raise GXWFormatError('Native UI resource language is missing, mixed, or ambiguous')
    if not required <= observed.keys():
        raise NativeLanguageNotReadyError('Required native UI resource satellites are not loaded yet')
    return next(iter(observed.values()))


def inspect_native_language(process_id, executable):
    import pywintypes
    import win32api
    import win32process
    handle = win32api.OpenProcess(0x410, False, process_id)
    try:
        # LIST_MODULES_ALL is necessary for the 32-bit IDE under 64-bit Python.
        modules = win32process.EnumProcessModulesEx(handle, 3)
        paths = []
        for module in modules:
            try:
                paths.append(win32process.GetModuleFileNameEx(handle, module))
            except (OSError, pywintypes.error) as error:
                # A transient helper DLL can unload after enumeration. Missing
                # required satellites still fail consensus below; never infer
                # a language from a failed or incomplete required observation.
                if getattr(error, 'winerror', error.args[0] if error.args else None) not in (6, 299):
                    raise
    finally:
        handle.Close()
    return language_from_satellites(paths, executable)


_CAPTIONS = {
    '新建': 'dialog_new', '新建数据': 'dialog_new_data', '打开工程': 'dialog_open',
    '工程另存为': 'dialog_save_as', '写入至CSV文件': 'dialog_csv_write',
    '从CSV文件读取': 'dialog_csv_read', '标签登录/选择': 'dialog_label',
    '自动分配软元件设置': 'dialog_allocation', 'FB实例选择': 'dialog_instance',
    '结构化工程': 'project_structured', '结构化梯形图/FBD': 'language_fbd',
    '<全部>': 'chooser_all', '程序': 'nav_programs', '全局标签': 'nav_globals',
    '结构体': 'nav_structures', 'MELSOFT系列 GX Works2': 'application',
    '删除光标行。确定吗？': 'delete_cursor_row', '是否执行全部编译？': 'compile_confirm',
    '将当前显示的标签信息写入至CSV文件。': 'csv_write_confirm',
    '读取指定的文件内容。': 'csv_read_confirm',
    '交叉参照': 'cross_reference', '指令输入': 'dialog_instruction', '确定': 'ok', '取消': 'cancel',
    '跳转': 'dialog_jump', '梯形图块号': 'ladder_block_no', '解除选择(&C)': 'instance_clear',
    '梯形图图形重叠。是否继续处理？': 'graph_overlap', '是(&Y)': 'yes', '否(&N)': 'no',
}


def native_caption(chinese, language):
    if chinese in _CAPTIONS:
        return ui_text(_CAPTIONS[chinese], language)
    if language == 'zh-CN' or not re.search(r'[\u3400-\u9fff]', chinese):
        return chinese
    raise GXWFormatError('Native operational caption has no verified localized resource identity')


def standard_yes_no_captions(yes, no):
    """A Windows message box may use the host UI language inside another IDE UI.

    The caller must already bind the exact message, owner and IDYES/IDNO
    controls. Both captions must be one coherent supported language pair.
    """
    return (yes, no) in {
        (ui_text('yes', language), ui_text('no', language))
        for language in ('zh-CN', 'en', 'ja')
    }


def native_accelerator(key, language):
    """Read one accelerator from the selected native resource caption."""
    keys = re.findall(r'(?<!&)&([A-Za-z0-9])', ui_text(key, language))
    if len(keys) != 1:
        raise GXWFormatError('Native menu accelerator is missing or ambiguous')
    return keys[0].lower()


def native_menu_selection(menu_key, item_key, language):
    """Use each native menu's own accelerator and Codejock display caption."""
    menu = ui_text(menu_key, language)
    caption = menu.replace('&&', '\0').replace('&', '').replace('\0', '&')
    return [('%' + native_accelerator(menu_key, language), caption)], native_accelerator(item_key, language)


def native_tree_path(path, language):
    """Translate structural slots only; user-owned object names stay exact."""
    result = list(path)
    if not result:
        return tuple(result)
    first = {'全局标签': 'nav_globals', '程序部件': 'nav_pous', '程序设置': 'nav_settings'}
    if result[0] not in first:
        raise GXWFormatError('Unsupported native navigation root')
    root = result[0]
    result[0] = ui_text(first[root], language)
    if root == '程序部件' and len(result) > 1:
        second = {'程序': 'nav_programs', '结构体': 'nav_structures'}
        if result[1] in second:
            result[1] = ui_text(second[result[1]], language)
        elif result[1] != 'FB/FUN':
            raise GXWFormatError('Unsupported native POU folder')
        if len(result) > 3:
            key = {'程序本体': 'nav_body', '局部标签': 'nav_labels'}.get(result[3])
            if key is None:
                raise GXWFormatError('Unsupported native POU page')
            result[3] = ui_text(key, language)
    elif root == '程序设置' and len(result) > 1:
        buckets = {'执行程序': 'nav_execution', '初始程序': 'nav_initial_program',
            '扫描程序': 'nav_scan_program', '待机程序': 'nav_standby_program',
            '恒定周期程序': 'nav_fixed_scan_program', '无执行类型指定': 'nav_no_execution_type'}
        if result[1] not in buckets:
            raise GXWFormatError('Unsupported native task folder')
        result[1] = ui_text(buckets[result[1]], language)
    return tuple(result)
