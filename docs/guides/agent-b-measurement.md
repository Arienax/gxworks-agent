# Agent B 对照测量

## 准备输入

使用 [benchmark_agent_b.py](../../scripts/benchmark_agent_b.py) 的 `load_cases` 读取 JSONL。每行具有唯一的 `case_id` 和 `confirmed_spec` 对象；可选字段及实验分组由该脚本的 `main`、`ARMS`、`run_case` 定义。[agent_b_smoke_cases.jsonl](../../benchmarks/agent_b_smoke_cases.jsonl)是边界测试样本。[agent_b_operand_purpose_cases.jsonl](../../benchmarks/agent_b_operand_purpose_cases.jsonl)包含 SFTL、WSFL、BMOV、CMP、IVCK、TCMP 各两个参数变体，以及起保停对照。

[定向挑战案例](../../benchmarks/agent_b_operand_purpose_challenge_cases.jsonl)包含六个 CMP 方向/符号/数值变体，DECO、ENCO、ZRST 各两个参数变体，以及相同起保停对照。它提高参数映射难度，沿用原来的重复次数；编码位宽与区域点数、包含首尾的复位范围分别取独立预期。

用途案例只在确认规格中给需求、地址和参数含义，由模型自行映射操作数。独立预期放在顶层 `evaluation`，不交给模型；`load_cases` 拒绝在这类规格中预填 `instruction_instances` 或 `operands` 列表。合成预期依据手册定义单独抄录，改变地址、长度、移动量、通道、时间值；不使用当前解析器生成答案。

固定确认规格、模型配置、服务端点及评估器。原生或行为预期由对应工程证据提供；`oracle_evidence` 使用人工审核的资料，必须显式标记 `oracle_reviewed`。调优值在[模型配置](model-settings.md)中设置，已废弃的 `--effort` 参数不改变请求。案例不指定 `construction_examples` 时沿用当前范例设置。

## 用途对照组

默认两组通过当前 knowledge builder 取得同一份证据，CLI 在预检时缓存该上下文供实测使用。

| 实验组 | 交付内容 |
| --- | --- |
| `manual_text` | 当前已清理的相同手册正文，保留原有型号、顺序、角色、类型及已验证用途；移除新增的候选 `usage_facts` 及对应绑定回执 |
| `usage_bound` | 同一上下文，保留当前逐槽位候选用途、位置和来源 |
| `oracle` | 操作者已审核并提供的事实；没有审核材料时不运行 |

`manual_text_context` 只消融结构化候选用途，手册原文保持不变；既有完整块交付回执随之更新。对照使用最终 Context Compiler 之后、供应商请求解析器实际生成的请求，而不依赖组名或中间对象。`preflight_pairs` 要求规格、请求参数、非用途内容和范例配置一致，原文组候选绑定为零、用途组确有绑定；起保停对照两组请求完全相同。条件不满足时在联网前退出。

兼容组 `automatic` 沿用当前交付；`legacy_retrieval` 仅删除旧 metadata 字段，仍可能进入当前结构化检索，不能用作旧版本基线。旧版本综合收益需另行固定两个代码版本测量。

## 查看实验计划

在仓库根目录运行：

```powershell
python scripts/benchmark_agent_b.py benchmarks/agent_b_operand_purpose_cases.jsonl
```

不带 `--live` 或 `--preflight` 时只打印计划，不加载凭据或请求模型。`--arms`、`--repeat`、`--seed` 控制对照组、重复次数和随机排列；通过 `--help` 查询默认值。

`--preflight --output <私有新文件>` 会读取已保存模型配置，离线编译两组最终请求，不调用网络。使用 `--profile-id` 可选择已有配置而不修改活动配置；`--require-endpoint` 在任何调用前核对端点。两组实测会自动执行相同预检，并在结果旁保存 `.preflight.json`。

## 执行测量

以下命令会使用已保存的活动模型配置请求模型，产生相应费用。原始请求、模型输出、确认规格和预期只写入仓库外的新私有文件。仓库内输出或已有结果路径会被拒绝：

