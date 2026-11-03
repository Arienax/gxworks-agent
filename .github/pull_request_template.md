<!-- 中文或英文均可。不适用的项可以删除；无需为普通修改另建报告。 -->
<!-- 漏洞请通过私下渠道联系维护者；切勿公开凭据、客户工程或未修复利用细节。 -->

## 目的与修改 / Summary

<!-- 解决什么问题、为何这样修改；相关 Issue（如有）。 -->
<!-- Describe the problem solved, the architectural rationale, and link related issues (if any). -->

## AI 辅助 / AI assistance

<!-- 在初始 PR 描述中明确列出提供实质性帮助的 AI 工具、精确模型 ID（不能仅写模型系列名）及协助范围；未使用则写“未使用 / None”。 -->
<!-- In the initial PR description, name the AI tools and exact model IDs (not just model families) that provided substantive assistance and specify what they helped produce, or write "None". -->
<!-- 欢迎 AI 辅助，但绝不能替代独立判断、人工审查或实际测试；切勿粘贴私人提示词、对话日志、API 凭据或推理过程。 -->
<!-- AI assistance is welcome but never replaces independent judgment, personal review, or empirical verification; do not attach private prompts, chat logs, credentials, or raw reasoning traces. -->

## 验证 / Validation

<!-- 列出实际执行的命令、测试环境与观察结果。未运行的检查请明确标注“未测试 / Not tested”并说明限制。 -->
<!-- List the exact commands executed, test environments, and observed results. If a check was not run, explicitly write "Not tested" and explain the constraint. -->
<!-- 文档/模板修改仅需核对格式、相对链接与内容一致性即可；不要求运行无关的代码回归或模型调用。 -->
<!-- Documentation or template changes only require verifying formatting, relative links, and consistency; unrelated test suites are not required. -->

## 影响与限制 / Scope and limitations

<!-- 记录行为、接口、依赖或配置变化，以及尚未验证的部分；不适用可删除。 -->
<!-- Detail changes to behavior, schemas, dependencies, or configuration, along with any unverified areas. -->
<!-- 原生/PLC 修改：具体 CPU 型号、软件产品及版本、工程来源，严密区分：离线解析 / 原生打开 / 原生编译 / 仿真 / 真实设备观察。 -->
<!-- For native/PLC changes: state exact CPU model, engineering suite version, and project origin; strictly distinguish: Offline parse / Native open / Native compile / Simulation / Physical hardware observation. -->
<!-- Web 修改：附相关截图并配文字说明，记录受影响的用户交互路径。 -->
<!-- For Web UI changes: provide screenshots with text descriptions and note affected interaction flows. -->

## 提交检查 / Checklist

- [ ] 已阅读 LICENSE 第 3 节；确认有权提交这些内容。 / I have reviewed Section 3 of the LICENSE and confirm the right to submit these contributions.
- [ ] 未包含有效凭据、登录链接、客户私有数据或未授权第三方材料。 / Contains no credentials, session tokens, confidential customer data, or unauthorized third-party materials.
- [ ] 已人工阅读并理解完整实际差异，能独立解释修改，并对提交内容与验证声明负责。 / I have personally read and understood the entire diff, can independently explain the changes, and take full responsibility for the content and validation claims.
- [ ] 已按影响范围验证，并如实说明失败、跳过和未验证内容。 / Verified according to the scope of changes, with all failures, skipped tests, and unverified areas truthfully disclosed.
