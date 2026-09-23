# GXWorks Agent：贡献约定

GXWorks Agent 的使用入口见 [README](README.md)，模块入口见[代码导航](docs/architecture/source-layout.md)。

## 实现职责

遵循[语言边界](docs/architecture/language-boundaries.md)：PLC 领域语义由 Python Core 持有，TypeScript 展示和传送数据，C# 适配原生接口。Web 和 MCP 共用应用服务与工具注册表。Qt 已退役，不恢复 GUI 兼容壳。

新模块放入已有职责包；旧路径的迁移映射见 [tools/source_layout.json](tools/source_layout.json)。产品代码不导入研究基线，不新增平行的模型、检索或 PLC 规则实现。应用检索从 [knowledge.retriever](src/knowledge/retriever.py)进入；模型参数按[运行时所有权](docs/architecture/runtime-ownership.md)处理。

## 修改与验证

修改前检查工作树，保留无关改动。按[测试归属](tests/README.md)向已有测试文件添加用例；数据变体优先参数化。根据影响运行相应测试和现有构建检查，记录失败及跳过原因。Windows 原生操作使用隔离工程和已确认目标，实测记录按[报告约定](docs/reports/README.md)保存。

外部操作遵循[审批策略](docs/architecture/approval-modes.md)。协议层调用共享工具并使用公开结果投影；stdio 的 stdout 只承载 MCP 数据，诊断写入 stderr。不得通过文案或适配器放宽地址策略、版本绑定、资源锁或原生操作许可。

## 文档

使用项目作者口吻，描述有效规则与已记录结果。README 保留介绍和快速上手；操作进入指南，字段、默认值和限制链接具体代码符号。正式文档、[版本报告](docs/reports/README.md)和[过程记录](docs/process/README.md)分开维护。

修改文案时检查模板、生成器和分发清单。许可证、第三方归属、原始证据与冻结快照保持其许可及内容；报告中的失败、环境、来源和版本不可省略。提交、推送和发布分别按明确指示执行。