```powershell
$env:PYTHONUTF8 = '1'
$resultPath = Join-Path $env:LOCALAPPDATA 'GXWorksAgent/measurements/agent-b-purpose-new/live.jsonl'
python scripts/benchmark_agent_b.py benchmarks/agent_b_operand_purpose_cases.jsonl --live --arms manual_text,usage_bound --repeat 2 --seed 20261003 --require-endpoint https://api-inference.modelscope.cn/v1 --output $resultPath
```

内置 `evaluate_synthetic_case` 用独立固定预期比较指定指令的每个操作数，包括常量、地址、长度/移动量、单位和连续区首地址。它保留调用出现次数，要求指定指令恰好一处，且没有其他输出操作；同样的调用出现在同分支、其他分支或其他梯级也不能被去重为一次。额外的 COIL、MOV、SET/RST、定时器、计数器，无论写入结果区还是其他区域，都使封闭的单指令案例失败。此处“无额外副作用”限定为没有额外生成操作，不宣称指定指令本身没有已记录的区域写入或通信效果。

十进制与等值十六进制正常量可等价。负数的 16-bit CMP 十六进制写法在 `evaluation.constant_aliases` 中独立列出；不为其他常量猜测位宽、截断或循环折返。新案例的 `evaluation.gate` 给出独立原生条件指令及地址，例如 `{"op":"LDI","args":["X5"]}`；通过既有 Core 降低逻辑检查单个直接触点的地址和极性，缺失、重复或不同条件均不通过。这些评估字段不交给模型。

`checks`、`call_count`、`output_count` 区分参数映射、唯一调用、额外输出和门控失败。起保停复用 Core 控制结构和绑定谓词检查。其他领域评价通过 `--evaluator module:function` 接入已有评估器，不在 runner 复制 PLC 规则。

## CMP 关系二因素对照

`--cmp-relations` 只选输入文件中现有的 CMP 案例，使用四组：

| 组 | 实验标识 | 候选逐槽位用途 | 完整关系组 |
| --- | --- | --- | --- |
| A | `manual_text_no_relations` | 移除 | 移除 |
| B | `usage_bound_no_relations` | 保留 | 移除 |
| C | `manual_text` | 移除 | 保留 |
| D | `usage_bound` | 保留 | 保留 |

关系消融移除整个原子手册证据组，保留共同正文、规格、型号事实、顺序及范例。它同时更新块回执和对应必要关系维度，不把未交付关系记为已送达。这里的“原文”仍指当前已清理的共同手册正文，不代表未经打包的完整原页。产品默认交付完整关系；消融只在 benchmark 中存在。

`preflight_factorial` 离线编译四份最终请求，检查去除两个因素后内容完全相同、用途数量为 `0/3/0/3`、前两组无关系、后两组具有相同的非零完整关系组数量。组块以 `(case_id, repeat)` 为单位随机排列，同一块四组相邻执行，组内也随机排列。新跑 A/B 基线，避免跨测量时段比较旧基线与新处理组。

```powershell
$env:PYTHONUTF8 = '1'
$resultPath = Join-Path $env:LOCALAPPDATA 'GXWorksAgent/measurements/agent-b-cmp-relations-new/live.jsonl'
python scripts/benchmark_agent_b.py benchmarks/agent_b_operand_purpose_challenge_cases.jsonl --cmp-relations --live --repeat 2 --seed 20261003 --require-endpoint https://api-inference.modelscope.cn/v1 --output $resultPath
```

六个 CMP 案例的独立 `evaluation.cmp_behavior` 真值表包含小于、等于、大于三种启用输入，以及八种已有结果状态的禁用保持检查。`evaluate_cmp_behavior` 只解释封闭任务里的单个 16-bit CMP、LD/LDI 使能和 M 结果区；`cmp_reference_state` 不读取生产关系组或用途数据，不证明原生接受或 PLC 扫描。程序仍先满足唯一调用、无额外输出、参数位置与门控判据，首次候选不被后续修复替换。

`factorial_contrasts` 分别记录 C−A、D−B 的关系增量和 B−A、D−C 的用途绑定增量，保留通过方向与配对耗时。文字等价和需求方向变化的离线测试验证独立真值表组织，不能冒充真实模型的语言不变性测试。模型、推理参数和构造范例保持预设值，不加入专用 CMP 生成提示。

