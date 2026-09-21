# Web 工作台迁移：行为基线

> 2026-09-21 更新：Qt 已按[退役审查](qt-retirement-audit.md)主动退役。下文保留迁移时期的行为基线、限制和验收记录；其中“保留 Qt”“全部验收前不删除”不再是当前发布要求。未完成的 Windows/GX/Simulator2 现场验收仍然未完成，不能由本次删除或离线回归推定为通过。

基线提交：`dff02be`（main）。迁移分支：`codex/web-workbench-migration`。
2026-09-09 使用仓库 `.venv`、Python 3.13 执行；未调用真实模型、写入 GX 或操作 PLC。

## 迁移前验证

- 全量：997 passed，2 skipped，20.51 秒。跳过的是依赖运行中 GX Works2 的只读集成检查。
- 生成/语言/工具/MCP/架构关键子集：205 passed，9.33 秒。
- `scripts/mcp_smoke.py`：12 个注册工具；读取 N0001 成功；首次生成返回 `confirmation_required`；版本数量仍为 0；`workspace_unchanged=true`。

## 固定输入与行为契约

现有测试中的假 Provider、临时 SessionStore 与确定性 IR 是迁移的输入输出基准，不复制或打开用户真实工程用于写入测试。

| 契约 | 基线证据 |
| --- | --- |
| ladder/ST 参数、确认规格、局部修复和失败行为 | `tests/test_contract_repair_policy.py`、`tests/test_contract_repair_planner.py` |
| stream/fallback 完整响应验收，语言冻结，拒绝不发布、不写产物 | `tests/test_language_workflows.py`、`tests/test_streaming_workflows.py`、`tests/test_model_provider.py` |
| IR 派生 SVG/CSV/ST、哈希与校验 | `tests/test_plc_core_boundary.py`、`tests/test_plc_ir.py` |
| 旧工作区只读、路径约束、候选私有字段不外泄 | `tests/test_mcp.py`、`scripts/mcp_smoke.py` |
| 版本绑定仿真、不可用状态与证据 | `tests/test_simulator_workflow.py`、`tests/test_simulator_verification.py` |
| 模型/工程/协议边界 | `tests/test_architecture_boundaries.py` |

## 分阶段验收门槛

0. 保留上述行为基线；已有缺陷与迁移新增失败分开记录。
1. 生成服务可在没有 Qt 的进程运行；Qt 保留薄适配层；原生成回归继续通过。
2. Web 读取真实格式的项目、版本与产物；只读启动及查询不改旧工作区。
3. 后端任务、事件补齐、提案审批、版本冲突、重复提交、重启恢复有离线回归。
4. 所有 GX 操作经串行协调器；环境不可用如实失败；真实导入/仿真单列现场验收。
5. 默认 MCP 不写入不变；显式连接模式只提交提案，Agent 不能批准。
6. 附件、设置、语言、SFC 需求输入、报告与打包有功能对照。只有现场验收完成后才考虑移除 Qt。

独立提交代表可检查的实现阶段，不代表已经发布远端 PR 或完成 Windows 实机验收。
