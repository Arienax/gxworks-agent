# Contributing / 贡献指南

欢迎提交可复现的问题、文档修正、测试、指令事实和代码改进。Issue 与 PR 可以使用中文或英文。参与协作请遵守 [行为准则](CODE_OF_CONDUCT.md)。

## 先了解项目与许可

GXWorks Agent 的当前使用范围见 [README](README.zh-CN.md)，开发约定以 [AGENTS.md](AGENTS.md) 为入口。本指南说明参与流程，不另建一套架构或测试规则。

本仓库采用**源码可见的专有许可，不是开源许可证**。提交贡献前，请阅读 [LICENSE](LICENSE)，尤其是第 3 节贡献授权条款。公共 fork 仅能用于该许可证允许的上游贡献准备，不应作为独立分发或发布渠道。本指南不扩大或变更许可证中的权利。

不要提交 API key、登录链接、个人数据、客户工程，或没有再分发许可的三菱软件、DLL、安装包、完整手册和第三方材料。引用资料时提供文档名称、版本、页码及允许公开的链接；保留第三方归属。

## 提交问题

从 [Issue 入口](https://github.com/Arienax/gxworks-agent/issues/new/choose)选择最接近的表单。先搜索是否已有相同问题；已有问题可补充新证据，不必重复创建。表单不适用时仍可创建空白 Issue。

普通缺陷请说明期望与实际行为、最小复现步骤、项目版本及相关环境。PLC/原生工程问题应尽量给出具体 Series/Type、GX Works2 版本和程序语言；不知道的字段明确写“未知”，不要猜测。型号选项出现在表单中不代表项目已支持或验证该型号。

生成、RAG 或耗时问题请保留相同的需求、确认规格、模型 ID 与参数条件，并区分候选结果和已接受版本。提供最小、可公开的证据即可，不要求上传完整工程或完整诊断包。共享记录前按 [诊断指南](docs/guides/diagnostics.md)检查并脱敏。

漏洞、凭据泄露、审批绕过或未授权工程写入等问题走 [安全政策](SECURITY.md)的私下报告流程，不要发布公开利用步骤。无障碍问题见 [ACCESSIBILITY.md](ACCESSIBILITY.md)。

## 开发与验证

环境准备及源码启动见 [安装指南](docs/guides/getting-started.md)，模块入口见 [代码导航](docs/architecture/source-layout.md)。从最新 `main` 创建聚焦的工作分支，通过 PR 提交，不重写已经发布的历史。

PLC 语义由 Python Core 持有；Web 与 MCP 复用应用服务。实现职责、原生操作审批和模型参数所有权直接遵循 [AGENTS.md](AGENTS.md)及其链接，不在前端、适配器或提示词中另建平行规则。

按 [测试归属](tests/README.md)选择已有测试文件，为修改的行为补充必要用例；同一不变量的变体优先参数化。只运行与影响范围相符的测试、构建和检查，在 PR 中列出实际命令、结果、失败及跳过原因。文档或模板修改检查格式、链接和内容一致性，不要求为此运行模型评测、全量回归或生成独立报告。

不要为了满足贡献流程操作真实生产设备。原生实验使用隔离工程和已授权的环境，不绕过确认、资源锁或目标版本约束。软件测试、GX Works2 编译和设备运行是不同层次的证据，不能互相替代。

## 指令事实与逆向贡献

先查阅 [知识资源](resources/knowledge/README.md)、[研究索引](research/README.md)及 [AGENTS.md](AGENTS.md)中的逆向约定。区分资料中的规则、静态推断、原生打开/编译结果和运行观察；证据绑定具体 CPU、软件版本和工程来源，不把一个型号的结果推广到整个系列。

保留最小复现与原始观察。形成较大模块成果后，再集中固定证据、补必要回归并接入产品；不为每个小发现新增报告，也不新增哈希验证流程。

大型原始输出和归档遵循 [研究证据存储](docs/guides/evidence-storage.md)，使用已有 LFS 规则，不提交厂商二进制或未授权工程。暂存后运行现有检查：

```powershell
python scripts/check_repository_storage.py --staged
```

涉及研究、Core 语义和产品集成时，按现有约定组织可独立审查的提交；普通文档维护不需要人为拆成这些层次。

## Pull Request

一个 PR 聚焦一个问题或明确的模块边界。说明改了什么、为何修改、如何验证及尚未验证的范围。截图应配文字说明；不适用的模板项可以删除或写明原因。依赖、配置、支持范围或行为变化应同步更新对应指南，不把路线图写成已实现能力。

允许 AI 辅助。请审查实际 diff、验证引用与接口、移除虚构结论，并确保你有权提交所有内容。没有运行的测试应直接写“未运行”，不要把模型的判断当作测试结果。

## English summary

Chinese and English contributions are welcome. Read [LICENSE](LICENSE), especially section 3, before contributing: this is source-available proprietary software, not an open-source license. Use a focused branch and pull request; follow [AGENTS.md](AGENTS.md), the existing [setup guide](docs/guides/getting-started.md) and [test ownership](tests/README.md).

Report minimal reproducible cases with exact versions and CPU details where relevant. Sanitize diagnostics, share only material you are allowed to publish, and report vulnerabilities privately via [SECURITY.md](SECURITY.md). Run checks proportionate to the change and state what was not tested. Small research findings do not require separate reports or full regression runs. AI-assisted contributions remain the submitter's responsibility.
