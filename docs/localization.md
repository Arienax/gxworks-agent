# 本地化

界面支持的语言与规范化规则由 [shared.i18n.normalize_language](../src/shared/i18n.py)维护。Python 本地提示使用 `tr`、`translate` 和 `runtime_text`；前端使用 [web/src/i18n.ts](../web/src/i18n.ts)。翻译资料在 [resources/locales](../resources/locales/)。

新增本地提示时复用现有键和格式化方式，保留占位符的名称、顺序及类型。翻译用户可见名称，不修改地址、指令、稳定 ID、枚举比较值或协议字段。用户输入、手册引文、已保存注释及模型原文不做批量翻译。

模型新写文本的语言由[响应语言契约](architecture/response-language.md)管理，与本地 UI 字典分开。后台任务使用提交时的语言快照，页面设置变化不改写正在运行的任务。

测试入口是 [test_i18n.py](../tests/test_i18n.py)、[test_display_names.py](../tests/test_display_names.py)及 [test_language_workflows.py](../tests/test_language_workflows.py)。运行后按实际结果记录缺键、占位符错误和跳过项。Qt 字符串迁移经过保存在[过程目录](process/README.md)。
