# 响应语言

## 语言快照与接受边界

应用工作流提交时捕获响应语言，多阶段调用沿用同一快照。语言上下文由 [shared.i18n.language_context](../../src/shared/i18n.py)与 `language_scoped` 管理。模型请求把目标语言写入消息；应用通过 [model_runtime.provider.collect_response](../../src/model_runtime/provider.py)接受响应。

字段声明在 [application.response_contracts](../../src/application/response_contracts.py)，检查规则在 [model_runtime.responses.inspect_response](../../src/model_runtime/responses.py)。增加可见字段时更新对应字段契约，不能把所有字符串统一翻译。

## 字段规则

新写的摘要、说明和程序注释按其声明的自然语言角色检查。地址、指令、代码、标识符、枚举、参数比较值、证据及明确保留的旧注释保持原样。ST 检查提取注释而不改写代码或字符串字面量。

正文或工具参数验收失败返回 `ResponseRejectedError`，保留诊断，不写入已接受的回答或候选。推理通道单独检查；不合格推理可以隐藏而不拒绝合格正文。供应商工具协议需要回放的原始推理保存在私有字段，仅由适配器发送，公开视图不展示。

完整响应验收后发布对应事件。语言失败与传输失败分别处理；传输降级遵循[调用契约](generation-call-contracts.md)。显示层保留用户和已接受模型文本，不重新翻译程序或证据。

## 识别范围

检查器使用文字脚本、句段和技术 token 规则。短专名、细粒度混语以及中日共用汉字存在歧义；日文纯汉字标签可能报告 `ambiguous_han_only`。明确的协议字段和引述按数据角色保留，未声明字段需由工作流作者补充契约。

外部 MCP 客户端拥有自己的模型响应与聊天语言；其工具输入仍经过工程工具校验。界面本地化见[本地化约定](../localization.md)。历史根因、尝试和原始测试记录见[过程目录](../process/README.md)。

[test_response_language.py](../../tests/test_response_language.py)、[test_language_workflows.py](../../tests/test_language_workflows.py)与 [test_model_provider.py](../../tests/test_model_provider.py)覆盖语言快照、字段角色、响应接受和原始协议保留。
