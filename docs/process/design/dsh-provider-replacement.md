# 用 DSH provider 模块替换本项目的 provider 逻辑：可行性评估

评估日期：2026-09-24。目标仓库 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)（本地检出 `D:\download\dsh\deepseek-harness`，包版本 `0.1.7-rc.1`）。

本文是**未执行方案**的评估记录：没有改动任何源码，没有运行构建、测试或真实模型调用。结论用于决定是否进入实施，不代表已交付行为。

> 2026-09-24 更新：本文回答的是"能否把代码迁移过来"。实际目标是吸收 DSH 的设计而非迁移代码，见[吸收 DSH 的模型配置逻辑与上下文策略](dsh-config-and-context-absorption.md)。本文作为"为什么不迁移"的证据保留。

## 结论摘要

按字面含义"把 provider 逻辑全部换成 DSH 的 provider 模块"不可行，有三个独立阻断点，任一都足以否决：

1. **语言与运行时边界**。DSH 的 provider 模块是 TypeScript ESM + Cordis 插件；本项目的 provider 逻辑是 Python。Python 无法导入它，DSH 也没有 Python 构建。
2. **DSH 的 provider 是 seam，不是 provider**。`@deepseek-ai/dsh-llm` 只有注册表、请求装配和 `StreamChunk` 词汇表，没有任何 wire 代码、没有结构化输出字段、没有重试执行、没有语言验收。
3. **契约面覆盖不足**。`model_runtime` 的 13 个模块里只有 3 个在 DSH 侧存在对应物，且都是部分对应。能力合同 v3、参数解析、验收、诊断、观测在 DSH seam 中没有位置。

因此只有两条真正可执行的路线：跨进程复用 DSH 代码（代价是产品新增 Node 运行时，且仍丢失结构化输出），或在 Python 内按 DSH 的 seam 形状重写（复用设计而非代码）。详见下文「可选路径」。

## DSH provider 模块的实际契约

### 包构成

| 包 | 角色 |
|---|---|
| `packages/llm/llm` | `LlmRuntime` 服务：适配器注册表、可配置 provider 目录、模型发现、调用准备、流式边界 |
| `packages/llm/llm-deepseek` | `deepseek-official` 路由，走 DeepSeek Messages 协议 |
| `packages/llm/llm-pi-ai` | 配置化 provider 路由，走 pi-ai catalog；支持 `openai-completions`、`openai-responses`、`anthropic-messages` 与手工声明的网关 |
| `packages/llm/llm-retry` | 在持久化 agent-step 边界重跑失败请求 |
| `packages/llm/token-meter` | 从持久化会话日志测量请求与上下文压力 |

`@deepseek-ai/dsh-llm` 在 npm 上公开（`publishConfig.access: public`，0.1.0-rc.2 起为 MIT；0.0.1 系列为 BSD-3-Clause），`next` 标签为 `0.1.7-rc.1`。它自身 peer 依赖 `@deepseek-ai/cordis`（`~4.0.4`），依赖 `dsh-brand`、`dsh-timeout`、`dsh-util-crypto`、`dsh-util-values`、`dsh-typert-protocol`、`schemastery`、`zod`。`llm-pi-ai` 额外 peer 依赖 `dsh-attachment`、`dsh-authorization`、`dsh-credentials`、`dsh-fs`、`dsh-launch-environment`、`cordis-plugin-loader` 和 `@earendil-works/pi-ai`。

即：这不是一个可独立引入的库，而是一组必须挂在 Cordis host 上的插件。

### 它明确不提供的东西

以下都取自 DSH 自己的文档，不是本项目的推断：

