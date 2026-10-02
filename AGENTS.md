# GXWorks Agent：贡献约定

GXWorks Agent 的使用入口见 [README](README.md)，模块入口见[代码导航](docs/architecture/source-layout.md)。

## 实现职责

遵循[语言边界](docs/architecture/language-boundaries.md)：PLC 领域语义由 Python Core 持有，TypeScript 展示和传送数据，C# 适配原生接口。Web 和 MCP 共用应用服务与工具注册表。Qt 已退役，不恢复 GUI 兼容壳。

新模块放入已有职责包；旧路径的迁移映射见 [tools/source_layout.json](tools/source_layout.json)。产品代码不导入研究基线，不新增平行的模型、检索或 PLC 规则实现。应用检索从 [knowledge.retriever](src/knowledge/retriever.py)进入；模型参数按[运行时所有权](docs/architecture/runtime-ownership.md)处理。

## 修改与验证

修改前检查工作树，保留无关改动。按[测试归属](tests/README.md)向已有测试文件添加用例；数据变体优先参数化。根据影响运行相应测试和现有构建检查，记录失败及跳过原因。Windows 原生操作使用隔离工程和已确认目标，实测记录按[报告约定](docs/reports/README.md)保存。

外部操作遵循[审批策略](docs/architecture/approval-modes.md)。协议层调用共享工具并使用公开结果投影；stdio 的 stdout 只承载 MCP 数据，诊断写入 stderr。不得通过文案或适配器放宽地址策略、版本绑定、资源锁或原生操作许可。

## 逆向工作

GX Works2 逆向常态化使用 `ghidra-cli` 和 `property-based-testing`，按各自适用范围加载技能及必要参考，无需用户逐次提醒。

- `ghidra-cli`：用于原生二进制的反编译、调用与交叉引用、接口布局、类型和数据流分析。将静态推断与隔离工程的原生实验交叉验证，分别记录推断和实测结果。
- `property-based-testing`：用于生成有效的源码、声明、图形连接及编辑序列，验证序列化回读、编辑不变量、端口方向和未知字节保留，并缩减失败为最小反例。优先采用独立预期、冻结原生样本或原生接口作为对照，避免仅验证“不崩溃”或由被测实现生成预期。原生接受与 PLC 运行语义分别取证。

一个大模块完成后，再对该模块扩大测试，覆盖所有受支持的具体型号和 CPU 菜单选项；尚未实测的型号明确标为未验证。证据绑定具体 CPU、软件版本和工程来源，不把 Q03UDV 的结果推广到整个 Q 系列，也不把 FX3U 的结果推广到整个 FX 系列。型号菜单中的合并选项保留其实际范围。

过程保留可重现的最小源码和原始实验记录；形成大模块成果后再集中固定证据、补必要回归并接入 Web/MCP，不因每个小发现新增报告或扩展应用能力，不新增哈希验证。

## 文档

使用项目作者口吻，描述有效规则与已记录结果。README 保留介绍和快速上手；操作进入指南，字段、默认值和限制链接具体代码符号。正式文档、[版本报告](docs/reports/README.md)和[过程记录](docs/process/README.md)分开维护。

修改文案时检查模板、生成器和分发清单。许可证、第三方归属、原始证据与冻结快照保持其许可及内容；报告中的失败、环境、来源和版本不可省略。提交、推送和发布分别按明确指示执行。
