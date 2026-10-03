# Accessibility / 无障碍说明

## 范围与目标

本说明覆盖 GXWorks Agent 的 Web 工作台和项目文档。我们希望需求输入、设置、规格审查、错误提示及版本操作能够逐步改善键盘操作、可读性和辅助技术体验。三菱 GX Works2、外部 MCP 客户端和浏览器自身的无障碍行为不由本项目控制。

当前安装和使用环境以 [安装指南](docs/guides/getting-started.md)为准。Windows 原生集成的可运行性不等同于浏览器、读屏软件或其他辅助技术的无障碍兼容性。

## 限制与待验证范围

本项目不声明已达到 WCAG 的某一符合性等级，也不保证所有工作流均可完全通过键盘或读屏软件完成。梯形图及其他图形化工程视图需要特别验证；不能因为图形能够显示，就认为其连接关系、顺序及语义可以被辅助技术完整读取。

键盘焦点与对话框、缩放后布局、颜色对比、错误及任务状态播报、图形信息的文字替代，是贡献和验证应重点关注的范围。这些是待验证事项，而不是已经完成的兼容性承诺。MCP 或文本导出可以提供不同交互形式，但不自动构成完整的无障碍替代方案。

## 报告障碍

可以使用 [缺陷表单](https://github.com/Arienax/gxworks-agent/issues/new?template=01_bug_report.yml)，选择“无障碍 / Accessibility”，也可以创建空白 Issue。请描述想完成的操作、遇到的障碍，以及愿意提供的浏览器、操作系统、缩放比例、键盘或辅助技术及其版本。

不需要披露诊断、残障情况或其他医疗信息。截图和录屏均为可选，请同时提供文字说明，并移除登录链接、客户工程和个人信息。需要私下沟通时，可先创建不含敏感信息的联系请求。报告不承诺固定处理时限。

## 贡献与维护

修改相关界面时，检查受影响路径的键盘进入/退出、焦点恢复、控件名称和非颜色提示；记录实际检查环境及尚未验证的范围。没有使用过某种辅助技术时，不写“已兼容”。遵循 [贡献指南](CONTRIBUTING.md)，按修改范围验证，不要求每个 PR 执行全产品无障碍审计。

项目维护者随相关功能和验证结果更新本说明。

## English summary

This statement covers the project's Web workbench and documentation, not GX Works2 or external MCP clients. Keyboard access, readable layouts, meaningful control names, focus handling, status announcements and text alternatives for engineering diagrams are improvement and verification priorities, not claims of completed support.

No WCAG conformance level or universal assistive-technology compatibility is claimed. Report a barrier through the bug form or a blank issue, describing the task and relevant environment. Screenshots are optional; no disability diagnosis or medical information is required. Contributors should record what they actually tested and what remains unverified.