- **没有 wire 代码**。`llm/README.md` 写明 "the service itself has no configuration and no provider wire code"。
- **不执行重试**。"This service never re-runs a request: retrying is the job of `dsh-llm-retry`"。
- **采样参数只有 `temperature` / `maxTokens` / `stop`**。`llm/README.md` 的已知限制原文："no `tool_choice`, `top_p`, or penalty fields"。
- **`GenerateOptions` 没有结构化输出字段**。`docs/subsystems/llm-streaming.md` 列出的 `GenerateOptions` 字段为 `provider`、`model`、`reasoningEffort`、`messages`、`system`、`tools`、`temperature`、`maxTokens`、`stop`、`signal`、`sessionId`、`purpose` —— 没有 `response_format`。
- **远程面不能流式调用**。`packages/llm/llm/lib/typert.remote-client.d.ts` 只暴露 `discoverModels`、`listConfigurableProviders`、`listProviders` 三个一元调用；`ctx.llm.stream()` 不跨 wire。

### 它要求调用方承担的义务

DSH 的不变式把模型可见输入绑定到它自己的会话日志：`Model-visible ⟺ logged`、loop 构造的请求进入时深度冻结、replay 状态只在同一个适配器实例内传递、一次准备调用只能派发一次。这些义务由 DSH 的 Session / agent-loop 承担。只取 provider seam 而不引入整套 harness，就得自己实现这些。

## 本项目调用链的实际影响面

### 分层与模块

| 模块 | 角色 | DSH 侧对应物 |
|---|---|---|
| [provider.py](../../../src/model_runtime/provider.py) | 厂商中立消息类型 + OpenAI-compatible 传输 + `collect_response` 验收 | 部分：seam 词汇表 + 适配器 |
| [responses.py](../../../src/model_runtime/responses.py) | 模型自述文本的验收规则 | 无 |
| [contract.py](../../../src/model_runtime/contract.py) | 版本化、模型中立的能力数据 | 无 |
| [domain.py](../../../src/model_runtime/domain.py) | v3 参数域、展示提示、有界证据 | 无 |
| [catalog.py](../../../src/model_runtime/catalog.py) | 本地能力目录，端点精确匹配 | 部分：`resolveModel` |
| [request_policy.py](../../../src/model_runtime/request_policy.py) | 纯请求解析：protocol > 用户选择 > hint > default > 省略 | 无 |
| [runtime_profile.py](../../../src/model_runtime/runtime_profile.py) | 持久化 profile → 运行时能力快照 | 无 |
| [transport_policy.py](../../../src/model_runtime/transport_policy.py) | 保守传输选择、流式回退许可 | 部分：`retry-policy.ts`、`adapter-failure.ts` |
| [response_format.py](../../../src/model_runtime/response_format.py) | 生成前选择可用 wire 模式 | 无 |
| [verification.py](../../../src/model_runtime/verification.py) | 单次显式授权的合成请求 | 无 |
| [probes.py](../../../src/model_runtime/probes.py) | 显式单请求验证的安全合成样本 | 无 |
| [legacy_migration.py](../../../src/model_runtime/legacy_migration.py) | 退役 profile 字段的唯一解释器 | 无 |
| [observations.py](../../../src/model_runtime/observations.py) | 有界、本地、尽力而为的传输观测 | 无（harness 层另有 telemetry/token-meter） |

13 个模块中只有 3 个有 DSH 对应物，且均为部分对应。也就是说"替换 provider 逻辑"实际会被拆成两件事：**替换传输 seam**，以及**其余 10 个模块无处可去、必须留下**。

### 调用链各层

