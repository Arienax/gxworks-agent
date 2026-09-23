# GLM 需求分析 JSON 格式失败与纠正

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

状态：**confirmed**（该实验实测的失败机制和纠正路径）；日期：2026-09-10。

同日后续将工作台语言检查改为偏好，并加入实时进度与可选预览，见 [规格交互与实时进度](../../process/research/2026-09-10-spec-interaction-and-live-progress.md)。本文记录的是首次格式纠正验证时的策略。

## 复现证据

- 用户在本地 Web 工作台使用 `glm-5.3-flash` 分析“起保停”，历史任务两次以 `content:invalid_json_object` 失败。历史任务没有保存原始回复，不能断言它们的具体语法错误与该实验完全相同（**unknown**）。
- 在用户 Edge 页面直接重试成功，说明不是每次失败。随后用失败任务保存的输入、工程上下文和当前打包程序的 Profile 重放：第一遍成功，第二遍失败（**confirmed**）。
- 请求已包含 `response_format={"type":"json_object"}`，实际工作流使用 `reasoning_effort=low`，并保留该 Profile 的 thinking 配置（**confirmed**）。
- 失败响应的最终内容长 2139 字符，思考内容长 52 字符，服务端 `finish_reason=stop`。错误位于 `flowchart_steps`，出现 `{"type":"transition":"X0启动按下"}` 和同形的 step 节点，缺少独立的 `label` 键（**confirmed**）。不能把 JSON 模式或正常结束标志当成语法验收通过。
- 原提示中的完整输出示例夹有 JSON 之外的“中选1-3个”，并行流程示例也使用伪 JSON（**confirmed**）。这些示例可能增加输出格式错误的概率，但没有做受控频率实验，因果贡献仍为 **unknown**。

原始流和请求仅保存在本地被忽略的 `research/experiments/analysis-repro/`，不提交完整模型思考、用户配置或认证信息。回归测试使用最小化的错误片段。

## 修复边界

1. 输出对象和并行流程示例改为有效 JSON，把枚举说明移到对象外。
2. 只对尚未确认的需求分析草稿允许一次格式纠正。必须先收到 `content:invalid_json_object`，且正文形似 JSON 对象并确实存在 JSON 语法错误。
3. 纠正请求复用该实验 Provider、输入上下文、语言及传输模式，给出解析错误位置，要求保持需求、设备、方案和问题，并返回完整分析对象。
4. 首次失败的正文和思考不向界面发布。纠正结果继续通过原有结构及语言检查；第二次仍失败则终止，不再循环请求。
5. 语言错误、字段类型错误、合法 JSON 的根类型不符、空回复和传输错误不触发此格式纠正。PLC 程序生成、审批与 GX 写入流程没有增加自动重试或自动确认。

纠正内容仍是待用户确认的规格草稿。模型纠正不构成已证明的语义等价转换。

## 验证

- **confirmed**：将捕获的真实失败作为第一轮，第二轮调用真实 `glm-5.3-flash`；返回合法 JSON，原五个流程节点的状态、转移、X0/X1/Y0 和文字标签保持，恢复缺失的 `label` 键；仅发布通过检查的结果。这是“真实失败重放 + 在线纠正”，不是声称连续两轮都为新请求。
- **confirmed**：新增 14 项回归，覆盖同步/流式入口、一次纠正上限、失败内容隔离、语言/字段拒绝、传输失败、上下文保持及提示示例语法。全量测试 `1382 passed, 3 skipped`，另有现存 Starlette/AnyIO 弃用警告。
- **confirmed**：前端生产构建与隔离安装包烟测通过，包含静态资源、登录、只读工作区保持及不包含 Qt 模块的检查。
- **confirmed**：已更新原 `dist/GXWorks-Agent-Web` 安装目录并在原端口 8765 重启。旧安装包和任务状态在本地 `dist/backups/` 备份，更新前后的用户 `config.json` 哈希一致。运行文件 SHA-256 为 `d1eb4ff01facd1735c92c2975b1bab8e32ec2196a29bc82cca0a65a065ce1e88`，与烟测构建一致。
- **confirmed**：在用户原 Edge 标签页、原工程内重新提交“起保停”，在线任务 `job_6744dff7765f4999a7738c125b930d90` 完成；打开规格草稿，看到两种实现方案、启动/停止/输出地址及停止按钮极性问题。保留待确认状态，没有生成程序、确认规格或操作 PLC。

## 复查入口

- `src/api.py`：`_request_analysis_response`、两种分析入口、`ANALYSIS_SYSTEM_PROMPT`。
- `src/application/workbench.py`：格式纠正期间的任务进度。
- `tests/test_analysis_format_repair.py`：最小复现和边界回归。