每次结果及时写入 JSONL；保留失败、重试、超时、缺失 usage 及全部分组结果。记录脚本版本、案例身份、模型/端点、实际请求参数、环境和执行日期。完成后产生 `.summary.json`；仓库报告只保留可公开的合成案例与汇总结果。

## 确认效果与 Core 参数绑定对照

[agent_b_instruction_effect_cases.jsonl](../../benchmarks/agent_b_instruction_effect_cases.jsonl)
包含 67 个已核对形式的 97 个合成操作意图案例，其中保留 15 个非脉冲形式的 30 个 `challenge-` 难例。规格给类型、效果目标、使能、触发和必要状态，完整原生操作数只在独立 `evaluation` 中出现。两组使用同一规格、手册证据、范例和保存的模型参数：`native_parameters` 让模型填原生参数，`core_binding` 让模型输出固定字面标记 `OP <id>`。两组随后都经过产品实际的显式 Core 阶段：原生返回也可依据已确认效果、已核对定义及相同读值重绑。产品没有实验开关。

`preflight_binding` 检查实际最终请求仅在绑定说明上不同。`first_candidate.Core_binding`
保存首次候选的绑定回执；`first_candidate.raw_model_candidate` 单独评价绑定前的模型输出，修正后的通过不计为模型原始答对。`native_parameter_rebindings` 统计实际原生参数修正；`binding_contrasts` 区分 `operation_reference` 和 `native_read_parameter_rebinding`，保留同案例、同重复的成功方向及耗时。没有操作意图的控制案例两组最终请求应完全相同。

`effect_traces` 给出 277 条独立固定的输入状态、是否使能及预期写入；评估复用 Core 参考原语核对实际调用，不由生产定义生成预期。序列轨迹逐帧检查触发、保持、重新使能及 `memory_after`。它可检出“绑定和验证共同使用错误定义”的问题，仍不证明原生执行。`raw_model_native_evaluated_runs` 是原始原生输出的评价分母，待 Core 展开的 OP 引用不计为原生失败。

`--require-reviewed-effects` 在任何真实调用前核对案例是否覆盖当前每个已核对效果形式及其独立轨迹，缺失时退出。它只约束测量输入，不改变产品支持范围。起保停对照可另加入同一私有案例输入。

```powershell
$env:PYTHONUTF8 = '1'
$resultPath = Join-Path $env:LOCALAPPDATA 'GXWorksAgent/measurements/agent-b-effects-new/live.jsonl'
python scripts/benchmark_agent_b.py benchmarks/agent_b_instruction_effect_cases.jsonl --live --arms native_parameters,core_binding --require-reviewed-effects --repeat 1 --require-endpoint https://api.deepseek.com --output $resultPath
```

`evaluation.assumed_bits` 是独立核对的位状态前提。判据仅允许在这些前提下恒真的串联触点，不忽略其他门控、相反极性或额外输出。`equivalent_operands` 逐案例列出经独立核对的等效完整调用，例如加法输入交换；不自动把所有参数交换视为等价。重评旧响应与新的真实调用分别保存，不能用重评覆盖首次实测记录。

生成请求将稳定的协议、型号能力、指令证据和范例放在前面，变化的确认规格放在后面，并稳定规格键顺序。`prompt_cache_meter` 只统计供应商实际返回的缓存 token；摘要按已报告请求的输入 token 加权，未报告或不一致的字段保持缺失。相同前缀有利于缓存复用，不能保证某次请求的命中率或耗时。

## 结果解释

`first_candidate` 单独解码第一次生成调用的首个完整 JSON；后续 JSON 或修复不能覆盖首次错误。`first_pass_semantic_correct` 统计独立预期通过数，`first_pass_usable` 还要求现有结构、IR、确认合同检查及生成流程通过。没有评估覆盖时不宣称首次语义正确。

请求计时、推理/输入/输出用量、调用次数、检索来源和程序检查分别报告。`json_complete_ms` 是首个完整候选到达时间，`end_to_end_ms` 包含交付和检查。CLI 复用预检的共同检索缓存，耗时不包含冷启动检索，需注明这一条件。`summarize` 保留分组及同案例/同重复的配对结果；供应商没有返回的用量保持缺失，不追加用量参数或改变预设参数来填补它。