| 层 | 位置 | 用法 |
|---|---|---|
| 传输与验收 | `model_runtime/provider.py` | `OpenAICompatibleProvider.stream` L741、`collect_response` L928、`ModelProvider` Protocol L271、`create_provider` L1113、`get_active_provider` L1122 |
| 应用门面 | [application/model_api.py](../../../src/application/model_api.py) | `provider_scope` L71、`_request_model` L127 调 `collect_response` L171、`current_provider` L183、`bound_provider_profile` L188、`_native_ladder_generation_options` L1343 读 profile 与 capability |
| Agent 循环 | [agent_runtime/agent.py](../../../src/agent_runtime/agent.py) | L13 导入 `ModelProvider`/`collect_response`/`get_active_provider`；L145 取 provider；L159 构造带 tools 的 `ModelRequest`；L177 调 `collect_response`；L196 回填缺失 tool call id |
| 上下文压缩 | [application/context_compactor.py](../../../src/application/context_compactor.py) | L14 导入 `ModelProviderError`；L200 经 `api.request_model` 发一次 checkpoint 调用；L210 用 `response_policy_scope(enforce_language=False)` |
| 模型检测 | [application/model_detection.py](../../../src/application/model_detection.py) | L14、L86 用 `ModelProviderError` |
| 设置与校验 | [application/settings.py](../../../src/application/settings.py) | L349/L363/L414 用 `create_provider` 做列表探测与合同验证 |
| 错误投影 | [application/base.py](../../../src/application/base.py) L71、[application/job_errors.py](../../../src/application/job_errors.py) L153 | `public_model_error`、`ResponseRejectedError` 分类 |
| 生成与修复 | [application/generation.py](../../../src/application/generation.py) L177/L415 | `ResponseRejectedError` 处理 |
| 配置与凭据 | [storage/config.py](../../../src/storage/config.py) | `get_model_profile`、`get_api_key`、`load_full_config` |

`integrations/mcp/context_provider.py` 是**工具上下文** provider，与模型 provider 无关，不在范围内。

### 规模

`grep` 统计 `model_runtime.provider` 的导入点：本仓库共 **109 处**，分布在 `src`、`tests`、`scripts`、`evals`。`src/model_runtime/provider.py` 单文件 **1222 行**。

### 测试归属

[测试归属](../../../tests/README.md)中直接拥有该契约的分组：

- **Model runtime and provider compatibility**（12 个文件）：`test_model_capabilities`、`test_model_catalog_v3`、`test_model_contract`、`test_model_detection`、`test_model_observations`、`test_model_presentation`、`test_model_profile_config`、`test_model_profile_deletion`、`test_model_provider`、`test_model_runtime_profile`、`test_model_stream_cancellation`、`test_model_verification`
- **Runtime diagnostics, privacy and streaming**（4 个直接相关）：`test_provider_error_privacy`、`test_response_language`、`test_runtime_diagnostics`、`test_streaming_workflows`
- 另有约 40 个文件通过 `TextDelta`/`ReasoningDelta`/`ModelProviderError` 等替身间接依赖该契约。

### 架构约束（任何方案都必须继续满足）

[test_architecture_boundaries.py](../../../tests/test_architecture_boundaries.py) 中与本主题直接冲突或互补的断言：

- L88 `test_only_model_provider_imports_openai_sdk`：除 `model_runtime/provider.py` 外任何源文件都不得导入 `openai`。
- L66 `test_provider_does_not_import_plc_gx_or_automation_implementation`。
- L146 `test_model_provider_reexports_the_same_neutral_tool_types`：`provider.ToolCall is agent_runtime.messages.ToolCall`。
- L98 `test_agent_and_api_do_not_parse_vendor_response_fields`。
- L35 起：PLC Core 文件不得导入 `model_runtime`；L103 起：MCP 适配器不得导入 `model_runtime` / `model_runtime.provider` / `storage.config`。

### 分发约束

[打包与更新](../../guides/packaging.md)：构建描述是 [web.spec](../../../packaging/pyinstaller/web.spec) 与 [mcp.spec](../../../packaging/pyinstaller/mcp.spec)，PyInstaller 打包 Python 产物；前端是构建后的静态 `web/dist`（[web/package.json](../../../web/package.json) 是 Vite/React，`private: true`，只做展示与 HTTP 传输）。**产品分发中不存在 Node 运行时。**

## 可选路径

