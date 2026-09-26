# 吸收 DSH 的模型配置逻辑与上下文策略

评估日期：2026-09-24。来源仓库 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)（本地检出 `D:\download\dsh\deepseek-harness`，对应 npm 上的 `0.1.7-rc.1`）。

本文只讨论**设计层面的吸收**，不涉及代码迁移。为什么不迁移见[替换可行性评估](dsh-provider-replacement.md)：DSH 的 provider 是 TypeScript + Cordis 的 seam，本项目是 Python，且 `model_runtime` 13 个模块中 10 个在 DSH 侧没有对应物。

本文是**未执行方案**：没有改动源码，没有运行构建、测试或真实模型调用。所有 DSH 侧引用来自只读阅读，不构成对其运行时行为的实测。

引用格式：DSH 文件以检出根为相对路径；本项目文件链接到源码。

## 一、模型配置逻辑

### 1.1 两侧对照

| 维度 | DSH 做法 | 本项目现状 | 差距 |
|---|---|---|---|
| 路由身份 | provider 是配置 dict 的 key，写入 `provider` 字段被显式拒绝；改名 = 新增 + 删除 | `profile_id` 标识配置，`model` 独立字段 | 已具备，且更适合本项目（一个 provider 下多模型切换） |
| 声明与激活分离 | `LlmConfigurableProvider{provider,displayName,settingsNs,settingsPath,declared?,error?}`；零 route 即休眠态；配置界面把"目录"与"已注册路由"合并展示 | 无休眠概念：配置了就是一条 profile，`resources/model_catalog` 只提供端点能力 | **缺**。无"可激活但未配置"的展示与诊断 |
| 配置分层 | installed catalog 铺底 → route 级 → 模型条目级 → 调用方；每层只声明自己知道的事实，未声明交给下一层 | 契约 `capabilityContract` + 用户选择 `userModelSettings` + 目录 + 手工覆盖 | 已具备 |
| 默认值继承方式 | 逐字段 spread 覆盖，不枚举字段（`llm-pi-ai/src/catalog.ts:912-931`） | `merge()` 深合并 + `MISSING` 哨兵 + `None` 墓碑（[request_policy.py](../../../src/model_runtime/request_policy.py#L19)） | 本项目更严格（有显式删除语义） |
| 展示目录 vs 正确性元数据 | `listModels` 明确 advisory，可以接受未列出的 model id；`resolveModel` 返回 correctness-sensitive 的 context/输出上限/模态/`systemPromptUpdate`，且缺失要"缺席"不猜 | `catalog.py` 精确端点匹配 + `contract.py` 能力契约；`capability.source ∈ {metadata, catalog, manual, legacy}` | 已具备，语义等价 |
| 适配器默认值物化 | 只在调用方省略时填入；`LlmCallConfigAdapterDefaults` 标记**哪些字段是适配器填的**，并写进请求头 | `EffectiveRequest.sources` 记录每个参数来自哪一层（[request_policy.py](../../../src/model_runtime/request_policy.py#L31)） | 已具备 |
| 参数支持度表达 | 无三态类型；用 per-route / per-model **compat 开关**声明端点行为，配 `Record<Key,true>` + `AssertNever` 编译期漂移门 | `status ∈ {supported, conditional, unsupported, fixed, unknown}` + `domain{type,range,enum,enforcement}` + `ui_hint` + `evidence`（[contract.py](../../../src/model_runtime/contract.py)） | 本项目**表达力更强**；缺的是 DSH 的**漂移门**思路 |
| 未知网关的保守姿态 | 未声明协议默认按 `openai-completions` 探；未知 key 一律拒绝而非静默丢弃 | `status: unknown` + `domain.enforcement: hint` + `default_mode: omit`（[generic-openai-compatible.json](../../../resources/model_catalog/generic-openai-compatible.json)） | 已具备，同构 |
| 凭据与配置分家 | settings 只存 `apiKeyEnv` 引用，值在独立凭据平面，每请求解析 | 配置存 `credentialTarget` 引用，值在 Windows 凭据库（[config.py](../../../src/storage/config.py#L210)、[credentials.py](../../../src/storage/credentials.py)） | 已具备 |
| 草稿凭据不落地 | `LlmModelDiscoveryRequest` 把正在编辑的 baseURL/apiKey 随请求直传，从不存储；表单里的一次性 key 优先于存储 key | `SettingsService._draft()` 支持未保存的配置；检测与单项验证复用该入口 | 已具备；入口区别及回归边界见 A4 |
| 错误码按修复动作划分 | `INVALID_CREDENTIAL`（改一个坏值）与 `MISSING_CREDENTIAL`（补一个缺失值）语义与重试策略都不同 | `ModelProviderError.code` 有 `invalid_request`/`protocol`/`metadata_unavailable` 等 | **缺**这一区分维度 |
| 拓扑变更原子性 | 整批候选集校验通过才生效，被拒的改动保留旧状态继续服务；统一用无载荷 `llm/adapters-updated` 通知 | `reset_model_provider` / `reload_model_provider` 整体重建缓存（[provider.py](../../../src/model_runtime/provider.py#L1142)） | 已具备，本项目更简单 |
| 配置生效时机 | 下一个请求生效，无需重启；prepared call 绑定同一 adapter generation，避免 HMR 混代 | `get_active_provider` 以 profile + api key 为缓存键（[provider.py](../../../src/model_runtime/provider.py#L1122)） | 已具备 |
| 上游漂移防护 | 用 `Record<Key,true>` / `satisfies` / `AssertNever` 把 vendored 库新增字段变成**编译错误**（`llm-pi-ai/src/catalog.ts:226-230,433-468`） | 无等价机制 | **缺**。见 A1 |

### 1.2 值得吸收的

**A1. 用漂移门对抗上游字段变化**
DSH 对 pi-ai 的 compat 字段做双向证明：文档里的字段都被 offer、被 offer 的都有文档、类型与上游一致。上游新增字段会导致编译失败，而不是配置静默失效。
本项目对应面是 `resources/model_catalog/*.json` 的 `parameters`/`capabilities` 与 `contract.py` 的描述符集合。OpenAI-compatible 生态新增参数（例如新的推理控制字段）目前不会触发任何失败，只会被忽略。
采纳动作：加一个测试，断言每个目录条目的参数名集合是 `contract.py` 已知描述符集合的子集，且内置目录之间的描述符覆盖差异是显式列表。Python 没有编译期，用参数化测试表达同样的"漂移即失败"。
成本：低。风险：低（只加测试，不改行为）。

**A2. 错误码按"如何修复"而不是"哪里出错"划分**
DSH 把凭据问题拆成 `MISSING_CREDENTIAL`（补一个值）和 `INVALID_CREDENTIAL`（改一个坏值），两者重试策略不同。
本项目当前只有面向传输的码。用户侧"服务地址填错"和"密钥没填"都落到 `invalid_request` 附近，界面无法给出不同的下一步。
采纳动作：在 [provider.py](../../../src/model_runtime/provider.py#L229) 的 `ModelProviderError` 码表增加与修复动作对应的码（至少区分"缺凭据"与"凭据无效"、"端点不可达"与"端点拒绝参数"），并在 [job_errors.py](../../../src/application/job_errors.py#L153) 的投影里保留该区分。
成本：中（涉及文案与 i18n 表）。风险：中（改的是公开错误语义，需要回归）。

**A3. 休眠 provider 目录与配置诊断就地修复**
DSH 的目录让"可配置但未激活"的 provider 出现在界面上，且 `LlmConfigurableProvider.error` 携带配置诊断，单个坏配置不拖垮其他模型。
本项目 `resources/model_catalog` 已有 12 个端点条目，但界面只展示已保存的 profile；用户要新增只能从空白表单开始，"这个端点我们已知"这一信息没有传到界面。同时一条坏 profile 的失败只在请求时暴露。
采纳动作：把目录条目的 `id`/`displayName` 投影到模型设置页作为可激活项；在 profile 上增加一个可投影的配置诊断字段，由保存校验写入而不是由请求失败回填。
成本：中。风险：中（触及设置界面与 HTTP 契约，需要同步[HTTP 参考](../../integrations/http-api.md)）。

**A4. 草稿端点勘察：一次性凭据不落地**
DSH 在用户还没保存 provider 时，允许把正在编辑的 baseURL/apiKey 随发现请求直传，从不存储；表单里输入的一次性 key 优先于已存 key。
本项目已具备这条路径：[settings.py](../../../src/application/settings.py) 的 `SettingsService._draft(id=None, api_key=..., **values)` 构造未保存的配置；`detect_profile()` 和 `verify_profile()` 都复用它。草稿 API Key 不会通过这些操作写入配置或凭据库。编辑已有配置时，显式提交的 key 优先；地址改变而未提交新 key 时，不复用原地址的已存密钥。
[Settings.tsx](../../../web/src/features/Settings.tsx) 的 `discover()` 在新建状态下不传 profile ID，仍可获取模型列表或加载能力配置；`verify()` 使用相同草稿并要求单次验证确认。`test_connection(profile_id, ...)` 对应已保存配置的“测试连接”按钮，不能由该按钮要求已有 ID 推断全部操作都必须先保存。
维护范围：复用现有入口，保留“模型列表可访问不等于生成能力已验证”的区分；回归覆盖未保存草稿、临时 key 优先、换址不泄露旧 key，以及取消验证不产生模型请求。不新增第二条凭据通道。相关服务测试见 [test_application_settings.py](../../../tests/test_application_settings.py)，浏览器流程见 [web_model_settings_e2e.py](../../../scripts/web_model_settings_e2e.py)。
成本：低（现有流程的回归与入口说明）。风险：低；凭据持久化与验证审批边界保持不变。

**A5. 一句话重申（无需改动）**
本项目 `EffectiveRequest.sources` 与 DSH 的 `adapterDefaults` 是同一个想法，且本项目已经做对了。后续不要把 `sources` 简化掉。

**A6. 配置写入的乐观并发**
DSH 的 settings 写入带 `revision` 并拒绝陈旧写入（`packages/settings/settings/src/index.ts:44-64,393-395`），即两个窗口同时编辑同一配置时后者不会静默覆盖前者。
本项目配置由单个用户在本机操作，但 Web 与 MCP 两个入口可并发保存。采纳动作：在保存路径上引入等价的修订号或内容指纹校验，冲突时报错并保留现有目标，而不是后写覆盖。
这条与[运行时所有权](../../architecture/runtime-ownership.md)中"已有目标优先"的迁移原则同向。
成本：中。风险：中（触及保存路径与并发测试）。

**A7. 写时严格、读时宽容**
DSH 的 `strict` 写入拒绝不可服务的配置，而读取时把诊断留在目录里以便修复（`llm-pi-ai/src/config.ts:402-409`、`catalog.ts:8-11,932-943`）；单个坏模型不拖垮整条 route。
本项目保存时的校验已经偏严格，但"读时容错"这一半较弱：一条 profile 的问题只在请求时以 `ModelProviderError` 暴露，且没有把诊断附着在配置本身上。采纳动作与 A3 合并实施。
成本：并入 A3。风险：并入 A3。

### 1.3 已具备、不要重复造

- 能力契约 v3 的 `status`/`domain`/`ui_hint`/`evidence` 四段式比 DSH 的布尔 compat 开关表达力更强，且已经覆盖"声明而非探测"的姿态。
- `capability.source ∈ {metadata, catalog, manual, legacy}` 与 DSH 的 `declared` 标志同构。
- `credentialTarget` 的配置/凭据分离与 DSH 等价。
- 草稿发现与单项验证共用 `SettingsService._draft()`；已有临时凭据优先和换址隔离，维护范围见 A4。
- [observations.py](../../../src/model_runtime/observations.py) 的有界遥测（`MAX_ROWS`/`MAX_AGE`、只保留标量与短标识符、不记录 prompt/路径/凭据）已经比 DSH 的对应约定更严格。
- `None` 墓碑语义（显式删除沿 `extra_body` 嵌套路径传播）DSH 没有等价物。
- "用户配置的输出上限"与"模型能力上限"分离：DSH 用 `configuredMaxTokens` vs `Model.maxTokens`，本项目由 [_output_limit](../../../src/application/context_compiler.py#L40) 先读用户选择再回退契约边界，同构。
- 未知网关的保守默认：DSH 对未知 key 一律拒绝而非静默丢弃；本项目用 `status: unknown` + `default_mode: omit` + `enforcement: hint`，同样不臆测。

### 1.4 明确不采纳

- **路由键即身份**：DSH 需要它是因为 route 名进入请求头、凭据 scope 和会话日志；本项目 `profile_id` 与 `model` 已分离，且支持一个 profile 内切换模型。
- **`Volatile` / HMR 热替换**：本项目是 PyInstaller 一次性进程，没有插件热替换场景。
- **`agent-default-model` 的"不校验目录成员资格"**：本项目在保存时就要求端点/模型明确，这一宽松性不适合。

## 二、上下文策略

### 2.1 两侧对照

| 维度 | DSH 做法 | 本项目现状 | 差距 |
|---|---|---|---|
| 计量方法 | 固定启发式（4 字符/token）+ 可复用 provider usage 锚点；`baseline.kind ∈ {usage, estimated}` | 确定性启发式 + wire 级实测（`wire_token_estimate`）；`token_estimate: deterministic_heuristic_estimate`（[context_compiler.py](../../../src/application/context_compiler.py#L552)） | 本项目**更贴实际 wire**；缺锚点复用 |
| 锚点复用规则 | 仅当最近一次成功调用的规范化请求封套与当前 `requestHeader` 匹配、且其总量不低于该次路由计价锚点时复用；否则整体重算 | 每次编译重算，不复用 provider 返回的 usage | **缺**。见 C7 |
| 相对变化表达 | 有符号 `surfaceDeltaTokens`，两侧在同一路由下重新计价 | 只有绝对值 | **缺** |
| 单一价格驱动 | 触发、保留、范围选择都读同一个 `TokenSurfaceNode.tokens`；替换用 `heuristicTokens` 做 shadow price 以保持增量折叠自洽 | 各片段独立 `estimate_tokens` 累加 | 部分缺（无 shadow price 概念） |
| 压缩触发时机 | 主动阈值 `floor(min(W × thresholdRatio, W − O − headroomTokens))`，默认 `0.8` / 65536；另加 `CONTEXT_WINDOW_EXCEEDED` 后的溢出恢复 | **事后**：仅在常规裁剪后仍超 `usable` 时压缩（[context_compactor.py](../../../src/application/context_compactor.py#L194) 的 `budget_exceeded_after_compaction` 闸门） | **缺主动性**。见 C3 |
| 预算扣除 | `W − O − B`，`B` 默认 65536 | `window − reserved_output − 4096(协议) − safety_margin`，`safety_margin = min(16384, max(2048, W×0.02))`（[context_compiler.py](../../../src/application/context_compiler.py#L75)） | 本项目更保守（2% 且有 16K 上限） |
| 保留比例 | 最新 `retainRatio`（默认 0.16）的 `W − O` 逐字保留 | `recent_budget = max(1024, min(16000, usable × 0.16))`（[context_compactor.py](../../../src/application/context_compactor.py#L106)） | **已对齐**（同 0.16） |
| 每模型策略 | `modelPolicies` 按 provider/model 覆盖 `thresholdRatio`/`retainTokens`；一个后端服务多种上下文尺寸 | `model_budget` 按 profile 计算窗口，但**压缩阈值与保留策略是全局单一** | **缺**。见 C4 |
| 压缩次数 | 溢出后压缩并重试，可多轮；每次省略更多保留项直到无可省略 | **至多一次** checkpoint 调用（[context_compactor.py](../../../src/application/context_compactor.py#L192)） | 本项目更保守，可接受 |
| 能力边界声明 | 明确写出"不能减少 system prompt / tools / 会话前缀，不能拆开不可分割单元" | 未声明 | **缺**。见 C5 |
| 系统提示词变更 | `systemPromptUpdate: 'in-history'` 表示模型读任意位置的最新 `system` 消息，变更可追加在缓存历史之后 | `with_response_language` **改写首条 system 消息**（[provider.py](../../../src/model_runtime/provider.py#L208)） | **缺**。见 C6 |
| 图片预算 | 超预算失败码携带需卸载的**数量** `offloadImages`；执行者按模型请求序选最旧的，替换为占位文本（含只读路径），记一条 `image/offload` 事件后重试 | 编码后超 **45 MiB** 直接抛 `image_payload_too_large`（[provider.py](../../../src/model_runtime/provider.py#L569)） | **缺卸载路径**。见 C1 |
| 图片计价 | 适配器声明 per-route `imageRequestPricing`，按"视觉 token + 模型可见文本"给每次出现计价，计价结果同时驱动触发/保留/范围选择 | 预算里**没有图片项** | **缺**。见 C2 |
| 文件/二进制投影 | 任何 provider 都拿不到文件字节，每个 `FileBlock` 投影为一行确定性句柄文本，模型按需用工具读取 | 图片走内联 base64；GXW/工程文件不进入模型 | 本项目**已经是不传字节**，方向一致 |
| 计量弱点自述 | 明确写出"4 字符/token 对 CJK 与 JSON Schema 低估" | 未声明 | **缺**。见 C8 |
| 保留边界 | 从尾向头累加路由计价 token 到 `retainTokens`，再向前回退直到 **tool-call/result 成对**（`compaction-basic/src/region.ts:117-155`）；surface node 0 的 system prompt 永不进入压缩范围 | `_recent_history` 按预算切分 `wire_history`（[context_compactor.py](../../../src/application/context_compactor.py#L106)） | **缺成对性保护**。见 C9 |
| 压缩调用形态 | 逐字节**重放上一次真实请求的可缓存前缀**（node 0 的 system prompt + tools + 被遮蔽区间的派生消息），末尾只追加压缩指令，从而复用 warm prefix cache（`compaction-basic/src/region.ts:544-563`） | 另起一个两消息请求（system + 用户 JSON 载荷，[context_compactor.py](../../../src/application/context_compactor.py#L211)） | **缺前缀复用**。见 C10 |
| 竞态复核 | 异步摘要返回后重新 `measure()` 并深度比对节点向量与所选区间，变了就整体作废（`region.ts:430-468`） | 压缩后重新编译 wire，但未比对摘要期间源是否变化 | **缺**。见 C11 |
| 压缩事务 | `compaction/start`（durable 锁）→ `summary` → 一次 surface 替换 → `compaction/end`；崩溃留下可检测的孤立锁（`docs/subsystems/compaction.md:19`） | 单次调用 + `status: model_failed` 标记，无事务 | **缺**。见 C12 |
| 测量与策略分离 | 计量服务**无配置、无模型 profile**，被压缩、占用率 UI、遥测共享；阈值与保留留在可选的压缩后端（`2026-07-20-routed-model-context-and-compaction-policy.md:23`） | `model_budget`（测量）与压缩阈值（策略）同在 [context_compiler.py](../../../src/application/context_compiler.py#L75) / [context_compactor.py](../../../src/application/context_compactor.py) 内 | **缺分离**。见 C13 |
| 策略合法性的加载期校验 | `retainRatio` 继承后若不低于 `thresholdRatio` 则插件加载失败；重复/未知目标同样失败（`2026-07-20-...:27`） | 无等价校验 | **缺**（并入 C4） |
| 状态区分 | `unknown` 与 `exhausted` 是不同状态 | `budget_state ∈ {unknown, unusable, available}`（[context_compiler.py](../../../src/application/context_compiler.py#L84)） | **已具备**（同构，且注释已写明理由） |
| 交付记账 | — | `fact_coverage` 的 `candidate_evidence`/`budget_omitted`/`unresolved` 三态 + 编译后按完整块身份复核（[instruction-fact-delivery.md](../../architecture/instruction-fact-delivery.md)） | 本项目**独有且更强** |

### 2.2 值得吸收的

**C1. 图片预算：从"硬失败"改为"卸载 + 占位符"（推荐优先）**
这是本次对照中差距最具体、收益最直接的一项。当前行为：请求体编码后超过 45 MiB 就抛 `image_payload_too_large`，用户只能减少图片或压缩后重试，整轮工作白费。
DSH 的做法值得完整照搬其**形状**：失败码携带"还需要卸载多少个最旧的出现"；执行者按模型请求序从最旧开始选择，跳过助手节点与已标记项；被选中的图片替换为占位文本，点名附件和可用的只读路径；决策落地为一条可重放的事件，重试不消耗 provider 重试预算。
本项目对应改造：`ImageAttachment` 增加"已卸载"标记；投影时对已卸载项输出占位文本而不是 base64；[storage.session](../../../src/storage/session.py#L184) 已有附件落盘与只读读取路径，占位符可以指向它；把 `image_payload_too_large` 改造成携带数量的可恢复失败。
成本：中。风险：中（改变图片请求语义，需要同步图片相关测试与[响应语言](../../architecture/response-language.md)之外的验收）。

**C2. 图片进入预算计价**
当前 `model_budget` 只算文本：`wire_token_estimate` 针对消息文本，图片的 base64 体积与视觉 token 都不参与。对带视觉的分析工作流，预算会系统性低估。
采纳动作：在 [context_compiler.py](../../../src/application/context_compiler.py#L459) 的费用表里增加图片项，按"每次出现"计价（视觉 token + 模型可见文本），并让触发、保留、范围选择读同一个价格。
成本：中。风险：低（只影响预算数值与触发点，不改请求内容）。

**C3. 主动阈值触发压缩**
当前只在常规裁剪后仍超 `usable` 时压缩，属于事后补救：请求已经逼近上限，压缩后还要重新编译并可能再失败。
DSH 用 `floor(min(W × 0.8, W − O − 65536))` 在到达上限前触发，另外保留"溢出后压缩并重试"作为兜底。
采纳动作：把 [context_compactor.py](../../../src/application/context_compactor.py#L194) 的单闸门改成"主动阈值 或 事后超限"两者取先，阈值比例与 headroom 用 DSH 的默认值起步再按本项目实测调整。
成本：低。风险：中（会更早触发压缩 = 更早花一次模型调用，需要权衡；建议先用观测数据决定比例）。

**C4. 每模型压缩策略覆盖**
一个后端服务多种上下文尺寸这一需求本项目同样成立（目录里既有 256K 级也有更小的端点）。
采纳动作：仿 `modelPolicies`，允许按 profile/模型覆盖阈值比例与保留 token 数，默认值保持全局。
成本：低。风险：低。

**C5. 把压缩的能力边界写进文档**
DSH 明确声明压缩**做不到**什么：不能减少 system prompt、tools 或会话前缀，不能拆开一个不可分割单元（例如单个巨大工具调用）。
本项目有同类边界但没写出来。采纳动作：在[调用契约](../../architecture/generation-call-contracts.md)或本文档的落地版本中写明，并声明压缩失败时保留恢复材料。
成本：极低。风险：无。

**C6. in-history 系统提示词变更，保护前缀缓存**
当前 `with_response_language` 在每个请求上剥离并重写首条 system 消息里的语言指令（[provider.py](../../../src/model_runtime/provider.py#L208)）。同一语言下结果稳定，但用户中途切换响应语言时会改写消息 0，导致该会话的前缀缓存整段失效。
DSH 的对应设计是把"最新 `system` 消息在任意位置都生效"声明为**模型元数据**（`systemPromptUpdate: 'in-history'`），于是提示词变更可以追加在缓存历史之后，而不是重写前缀。
采纳动作：先核对本项目实际使用的端点是否具备 in-history 语义（这是端点事实，不能假设）；具备时，把语言指令从"重写消息 0"改为"在末尾追加一条 system 消息"。不具备时保持现状并把该限制写进[响应语言](../../architecture/response-language.md)。
成本：中。风险：中（依赖端点行为，必须实测；本项目现在是 2 个 profile 系列 + 任意 compatible 网关，不能一概而论）。

**C7. usage 锚点复用与有符号增量**
当前每次编译都重算，provider 返回的真实 usage 不参与下一轮的估算。
DSH 的规则值得吸收其**约束条件**而不只是机制：只在"规范化请求封套匹配"且"锚点总量不低于计价结果"时才复用，避免把一次异常小样本当成常态；变化量用有符号增量并在同一路由下重算两侧。
采纳动作：在观察记录里已有 usage 证据（[observations.py](../../../src/model_runtime/observations.py)）的基础上，为预算增加"可复用锚点"路径，先只用于报告对照，不参与触发决策。
成本：中。风险：中（计价语义变化会影响计量报告，需按[报告约定](../../reports/README.md)留对照记录）。

**C8. 声明计量的已知弱点**
DSH 自己写明：4 字符/token 的启发式对 **CJK 文本和 JSON Schema 文档**低估（`packages/llm/token-meter/src/estimate.ts:13-19`、`compaction-basic/README.md:258`）。
本项目的两个主要场景恰好同时命中这两个弱点——中文界面与需求文本，以及重度使用的 JSON Schema 结构化输出（[generation_contract.ladder_response_schema](../../../src/plc/generation_contract.py)、[capability-contract-v3](../../integrations/capability-contract-v3.md)）。这意味着预算可能系统性偏松。
采纳动作：把这一弱点写进预算报告的 `token_estimate` 说明，并在图片计价（C2）同一个改动里优先用 wire 实测值校正，而不是继续叠加启发式。
成本：极低（文档）到中（校正）。风险：低。

**C9. 压缩保留边界必须保持 tool-call/result 成对（建议并入阶段 1，但需先核实）**
这是第二个疑似直接缺陷。DSH 在选定保留起点后会向前回退，直到助手 tool-call 与对应 result 成对（`toolPairingBalancedBefore`），避免把一个工具往返拆到压缩边界两侧。
本项目在 [select_compactable_context](../../../src/application/context_compactor.py#L96) 里按预算切分 `wire_history`。**在已阅读的代码路径中没有找到等价的成对性检查**，但这不构成结论——切分函数本身未逐行核对，实际可能已经避开。工具往返被拆开会产生协议上无效的请求（助手 tool-call 没有对应结果，或结果没有对应调用），所以值得先确认再决定改不改。
采纳动作：先核对切分点是否已经保证成对；未保证时，在保留起点上加入等价的回退规则，并在回归用例里加入"工具往返恰好跨边界"的参数化样本。
成本：低（核实）或低-中（补规则）。风险：低（收窄切分点，行为更保守）。

**C10. 压缩调用应复用会话前缀**
DSH 的摘要调用不是另起一段文本，而是逐字节重放上一次真实请求的可缓存前缀，末尾只追加压缩指令——因此辅助调用是会话的真正前缀，能命中 provider 的 warm prefix cache，而且摘要看得见真实对话。
本项目当前构造一个独立的两消息请求（system + 用户 JSON 载荷），既不复用缓存，摘要也只能看到被挑选出来的片段而非真实上下文形态。
采纳动作：让 checkpoint 调用复用当前 wire 的可缓存前缀并只追加压缩指令。这一步与 C3（主动阈值）叠加时收益最大：更早触发也能更便宜。
成本：中高（需要构造"前缀 + 指令"的请求形态，且要保证前缀与上一次请求逐字节一致）。风险：中（改变压缩质量，需人工比对摘要结果）。

**C11. 摘要返回后复核源未变化**
DSH 在异步摘要返回后重新测量并深度比对节点向量与所选区间，任一变化就整体作废，避免把摘要套用到一个已经不同的历史上。
本项目在压缩调用返回后重新编译 wire（[context_compactor.py](../../../src/application/context_compactor.py#L224) 之后的路径）。**重新编译本身不等于复核**：若摘要期间的输入已经变化，重新编译会照常接受这份摘要。这一点未逐行核实，属于待确认项。
采纳动作：记录压缩发起时的 `wire_sha256`（该字段已在预算报告里存在）或节点指纹，返回后比对，不一致则丢弃摘要并重新评估而不复用。
成本：低。风险：低。

**C12. 压缩事务化**
DSH 用 `start`（durable 锁）→ `summary` → 一次 surface 替换 → `end` 的顺序落库，崩溃在中间会留下可检测的孤立锁，而不是谎称成功。
本项目标记 `status: model_failed`，但正常中断（进程被杀、工作区被替换）可能留下"压缩半完成"的中间态而无从检测。
采纳动作：为压缩增加显式的开始/结束标记与孤立态检测，恢复时按[模型设置](../../guides/model-settings.md#迁移备份与恢复)的既有恢复原则报告冲突而不是强行继续。
成本：中。风险：中（涉及持久化状态与恢复路径）。

**C13. 测量与策略分离**
DSH 把计量做成**无配置、无模型 profile** 的服务，阈值与保留策略留在可选的压缩后端；理由是避免复制 replay 记账，也避免计量变成"第二个模型注册表"。
本项目 `model_budget` 同时承担测量与策略（阈值、保留、RAG 配额），压缩阈值又在 `context_compactor` 里。结果是任何策略调整都要动测量模块，且预算报告与压缩决策可能各自漂移。
采纳动作：把"测多少 token"与"超过多少才压缩"分到两个模块，压缩策略按 profile 可覆盖（C4）。这是结构建议，不急于实施，但会影响后续所有改动的位置。
成本：中高。风险：中。

### 2.3 已具备、不要重复造

- `budget_state ∈ {unknown, unusable, available}`：DSH 的"未知容量与已知耗尽不同状态"本项目已实现，且注释已写明理由。
- 保留比例 0.16：与 DSH 默认 `retainRatio` 相同，属于独立收敛，无需调整。
- wire 级实测：`wire_token_estimate` 直接测实际 wire，比 DSH 的纯消息启发式更贴实际。
- `fact_coverage` 三态 + 编译后按完整块身份复核：本项目独有的交付记账，DSH 没有对应物。
- 来源指纹：`context_plan_sha256` / `wire_sha256` / `persistent_spec_sha256` 等已经提供了 DSH `request/header` 想要的可核对性。
- 语言快照：`language_context` 提交时捕获、多阶段沿用，与 DSH 的"每请求纯函数"目标一致。

### 2.4 明确不采纳

- **`ContextForm` 六类词表**（instructions/catalog/snapshot/notice/relay/recall）：这个设计本身很好——它把"谁产生"（`kind`）与"是什么形状的信息"（`form`）作为两个正交轴，词表是语义而非视觉的，分类进 durable 数据所以恢复/外部日志无需 producer 在场也能渲染。但本项目已有 `superseded_by`、`absorbed_by_confirmed_fields` 这类表达取代关系的领域语义，直接引入通用词表会与它重叠并稀释。若将来需要，应按 DSH 的两轴原则**新增**而不是替换现有字段。
- **`EpochHeader` 全套**：本项目已有编译期指纹（`context_plan_sha256`/`wire_sha256`）与请求审计，且不把模型可见输入绑定到单一持久化日志，架构前提不同。
- **"Model-visible ⟺ logged" 不变式**：DSH 用"接口级不可表示"（deep-frozen + 独立 invariant 用全新 Session 重建自证）保证它，很彻底；本项目用来源证明与审计替代，不引入该前置。
- **`compaction-tool-result-pruner` 的独立执行器形态**：本项目工具结果规模由领域控制，不需要通用修剪器。
- **三段式 seam（Service Definition / provider / human consumer）**：DSH 把压缩契约、策略实现、`/compact` 命令分三个包，本项目没有插件生态，拆包只增加跳转成本。但其**内容**（C13 的测量与策略分离）值得吸收。

## 三、建议的采纳顺序

| 阶段 | 内容 | 成本 | 风险 | 依据 |
|---|---|---|---|---|
| 1 | C1 图片卸载 + C2 图片计价 + C5 压缩边界 + C8 计量弱点 + C9 成对性核实 | 中 | 中 | 当前行为的直接缺陷（或待核实项），用户可感知 |
| 2 | C11 摘要后复核 + C3 主动阈值 + C4 每模型策略（含加载期校验） | 低-中 | 中 | C3 的比例依赖阶段 1 的计价结果 |
| 3 | C10 压缩调用复用前缀 | 中高 | 中 | 前置是阶段 2 的触发点已经前移，否则收益不明显 |
| 4 | A1 漂移门 + A2 错误码按修复动作划分 | 低-中 | 低-中 | 纯测试与错误语义，可独立验证 |
| 5 | A3 + A7 休眠目录与读时诊断 + C12 压缩事务 + A6 写入乐观并发 | 中-高 | 中-高 | 触及设置界面、HTTP 契约、凭据与持久化 |
| 6 | A4 现有草稿流程的回归与入口说明 | 低 | 低 | 已有 `_draft()`、`detect_profile()`、`verify_profile()`；不新增凭据通道 |
| 7 | C7 usage 锚点（先只用于报告） | 中 | 中 | 需先有阶段 1-3 的计价基线才谈得上对照 |
| 8 | C13 测量与策略分离 | 中高 | 中 | 结构性调整，最后做，避免与前面阶段冲突 |
| — | C6 in-history（需先实测端点） | 中 | 中 | 前置条件是端点事实，未验证不得实施 |

## 四、未执行项

- 未安装或构建任何 DSH 包；所有 DSH 侧描述来自本地检出的文档与源码只读阅读，未运行其测试。
- 未运行本项目任何构建、测试或 CI 门禁；两侧对照中的"现状"来自静态阅读。
- 未发起任何真实模型调用，未测量任何端点的 `systemPromptUpdate` 行为（C6 的前置条件因此未验证）。
- 未测量 `floor(min(W × 0.8, W − O − 65536))` 在本项目实际工作负载下的触发频率（C3 的比例需实测后定）。
- 未核对 `capabilityContract` 描述符集合与 12 个目录条目的实际差集（A1 的断言范围未定）。
- 未评估 C1 的占位符文本对生成质量的影响。
- 未核对 [select_compactable_context](../../../src/application/context_compactor.py#L96) 的现有切分点是否已经实践上避开了工具往返（C9 的缺陷判断因此未成立，仅为待确认项）。
- 未逐行核对压缩摘要返回后是否复核了输入变化（C11 的缺陷判断同样未成立）。
- 未核对压缩范围是否已经排除首条 system 消息（DSH 明确保证 surface node 0 永不进入压缩范围）。
- 未测量 C10 的前缀复用能带来多少缓存命中收益（取决于端点是否做前缀缓存）。

## 待决策

1. 阶段 1 是否作为下一批实施内容，以及 C1 的占位符是否复用 [storage.session](../../../src/storage/session.py#L249) 的只读读取路径。
2. C3 的阈值比例：直接采用 DSH 的 0.8/65536，还是先用现有观测数据反推。
3. C6 是否值得为一个未验证的端点行为付出实测成本，还是先只把限制文档化。
4. C13（测量与策略分离）是否现在就定下模块边界，还是等阶段 2 的策略覆盖落地后再决定切分位置 —— 先做 C4 会加剧当前的混合状态。