结构检查、用途映射、原生接受与 PLC 运行各自标明范围。合成样本中的成功率不等于 227 个形式的生成正确率；用途覆盖审计也不等于语义正确率。少量重复的耗时差异需结合配对结果和起保停波动解释，不能据此自动接受性能收益或批量提升候选事实。

离线回放的方法见 [context-replay](../../evals/context-replay/README.md)，它测量交接和调用路径。真实模型的时间及用量结论只引用同一条件下的实际对照结果。带版本的结果放在[报告目录](../reports/README.md)。

## 复杂需求的用户路径与构造范例对照

[benchmark_user_path.py](../../scripts/benchmark_user_path.py) 使用 Web/MCP 共用的
`WorkbenchService`，依次创建工程、提交 Design 分析、补齐需求、保存审核后的规格、
首次生成并审阅版本。它不启动浏览器或原生 PLC，不在 runner 实现生成或检索规则。
[公开合成案例](../../benchmarks/user_path_complex_cases.json) 包含双泵轮换、批次灌装、
节距分拣和时间窗口记录；每例的独立验收要求在模型调用前固定。

选择已保存的 ModelScope profile；脚本核对其端点为
`https://api-inference.modelscope.cn/v1`，只在当前进程选择该 profile，不修改活动模型、
调优参数或供应商默认值。`analysis`、`clarification`、`generation`、`review` 不带
`--live` 时只打印计划；`preflight` 不发送模型请求。

```powershell
$env:PYTHONUTF8 = '1'
$resultPath = Join-Path $env:LOCALAPPDATA 'GXWorksAgent/measurements/complex-user-path-new'
$profileId = '<已保存的 ModelScope profile id>'
python scripts/benchmark_user_path.py --output $resultPath --profile-id $profileId --phase analysis --live
python scripts/benchmark_user_path.py --output $resultPath --profile-id $profileId --phase clarification --live
```

确认阶段读取 `$resultPath/reviewed_drafts/<case_id>.json`，格式为
`{"spec": <审核后的规格>, "expected_hash": <当前规格身份或null>, "review": <审核记录>}`。
操作者先选择方案，核对完整 I/O 与输入电平，回答未决问题，再提交 `--phase confirm`。
同时检查 `io_bindings` 的明确电平是否实际保存；`io_table` 只有地址与用途，用户原文含电平
也不代表最终派生输入谓词非空。缺失绑定的旧规格仍可生成，但需在对照报告中注明这一条件。
答案、方案和 I/O 修改需保留审核来源，不能把完整预期程序或原生操作数列表填进规格。
`set_spec` 的有效结果才进入测量，后续生成拒绝规格变化。

```powershell
python scripts/benchmark_user_path.py --output $resultPath --profile-id $profileId --phase confirm
python scripts/benchmark_user_path.py --output $resultPath --profile-id $profileId --phase preflight
python scripts/benchmark_user_path.py --output $resultPath --profile-id $profileId --phase generation --repeats 2 --seed 41004 --live
python scripts/benchmark_user_path.py --output $resultPath --profile-id $profileId --phase review --review-depth basic --live
```

`preflight` 检查最终供应商请求：只去除既有的构造范例块后，两组必须完全相同，
包含规格、证据、模型参数与消息。`example_contrast_delivered=false` 表示路由器没有交付
范例，这类案例只能作为相同请求的随机波动对照。不能仅凭 `examples_on` 名称计算范例收益。
生成按 `(case_id, repeat)` 随机排列组块，两组在块内随机顺序；每次使用同一确认规格，
不把前一个候选作为后一组的输入，不追加测量专用重试。

`routed_construction/4` 对明确选定的位状态／寄存器状态流程优先路由相应的计时流程范例，
而不是被同时存在的边沿或计数需求覆盖。范例保留连续定时使能、状态交接与停止优先；
位状态范例将同时执行的转换动作放在共同条件之后。此包的手册事实仍限定 FX3U。
每次改变范例或证据打包后，另存新的最终请求预检与实测，不能覆盖旧试验。

