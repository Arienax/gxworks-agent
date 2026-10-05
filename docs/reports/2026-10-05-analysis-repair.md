# 2026-10-05 分析协议与首次通过率

按用户要求切换到保存的官方 `deepseek-flash` 配置后，最终三次首次模型调用分别在 **55.142、48.786、43.459 秒**完成，均一次通过 Core 协议和绑定验证，没有发起修复。三份合同都保留三条原样 SFTL；没有声明寄存器计数器或近似状态机。但严格逐项审阅工艺提问，只有第二份问齐全部现场事实，第一、第三份仍有缺口，**首次协议通过率 3/3 不等于工程问题验收 3/3**。

局部修复的边界和合同投影已落实，性能结果仍有限制。官方新路径三次都满足冻结字段、精确实例、具体设备限制和绑定检查，修复耗时 27.989–52.113 秒；耗时和推理 token 仍高于旧路径。此前 intern-ai 新路径甚至达到 744.802 秒。**没有证明缩短上下文能够降低推理量，或已经消除过度思考**。首次直接通过是本轮主要目标；修复正确性单独验收。

证据入口为[摘要](data/2026-10-05-analysis-repair.json)、[官方最终首次分析](../../research/evidence/analysis-repair/20261005-sftl-official-final.zip)、[官方修复配对](../../research/evidence/analysis-repair/20261005-sftl-official-pairs.zip)、[官方初批首次分析](../../research/evidence/analysis-repair/20261005-sftl-official-first-pass.zip)、[intern-ai 首批对照](../../research/evidence/analysis-repair/20261005-sftl-pairs.zip)、[intern-ai 后续首次采样与中断](../../research/evidence/analysis-repair/20261005-sftl-first-pass.zip)、[最小原文和首个响应 witness](../../tests/fixtures/analysis_sftl_repair.json)、[测量脚本](../../scripts/benchmark_analysis_repair.py)。原附件未修改。真实首次分析与固定旧稿的局部修复分别验收；旧稿中被冻结的结构声明和登记方案不认证为工程正确。

## 来源、配置与测量边界

原作业为 `job_485443a0265c40ce97d4cb5363d799a8`，FX3U、Direct。附件首次调用 62.943 秒、1,475 推理 token、3,730 正文字符；第二次 382.992 秒、14,024 推理 token、4,148 正文字符。原分析作业共 447.466 秒，其中本地及间隙约 1.531 秒。该作业未进入生成、Core 构造、反例检查或真实设备操作。

| 项目 | 条件 |
| --- | --- |
| 旧组 | `26b57a6` 的字面源码，在隔离进程导入 |
| 新组 | `26b57a6` 加本次修改，运行前冻结；最终增补另外留源码快照 |
| 受测请求 | 附件当前原文；三条完整 SFTL、128 bit 跟踪区、D0 分类、X004 编码器移位 |
| 原保存预设 | `intern-ai`；`https://discovery-api.intern-ai.org.cn/v1`；`deepseek-v4-flash-0731` |
| 用户指定切换 | `DeepSeek`；`https://api.deepseek.com`；`deepseek-flash`，另起目录保留原记录 |
| 调参 | 保存的 `userModelSettings.parameters={}` 原样使用；未增加温度、推理预算、输出上限或思考开关 |
| intern-ai 实际 SDK 参数 | 除 messages 外为 `model=deepseek-v4-flash-0731`、`stream=true` |
| 官方实际 SDK 参数 | 除 messages 外为 `model=deepseek-flash`、`stream=true`、`extra_body.thinking.type=enabled`；沿用产品适配器 |
| 配对顺序 | 旧1、新1、新2、旧2、旧3、新3；每组同一个冻结首个响应 |
| 入口 | 修复经共享 `_request_analysis_response`；真实首次分析经 `analyze_requirement_streaming` 的 Direct 入口 |
| 环境 | Windows 11 build 26200，Python 3.13.14，项目 `.venv`；2026-10-05 至 2026-10-06，Asia/Shanghai |

这是应用服务入口测量，未经过 HTTP 作业调度或浏览器。固定首个响应的本地回放时间和用量全部排除，只计实际修复网络请求；真实首次调用单独列示。未控制服务负载、采样或供应商缓存，不推导总体性能收益。intern-ai 请求标识固定，但多数返回标识为 `dsv4-flash-vision`，只有新组第二轮修复返回 `deepseek-v4-flash-0731`；官方全部返回 `deepseek-flash`。两个端点使用各自保存配置，适配器的实际参数也有差异，不混合为同条件性能配对。已经完成的批次以字节比较确认全局活动配置未改；主动终止的 intern-ai 批次保留中断记录。

