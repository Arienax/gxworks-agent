# 文档索引

## 使用工作台

从[安装与第一个工程](guides/getting-started.md)开始，再按任务查阅：

| 任务 | 指南 |
| --- | --- |
| 工作区、版本与日常操作 | [Web 工作台](integrations/web.md) |
| 连接模型、调整参数、备份配置 | [模型设置](guides/model-settings.md) |
| 向 GX Works2 传送或回读程序 | [GX Works2](guides/gxworks2.md) |
| 编辑、导入或检查结构化工程 | [Structured Ladder / FBD](guides/fbd.md) |
| 排查生成问题、导出交互记录 | [诊断与记录](guides/diagnostics.md) |
| 连接外部 Agent | [MCP 接入](integrations/onboarding.md)、[Codex](integrations/codex.md) |
| 安装、构建或更新发布包 | [打包与更新](guides/packaging.md) |

## 实现与接口参考

[代码导航](architecture/source-layout.md)连接各模块的职责和实现入口。[语言边界](architecture/language-boundaries.md)定义 Python、TypeScript 和 C# 的分工；测试归属由 [tests/README.md](../tests/README.md)维护。

| 主题 | 详细来源 |
| --- | --- |
| Direct / Design 分析 | [分析模式](architecture/analysis-modes.md) |
| 当前规格、原始需求与审计回执 | [意图和证据交接](architecture/intent-evidence-handoff.md) |
| I/O 身份与用途注释 | [I/O 标签](architecture/io-binding-labels.md) |
| 生成调用、传输与候选处理 | [调用契约](architecture/generation-call-contracts.md) |
| 已确认决策的生成策略 | [生成执行策略](architecture/generation-execution-policy.md) |
| 结果展示与交付 | [生成交付](architecture/generation-delivery.md) |
| 修复范围 | [修复边界](architecture/generation-repair.md) |
| 操作审批 | [审批策略](architecture/approval-modes.md) |
| 模型参数与能力描述 | [能力合同](integrations/capability-contract-v3.md)、[兼容协议](integrations/openai-compatible.md) |
| 请求语言与界面翻译 | [响应语言](architecture/response-language.md)、[本地化](localization.md) |
| 事实检索、指令合同与步宽 | [知识库](../resources/knowledge/README.md)、[事实交付](architecture/instruction-fact-delivery.md)、[合同覆盖](architecture/instruction-contract-coverage.md)、[步宽](architecture/instruction-step-widths.md) |
| HTTP 与 MCP | [HTTP 接口](integrations/http-api.md)、[MCP 接口](integrations/mcp.md) |

## 结果与历史

[验证报告](reports/README.md)按被测版本、环境和证据记录结果。[过程目录](process/README.md)保存设计讨论、迁移经过、未完成方案和历史进度。GXW 原始样本、失败记录及冻结结果从[研究证据索引](../research/README.md)进入。

功能入口见相应指南，验证状态见版本报告，已发布的版本及附件在[仓库 Releases](https://github.com/Arienax/gxworks-agent/releases)查询。

许可证原文在 [LICENSE](../LICENSE)，第三方知识资料的许可与归属在 [THIRD_PARTY_NOTICES.md](../resources/knowledge/THIRD_PARTY_NOTICES.md)。