需要测量模型自身知识时，在 `preflight` 和 `generation` 两阶段加 `--include-no-rag`，
增加第三组 `no_rag`。该组跳过检索和 SQLite 别名规划，强制关闭构造范例；预检直接检查
最终请求没有手册块、用途／指令事实、历史压缩或自动操作绑定提示，且与 `examples_off`
只差实际检索正文及证据可用标记。三组共享确认规格、输出协议及保存的模型参数。
这是从确认规格开始的生成实验；之前的分析与用户选定方案仍属于共同输入。
应用后处理和校验继续共用，审阅必须同时保留模型原始首次候选，避免把本地修正算成模型正确。
`no_rag` 与 `examples_off` 比较 RAG 增量，`examples_off` 与 `examples_on` 比较范例增量。
该开关只属于实验脚本，不是产品配置；原有默认两组不变。

`review-depth basic` 通过原有本地审阅流程，模型调用为零；`deep` 另调用所选模型的既有
深查流程。审阅结果不修改测量候选，首次失败、原始输出、交付版本和后续复评分别保存。
深查不再注入旧的 120 秒模型请求超时，按模型能力优先流式接收，沿用保存的模型参数。
一个阶段失败时保留已完成阶段的证据并标为部分完成；不能把作业结束当作完整审阅通过。
复杂控制的独立审阅需检查扫描顺序、故障与复位、重入、持续电平和占用区域；结构合法及
合同特征通过不能替代完整流程正确性。用量缺失保持缺失，耗时波动与失败同时报告。

## ModelScope 手册证据诊断

`benchmark_user_path.py --experiment evidence-value` 固定使用保存的
`deepseek-ai/DeepSeek-V4.1-Flash` 与 ModelScope 端点，三组均关闭构造范例，
保留相同 Core 绑定和校验。旧的 `no_rag` 消融及范例对照含义不变。

| 组 | 标识 | 手册交付 |
| --- | --- | --- |
| A | `no_manual` | 空手册上下文，保留共同 Core 事实及绑定 |
| B | `current_rag` | 当前 knowledge builder 的实际结果 |
| C | `curated_evidence` | Codex依据原始手册复核的必要片段，含型号、修订、页码与适用条件 |

[12例公开案例](../../benchmarks/user_path_evidence_cases.json)包含普通需求、型号／边界
各四例，并引用原有四个复杂案例；默认每组三轮，共108个首次生成作业。
确认规格和已选方案共用，不传递前一个程序、不预填完整指令调用。
这仅评价确认规格后的生成阶段，不推论需求分析阶段的检索价值。

C包保存到仓库外，按 `case_id` 索引；`curated_evidence_context` 检查来源、正页码、
CPU范围、`required_fact_ids` 完整性和 `conditions` 是否逐字存在于片段内，拒绝预填调用、
程序及虚假的人类审签。`review.performed_by=Codex`、`human_signoff=false`；
此审核不修改知识库或事实验证等级。表格符号的视觉转录需在私有来源记录说明。
FX5U的C资料可补齐官方手册，B继续使用当前知识库。

```powershell
$resultPath = Join-Path $env:LOCALAPPDATA 'GXWorksAgent/measurements/evidence-value-new'
$profileId = '<已保存的 ModelScope profile id>'
python scripts/benchmark_user_path.py --experiment evidence-value --phase generation --output $resultPath --profile-id $profileId
python scripts/benchmark_user_path.py --experiment evidence-value --phase prepare --output $resultPath --profile-id $profileId --curated-evidence <私有C包.json> --confirmed-specs <共享复杂规格目录>
python scripts/benchmark_user_path.py --experiment evidence-value --phase generation --output $resultPath --profile-id $profileId --live
```

首条只查看计划，不加载凭据或联网。`prepare` 经 `set_spec` 验证后，用同一Context Compiler
编译36份实际供应商请求，要求除证据及存在标记外完全一致、无范例／历史，C每个必要块完整交付。
代码和实验材料保存私有字节快照；实际生成请求在发送前与冻结请求比较，漂移即拒绝。
`--workers N --worker-index I` 支持独立进程分区，每个案例／轮次的三组仍随机相邻串行执行。
首次运行固定分区数；续跑只执行既无结果也无开始标记的作业。已开始但无结果的作业保持未定，
不会隐式重试；结果已写但匿名包未写完时仅补匿名导出。