## 协议交付及接收

`TARGET_CONTRACTS` 集中定义四种 target 的字段、允许操作及最小示例，首次与修复提示和 Core 校验共用。`instruction_instance` 直接包含 `opcode/operands`；`category` 仅允许 `clear`。原 `violations` 字符串接口保留，`details` 增加路径、实际值和合法字段。具体设备的证据按既有身份规则比较，包括 `Y000/Y0`，同时保留词边界和当前原文逐字检查。

一次性修复目标来自协议错误及每条 claim 的独立编译回执。已知复数类型名和实例列表可无损转换；无证据、非法设备、缺实例原文、伪造或历史证据均进入失败清单。内部回复只含 `repairs`，claim 项替换值为数组，支持一对多拆分。只接受预先列出的路径，拒绝重复、缺失、越界路径及增删已识别目标；其余方案、必要问题和字段冻结。无依据首扫候选可删除，不能补造证据、设备或近似结构标签。

完整对象经合并、协议、绑定和原有语言验证后发布。内部补丁不进入 Web/MCP 分析对象、内容回调或预览；原始两次回复留在诊断。JSON 语法失败共用一次预算并使用独立短提示；二次失败不会发起第三次请求。

修复消息仅含当前请求、失败片段、合法形状和必须保留的目标，不携带手册、型号资料或历史分析。方法描述不能成为设备地址，其原文保留在修复回执和 `intent_context.requests`，继续进入生成上下文。未能投影的原值另标 `unresolved_values`、`projection_status=not_projected`。该保留不等于这些方法禁令已经成为机器验证的设备限制。

合同投影还修正了同批次多实例的累积：当前轮首次要求某 opcode 时替换旧轮实例，随后同 opcode 的本轮实例累积；后续 `clear/forbid` 仍按顺序生效。因此三条指定 SFTL 不再只留下最后一条。

空证据的首扫/周期候选也不能一律删除：当前原文有既有 Core 快路径识别的明确首扫或周期要求时，修复须重新提取该原文并保留要求；没有这种依据及设备锚点时才能删除无依据候选。该收尾修正没有改变本包六次新组修复请求的消息；包含原有语言说明的完整消息逐项比对一致，全部测得补丁再经最终 Core 离线通过。

## 官方首次分析与修复对照

用户指定更换官方供应商后另起目录，原 intern-ai 观察不覆盖。官方初批首次调用为 54.224、24.153、34.019 秒，首次协议通过 2/3。第一份 M0 claim 引用了只有 Y 输出的句子，绑定失败；第二份把按钮用途声明为源上升沿，同时仍在询问有效电平。随后首次提示明确每条证据必须覆盖该 claim 的全部设备、不能借请求另一句的地址补证据；未确认按钮电平或事件条件时不声明触发约束。补充后的源码重新冻结，以同一官方保存配置再测三次。

| 最终首次轮次 | Core 接受 | 模型秒 | 应用入口秒 | 输入 token | 推理 token | 正文字符 | 后续修复 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 是 | 55.142 | 55.822 | 15,826 | 9,954 | 10,002 | 无 |
| 2 | 是 | 48.786 | 49.262 | 15,826 | 10,039 | 4,173 | 无 |
| 3 | 是 | 43.459 | 43.916 | 15,826 | 9,815 | 3,496 | 无 |

全部请求和返回型号为 `deepseek-flash`，系统消息为 42,148 字符；首次手册和信息阶段沿用产品入口。耗时中位数 48.786 秒，推理 token 中位数 9,954。这些样本没有进入数百秒修复，但推理用量仍显著高于附件首次的 1,475；不同端点不能直接归因，本轮也没有给模型设置新的推理预算。

独立人工审阅按原始首个回复的实际问题验收，不用程序关键词匹配替代判断：

| 必要事实 | 第1份 | 第2份 | 第3份 |
| --- | --- | --- | --- |
| 物料间 D0 是否保证回零 | 未直接问清，仅问登记策略 | 已问 | 已问 |
| 同件 D0 分类能否变化 | 已问 | 已问 | 已问 |
| 怎样重新允许登记 | 已问 | 已问 | 已问 |
| 待登记结果在 Encoder shift 前如何保持 | 未直接问清 | 已问 | 未直接问清 |
| shift 后怎样消耗/清零 | 已问 | 已问 | 已问 |
| 启动、停止按下时有效电平 | 已问 | 已问 | 已问 |

