# Qt desktop — legacy/frozen

此目录是冻结的兼容界面，不再新增功能或追求 Web 新功能 parity。只修安全风险、崩溃、数据损坏及关键流程不可用等严重 bug；共享 Core 变更只做必要适配。

PLC 语义留在无 GUI 的 Python Core。Qt 只接收 Core 结果或序列化用户编辑意图。Web 是继续开发的主界面。

在 Web 覆盖冻结时的必要工作流并完成 Windows/GX 验收前，保留 Qt/Win7 入口、依赖和打包配置。完成后另行删除 Qt。

完整约定：[`docs/architecture/language-boundaries.md`](../../../docs/architecture/language-boundaries.md)。