| 维度 | A. Node sidecar | B. 在 Python 内重建 seam | C. 选择性吸收 |
|---|---|---|---|
| 是否复用 DSH 代码 | 是 | 否，复用设计 | 否 |
| 语言边界 | 跨进程 | 不跨 | 不跨 |
| 产品新增运行时依赖 | Node + DSH 包树（含 rc 预发布） | 无 | 无 |
| 结构化输出（`response_format` / 原生 JSON Schema） | **丢失**，seam 无此字段 | 保留 | 保留 |
| `tool_choice` | **丢失** | 保留 | 保留 |
| 语言验收 / ResponseContract | 需自行接回 | 保留 | 保留 |
| 能力合同 v3 与参数解析 | 仍留在 Python，split-brain | 保留在 Python | 保留 |
| `Usage` 语义 | 改为 DSH 的分离 cache 字段，token 记账语义变更 | 可对齐但不必改语义 | 不改 |
| 满足现有架构测试 | 需改写 `test_only_model_provider_imports_openai_sdk` 的语义 | 可保持 | 可保持 |
| 第三方归属 | 需新增 Node 依赖归属与离线打包 | 无新增 | 无新增 |
| 相对工作量 | 高 | 中高 | 低 |

### A. Node sidecar

写一个最小 Cordis 组合，只挂 `@deepseek-ai/dsh-llm` + `@deepseek-ai/dsh-llm-pi-ai`，暴露本地 stdio 或 127.0.0.1 JSON-RPC 端点；Python 侧把 `OpenAICompatibleProvider` 换成该端点的客户端，`ModelProvider` Protocol 以上不动。

阶段划分：

1. 组合与端点：Cordis 组合、协议定义、/health 与版本自检。
2. Python 客户端：实现 `ModelProvider` Protocol，事件映射到现有 `ModelEvent`。
3. 双跑对照：同一请求分别走旧 provider 与新端点，比对事件序列、usage、tool call 分片。
4. 打包：Node 运行时随包分发、离线安装、进程生命周期与崩溃恢复。
5. 切换与回退开关。

**否决理由**：结构化输出和 `tool_choice` 是当前生成链的硬依赖 —— [model_api.py](../../../src/application/model_api.py) L1343 `_native_ladder_generation_options` 依据 capability v3 决定是否下发 `response_format: {"type": "json_schema", ...}`，L1318 与 L1231 为字段级/局部修复构造受限 schema。DSH seam 没有承载这些字段的位置，走这条路等于**先放弃原生结构化输出，再在 DSH 之外把它加回来**，复用收益被抵消，同时承担 Node 运行时与 rc 版本依赖。

### B. 在 Python 内按 DSH seam 重建（推荐）

保留 Python 与现有 wire，把 DSH provider 的**形状**搬过来：

| DSH 概念 | 现有位置 | 迁移动作 |
|---|---|---|
| `StreamChunk` 判别联合（`block-start` / `text-delta` / `reasoning-delta` / `tool-call-delta` / `block-end` / `usage` / `finish`） | `ModelEvent` L117 + 私有 `_ProviderState` L112 | 合并为单一封闭联合，`block-end` 携带完整块 |
| 终结 `finish` 块 + `LlmFailure` 稳定码（`NO_ADAPTER`/`AUTH`/`RATE_LIMIT`/`TIMEOUT`/`EMPTY_RESPONSE`/`CONTEXT_WINDOW_EXCEEDED`） | 抛异常 + `_normalize_error` L437 | 增加终结块路径，错误码表对齐 |
| `LlmAdapter` 注册表 + `NO_ADAPTER` | `create_provider` L1113 对 `adapter == "openai_compatible"` 硬编码 | 引入注册表，保留现有 adapter 作为唯一实现 |
| `BlockAssembler` 共享折叠 | `collect_response` L959–L1026 内联累加 | 抽出独立折叠器，`interruptedBlocks` 语义对齐 |
| `ResolvedRetryPolicy`（service 不重试） | `transport_policy.py` 把流式回退混进验收 | 把回退许可与重试策略从验收里分离 |
| replay 状态只在一个 adapter 实例内 | `_provider_fields` + `_origin()` L661 | 语义已接近，补齐"跨实例丢弃 + 降级而非失败" |
| `TokenUsage` 分离 cache 字段 | `Usage` L102 | **不建议改**：会改变现有记账语义，需单独决策 |