三份只声明有依据的 self_hold/edge_trigger，前两份另有与直接跟踪位驱动相符的 direct_logic；没有新增首扫、按钮触发约束或 M 八进制误报。严格的结构与全部工艺问题验收为 **1/3**。这些问题仍等待用户回答，本轮没有生成或验证 PLC 程序，也没有证明逐件登记实现正确。

以下官方配对固定回放附件首个响应，仅真实调用一次修复；系统旧组 41,490 字符、新组 1,387 字符。

| 轮次 | 组 | 该组 Core 接受 | 完整局部边界/投影 | 修复秒 | 输入 token | 推理 token | 正文字符 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | 旧 | 否 | 未通过 | 13.776 | 17,293 | 1,946 | 4,533 |
| 1 | 新 | 是 | 通过 | 27.989 | 2,784 | 6,535 | 1,661 |
| 2 | 旧 | 是 | 未通过 | 12.499 | 17,293 | 1,640 | 4,018 |
| 2 | 新 | 是 | 通过 | 52.113 | 2,784 | 12,602 | 1,397 |
| 3 | 旧 | 是 | 未通过 | 11.362 | 17,293 | 1,433 | 3,524 |
| 3 | 新 | 是 | 通过 | 45.012 | 2,784 | 10,865 | 2,322 |

旧组第一份缺 `target.kind`；第二、第三份虽然被旧校验器接受，仍有未绑定方法值、设备别名未投影、只剩最后一条 SFTL 及改写已有效字段的问题。最终严格判据旧组 **0/3**、新组 **3/3**。新组系统和输入明显缩小，但修复耗时中位数 **45.012 秒，高于旧组 12.499 秒**，推理用量也更高；旧组较快的错误或不完整对象不能叫有效修复。该结果不支持“局部补丁本身加速”的结论。旧方案中被冻结的 `data_register_counter` 等可疑字段仍不认证为工程正确。

## intern-ai 固定首个响应的修复对照

下表全部计入失败；每项恰好一次真实网络请求。推理用量缺失保持未知。

| 轮次 | 组 | Core 接受 | 修复秒 | 输入 token | 推理 token | 正文字符 | 返回模型 |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| 1 | 旧 | 否 | 15.446 | 17,267 | 未知 | 3,619 | dsv4-flash-vision |
| 1 | 新 | 是 | 485.707 | 2,837 | 10,438 | 1,400 | dsv4-flash-vision |
| 2 | 旧 | 否 | 122.247 | 17,346 | 4,329 | 3,024 | dsv4-flash-vision |
| 2 | 新 | 是 | 94.438 | 2,837 | 8,213 | 1,395 | deepseek-v4-flash-0731 |
| 3 | 旧 | 否 | 170.494 | 17,346 | 894 | 3,485 | dsv4-flash-vision |
| 3 | 新 | 是 | 744.802 | 2,837 | 16,922 | 2,322 | dsv4-flash-vision |

系统说明由旧组 41,490 字符降到新组 1,387 字符；真实输入 token 也显著减少。但修复耗时中位数旧组为 122.247 秒、新组为 485.707 秒；旧组全失败，且供应商返回型号有差异。该结果支持协议正确率和局部边界，**不支持延迟或推理量改善**。

旧组第一、第三轮仍使用非法类型名；第二轮实例仍放错层次。新组三份原始补丁用最终 Core 离线回放，均保留全部冻结字段、三条原样 SFTL、M8011/M8012 具体设备禁令和规定的 Y 输出，并且没有被拒绝的 claim。第一份在线记录的输出别名判据误用了 Y0/Y1/Y2/Y3，后来改为独立的原文预期 Y0/Y2/Y4/Y6/Y10；原误判记录保留，纠正结果另写 `final_review.json`，未改原始观察。

## intern-ai 首次采样与中断

首次提示允许结构标签为空，不把 SFTL 跟踪近似声明为寄存器计数器。逐件登记需要确认物料之间是否回到无物料值、同件分类能否变化、重新允许登记的事件，以及登记结果在 Encoder shift 前后的保持、消耗和清零；启动、停止输入的有效电平仍是必要事实。格式修复冻结旧方案和必要问题，不承担这些判断。

第一份冻结提示的首轮在 20.905 秒返回，却漏掉必要问题、遗漏 transition 的 `from/to`，并把方法禁令表达成 `forbid category`。后续局部修复耗时 514.512 秒，还输出了非法的 `from_states/to_states` 数组，最终失败。这一观察证明只缩小修复上下文不能保证避免数百秒等待。

