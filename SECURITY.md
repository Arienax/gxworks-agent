# Security Policy / 安全政策

## 报告漏洞

请勿在公开 Issue、PR、截图或日志中披露未修复漏洞的利用细节、有效凭据或客户数据。

优先查看仓库的 [Security 页面](https://github.com/Arienax/gxworks-agent/security)。**只有页面提供 `Report a vulnerability` 入口时，才能通过 GitHub 私密漏洞报告提交详情。** 本文件的存在不代表该功能已经开启。

该入口不可用时，请创建标题为 `Security contact request` 的空白 Issue，正文仅写“希望取得私下报告安全问题的联系方式”，不要附带组件名称、复现步骤、日志或其他漏洞细节。维护者提供私下渠道后再发送报告。不要把公开 Issue、公开 gist 或普通 PR 当成私密渠道。

私下报告请尽可能包含受影响版本或 commit、操作系统、使用入口（Web/MCP/原生适配器等）、攻击前提、潜在影响及使用合成数据的最小复现。CPU、GX Works2、模型配置仅在相关时提供；不要发送有效 API key、登录链接、完整客户工程或无关个人信息。

## 范围与版本

凭据泄露、未授权文件或工程访问、审批/目标绑定绕过、不可信输入导致代码执行，以及跨项目数据泄露等均属于应私下报告的安全问题。不能确定风险性质时，先走私下联系流程。

报告时标明实际使用的版本，不必为了报告问题升级生产环境。`main` 是当前开发线；旧包、旧分支和历史版本不承诺获得回补，也不承诺固定修复时限或漏洞奖励。修复适用范围以具体公告或修复说明为准。

一般界面缺陷、无安全影响的生成错误或文档问题可以使用普通 Issue。如果错误可能导致越权操作、数据泄露或危险动作，不要在生产设备上复现；先按安全问题私下沟通。

## 使用边界

本项目面向本地工程工作流，不应在没有独立安全评估和访问控制的情况下将工作台暴露到公网或不可信网络。模型服务、MCP 客户端、项目文件和本地原生组件均涉及各自的信任边界。

导出的交互记录可能含需求、模型回复、端点和附件；公开前按 [诊断指南](docs/guides/diagnostics.md)逐项审查。不要假定导出包已经完全脱敏。发现真实凭据泄露时，应通过对应服务撤销或轮换凭据，不能只删除公开文本。

原生测试使用隔离工程、备份和明确授权，不绕过审批或直接在生产设备上测试攻击。生成的 PLC 逻辑需要独立审查和目标环境验证；急停、防护及其他安全功能必须由设备的独立安全系统承担，不能依赖本工作台或 AI 生成逻辑代替。

安全政策不提供额外的软件保证，也不改变 [LICENSE](LICENSE)及第三方权利。

## English reporting instructions

Do not disclose unpatched exploit details, credentials or customer data in public issues or pull requests. Use **Report a vulnerability** on the repository's [Security page](https://github.com/Arienax/gxworks-agent/security) only when that option is available. Adding this file does not enable private reporting.

Otherwise, open a blank issue titled `Security contact request` with only a request for a private reporting channel. Include no vulnerability details. Send affected versions, prerequisites, impact and a minimal synthetic reproduction only after a private channel has been established.

Reports are welcome for the version actually in use; do not upgrade production systems merely to file a report. No fixed response deadline, bounty or backport coverage is promised. Never test an exploit on production machinery. Review and sanitize diagnostics, revoke exposed credentials, and independently validate generated PLC logic before deployment.