阶段划分（每阶段可独立回退）：

1. **词汇表**：新增 `StreamChunk` 联合与 `finish`，旧 `ModelEvent` 保留为兼容别名；`collect_response` 两者都接受。加回归用例。
2. **注册表**：`create_provider` 改为注册表查询，保留 `openai_compatible` 唯一注册项与现有报错文案。
3. **折叠器**：抽出 `BlockAssembler` 等价物，`collect_response` 改为消费它。
4. **策略分离**：`transport_policy` 的回退许可与 retry policy 解耦。
5. **清理**：删除兼容别名，更新 [openai-compatible.md](../../integrations/openai-compatible.md) 与 [runtime-ownership.md](../../architecture/runtime-ownership.md)。

不改动：`responses.py`、`contract.py`、`domain.py`、`catalog.py`、`request_policy.py`、`runtime_profile.py`、`legacy_migration.py`、`response_format.py`、`observations.py`、`probes.py`、`verification.py`。

### C. 选择性吸收

只对齐 `finish` 终结块、错误码表、usage 字段语义、retry 策略归属，不动其余。风险最低，但只覆盖 provider 逻辑的一小部分，不应称为"替换"。

## 推荐

**结论更新（2026-09-24）**：代码迁移在任何形态下都不是本项目要做的方向。上文 A/B/C 的取舍仅作为"为什么不迁移"的记录保留。真正要吸收的是 DSH 的**设计**——模型配置逻辑与上下文策略——评估见[吸收 DSH 的模型配置逻辑与上下文策略](dsh-config-and-context-absorption.md)。

以下原有推荐保留为当时的技术判断，不再作为行动项：

推荐 **B**，理由：

1. 它是唯一能保住原生结构化输出与 `tool_choice` 的路线，而这两项是当前修复链的硬依赖。
2. 不引入 Node 运行时，不改变 PyInstaller 分发与离线安装。
3. 满足现有架构约束（见上文「架构约束」一节），无需放宽 `test_only_model_provider_imports_openai_sdk` 等断言。
4. 13 个 `model_runtime` 模块中 10 个本就没有 DSH 对应物，任何路线下都会留下；B 承认这一点而不是制造 split-brain。
5. 若未来 DSH seam 补齐结构化输出并稳定到正式版，A 的复用收益会重新成立 —— B 的词汇表迁移正是 A 的前置步骤，不浪费。

不建议在 `Usage` 记账语义上跟随 DSH：本项目 [runtime-ownership.md](../../architecture/runtime-ownership.md) 与 `observations.py` 现有约定已覆盖该职责，改动会影响计量报告。

## 未执行项

本评估未执行以下内容，不得据本文推断其结论：

- 未安装或构建任何 DSH 包，未验证 `@deepseek-ai/dsh-llm` 在本项目环境可解析。
- 未运行任何构建、测试或 CI 门禁；本文引用的测试与架构断言来自静态阅读，未执行验证。
- 未发起任何真实模型调用，未做双跑对照，未测量延迟或计费差异。
- 未评估 `llm-pi-ai` 对 `openai-completions` 网关的实际兼容度（本项目面向任意 `baseUrl` + API Key 的自定义网关，DSH 侧为 catalog 驱动）。
- 未核对 DSH `EMPTY_RESPONSE`、`CONTEXT_WINDOW_EXCEEDED` 等码与本项目现有 `code` 的逐项映射。

## 待决策

1. 是否接受 B，以及是否把 `Usage` 语义排除在迁移外。
2. 词汇表迁移（B 阶段 1）是否单独作为一个可发布的中间状态，还是与后续阶段合并。
3. 若考虑 A，需要先确认 DSH seam 是否会新增结构化输出字段 —— 这是 A 能否成立的前置条件。