`blind/<随机ID>.json` 交付共同规格、验收条目、首次与最终完整程序，不交付arm、证据或作业编号。
逐项静态审阅后将同ID结果写入 `reviews/`，再运行 `--phase summary` 解盲统计。
审阅记录必须含 `performed_by=Codex`、`human_signoff=false`、`native_execution=not_measured`、
方法与时间；`first`、`final` 各包含全部冻结验收ID，状态为 `passed/failed/unverified`，
每项保留具体梯级／触发条件证据。任一关键项失败或未验证不能计全项通过。
原始输出自身若透露来源，应在审阅限制中保留这一情况；匿名文件名不保证绝对盲法。
Core结构／合同检查是辅助，原始模型候选及共同Core处理后的首次候选均保留，本轮不做原生仿真。
首次可用率评价第一次解码并经共同Core绑定的候选；原始响应另外保存，不能将此指标改称模型原始输出正确率。
Workbench会将被拒候选保存为诊断版本，保存作业可能显示 `completed`；统计还必须识别
`saved_invalid`、`diagnostic_only` 和拒绝校验状态。诊断程序可逐项审阅，但不能计为正式交付。
作业完成、正式交付、静态全项通过分别报告，缺少交付依据时保持未验证。

汇总分别统计B、C相对A的救回、弄错、共同通过、共同失败与无法判定，按类别和案例展开。
通过率使用计划作业为分母，保留缺失与未完成。实际用量、推理与缓存未返回则保持缺失，
不以字符估算冒充实际token。配对耗时包含失败；修复及调用数由实际记录计算。
此实验的实时worker不复用预检进程的检索缓存：每个worker第一次检索包含其本地冷启动，
后续请求沿用原应用缓存。该耗时条件区别于前面的Agent B独立CLI缓存回放。
探索性配对符号翻转检验以案例的三轮通过率差为单位，不把重复轮次当成独立需求；
无法判定的案例不进入该检验，并保留排除名单。类别不按未知使用频率加权。
结论只适用于本次ModelScope提供的DeepSeek V4.1、保存参数和受测需求，不推广到其他模型、
服务商或RAG的普遍价值。仓库保存公开案例、测试与结果摘要，原始材料留在私有目录。

## Core构造对照

`--experiment construction-value`复用同一runner，保留原有实验含义。公开输入在
[构造流程变体](../../benchmarks/user_path_construction_cases.json)：普通、型号边界、
复杂组合各四例，默认两组各三轮，共72个首次生成作业。

| 组 | 标识 | 构造交付 |
| --- | --- | --- |
| 构造说明 | `construction_description` | 相同已确认关系与方法，由模型输出普通梯级 |
| Core实例化 | `core_instantiation` | 模型引用实例ID，由Core展开并隔离初始化写入 |

两组共用冻结规格、构造选择、知识证据、Core校验和保存的ModelScope参数，关闭参考范例，
不传递上一份程序。预检经实际Context Compiler检查最终请求只差完整构造交付块及引用
协议，并经过真实规格正向投影解码最小候选，检查运行时CPU、资源与展开边界。
预检比较完整关系值、计划及方法说明块，不只查找实例ID；已选择且可用的方法不能在
最终请求中被标成缺事实。说明块、解码和有界检查均须保留确切CPU身份。
生成前冻结源文件及材料，实际发送前再比对请求。用独立进程分区随机相邻的成对作业，
续跑只执行未开始任务；失败、开始后中断和未知计量保留，不能隐式重试。

```powershell
$resultPath = Join-Path $env:LOCALAPPDATA 'GXWorksAgent/measurements/construction-value-new'
$profileId = '<已保存的 ModelScope profile id>'
python scripts/benchmark_user_path.py --experiment construction-value --phase preflight --output $resultPath --profile-id $profileId
python scripts/benchmark_user_path.py --experiment construction-value --phase generation --output $resultPath --profile-id $profileId --live
python scripts/benchmark_user_path.py --experiment construction-value --phase summary --output $resultPath --profile-id $profileId
```