首批第二次首轮一次通过协议，耗时却为 987.171 秒、25,015 推理 token，并误报“M 地址按八进制、跟踪区不足 128 bit”，询问是否改变固定的区域或 K 值。其结构为 self_hold/edge_trigger，三条 SFTL 和具体设备禁令保留；输入电平、D0 回零及注入位消耗有问题，但同件分类变化没有问清。它不算工程验收通过。第三次尚未回包的旧提示采样在约 13 分钟后主动中断，以停止继续测已经暴露缺口的提示；原 worker 失败标记与中断原因都保留，实际调用次数、响应、用量和完整耗时未知，不填零，也不计入已完成调用的耗时统计。

首次提示随后补入 Core 的完整 transition 示例和单值 `from/to` 规则，明确 `category` 仅用于 `clear`，并要求用户提出“直接生成/不做分析”时仍确认每项缺失的必要事实。相同示例也交付给失败 trigger 的局部修复，其他已合法字段冻结。型号说明补充 `addressing` 的 X/Y 作用范围，以及从现有 Core 地址解析规则取得的逐类进制；M/D 仍为十进制。该变化只明确既有事实，没有改变地址策略、手册检索或信息阶段划分。

随后另冻一份提示，使用 `--phase fresh --first-only`。intern-ai 该批第一份一次通过协议，却耗时 527.866 秒、16,327 推理 token、4,878 正文字符；只有新物料事件、电平、输出持续及启停清空等问题，未明确问齐同件变化、重新允许登记和注入位生命周期。用户随后明确要求官方 Flash，正在接收推理的第二份被停止，第三份未开始。保留已完成结果与中断时接收进度；未完成响应及用量保持未知。官方两个批次分别记录，未覆盖这些失败。

`--first-only` 保持首次请求、保存调参、传输、检索及 Core 接收规则不变；若首轮需要修复，测量回调在第二次请求前停止并记首次失败，避免用长时间修复掩盖它。一次通过时仍执行共享入口的完整结果发布。停止修复后的失败耗时不能称为产品完整失败路径耗时。

## 离线验证与限制

最终扩大集合 **1,376 项通过、20 项跳过、2 项排除**，包括流式/非流式 witness、局部路径边界、四种 target 与非法操作组合、混合设备值、伪造证据、一次预算和补丁不泄漏、合同投影、语言、诊断、架构、地址解析和测量。最终运行耗时 86.54 秒。前期 1,365 项与后续定向集合有重叠，不相加。Hypothesis 使用独立样本，覆盖设备大小写和补零身份、表示转换幂等、冻结字段不变、补丁顺序及指定目标不丢失。

20 项跳过中，19 项要求 Linux Bash，1 项为当前 Windows 进程无法创建符号链接。两项失败在 `26b57a6` 字面源码隔离复现：既有 generation_contract 依赖白名单未包含 `plc.specification.behavior`；既有旧分析兼容测试预期未包含 `io_binding`。没有修改这两处产品行为或测试预期。唯一警告来自 Starlette/AnyIO 弃用别名。

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_analysis_format_repair.py tests/test_analysis_prompt_routing.py tests/test_analysis_prompt_integration.py tests/test_analysis_mode_jobs.py tests/test_analysis_design_rag.py tests/test_confirmed_generation_regressions.py tests/test_plc_semantics.py tests/test_semantic_contract_architecture.py tests/test_approach_contracts.py tests/test_intent_evidence_handoff.py tests/test_response_language.py tests/test_runtime_diagnostics.py tests/test_runtime_diagnostics_web.py tests/test_diagnostics_workflow_regressions.py tests/test_architecture_boundaries.py tests/test_architecture_boundary_cleanup.py tests/test_source_layout.py tests/test_web_architecture.py tests/test_workbench_service.py tests/test_workbench_planning.py tests/test_agent_b_measurement.py tests/test_plc_core_boundary.py tests/test_device_identity_delivery.py tests/test_hardware_read_only.py tests/test_index_register_operands.py tests/test_native_validation.py -q -k "not test_generation_contract_uses_only_stdlib_and_authoritative_instruction_registry and not test_legacy_analysis_output_restores_choices_without_writing"
```

能力覆盖审计为 19 个 enforced 项、零缺口、零失败。编译与工作树空白检查另外执行。复现及归档命令见[测量指南](../guides/agent-b-measurement.md#首次分析协议与局部修复)。本次没有提交、推送、修改原附件或操作真实设备；模型配置未改。实测限于 FX3U 的当前请求，不推广到其他型号、供应商或需求。