沿用匿名逐项完整程序审阅格式，在所有已有结果完成审阅后才解盲。分别评价首次Core处理
候选与最终交付，保留原始响应；构造机制是实验差异，因此Core展开的收益不能叫作模型
原始输出正确率。局部反例、资源／引用冲突和完整工艺失败分别记录，自动检查未发现
反例也不能代替全部关键项通过。先审阅再统计救回／弄错、首次可用率、最终全项通过率、
确认关系违反、有限覆盖、耗时、调用和实际用量／缓存；重复轮次只属于12个需求簇。

旧108份候选可用`scripts.benchmark_construction.replay_frozen_candidates`只读回放，传入
独立编写的行为与时间轨迹，输出另一个私有目录；旧程序、审阅和原报告保持冻结。
这属于后验局部检查，不能把新增要求追溯算入旧实验完整通过率。

需要修正检查器后复核构造候选时，先在新的私有目录调用`freeze_code`冻结当前代码，再用
`recheck_frozen_construction_candidates`读取原材料。执行反例与构造引用检查分别保存；
不调用模型、不改原程序，也不把复核结果替换成原始交付观察。若最终模型请求本身漏交付
必要方法，则原批次不能作为有效收益对照，需保留失败记录并重新冻结实验。

### 官方接口补跑

仅在明确授权后，`--supplement-from <原有效批次目录>`使用保存的官方
`https://api.deepseek.com`、`deepseek-flash`预设补齐传输失败或未完成作业。
已经返回程序的作业保留，即使它只保存为诊断版本或语义审阅失败，也不重新生成。
旧的实验入口仍使用ModelScope；补跑只属于测量脚本。

```powershell
$sourcePath = Join-Path $env:LOCALAPPDATA 'GXWorksAgent/measurements/<原有效批次>'
$resultPath = Join-Path $env:LOCALAPPDATA 'GXWorksAgent/measurements/<单独补跑目录>'
$officialProfileId = '<已保存的官方 DeepSeek profile id>'
python scripts/benchmark_user_path.py --experiment construction-value --phase prepare --output $resultPath --supplement-from $sourcePath --profile-id $officialProfileId
python scripts/benchmark_user_path.py --experiment construction-value --phase generation --output $resultPath --supplement-from $sourcePath --profile-id $officialProfileId --live
python scripts/benchmark_user_path.py --experiment construction-value --phase summary --output $resultPath --profile-id $officialProfileId
```

补跑目录必须独立于原目录；原结果、开始标记和已有审阅逐字节冻结，不覆盖或删除。
原已开始但无结果的作业只有在这次明确授权的补跑清单中才重新发起，原请求是否完成
仍保持未知。补跑自身的开始标记继续阻止隐式重试。额度不足、鉴权或权限失败写入共享
停发记录，其他worker停止发起新任务；已经在途的任务仍保留实际结果。

预检要求产品代码及冻结材料不变，允许测量适配器更新。两个端点的保存参数须一致；
补跑最终请求只允许模型标识变化。官方适配器自动增加的`json_object`和
`thinking=enabled`在实验传输层省略，沿用原请求未显式指定这些参数的方式。
该处理不修改用户预设或产品Provider；未指定的服务端默认行为不能据此认定相同。
任何其他参数、正文或证据差异均在联网前拒绝，实际请求发送前再逐项比对。

完成匿名审阅后，汇总按“原已完成程序优先，否则使用补跑记录”选择每个作业的主结果。
原失败调用另计；额度失败不能算成模型语义错误。合并质量统计保留接口身份，耗时、
用量及缓存分别按接口记录。跨接口配对只做描述统计，不进入构造收益检验；包含这种
配对或无法判定轮次的案例不进入案例级检验，并公开排除名单。混合接口实验的结论仅
适用于该批次实际运行条件，不替换原ModelScope实验的结论边界。

20份ModelScope结果与52项官方补跑的实际计数、检查器纠错及统计边界见
[2026-10-05实验报告](../reports/2026-10-05-core-construction-value.md)。
