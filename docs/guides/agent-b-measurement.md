# Agent B 对照测量

## 准备输入

使用 [benchmark_agent_b.py](../../scripts/benchmark_agent_b.py) 的 `load_cases` 读取 JSONL。每行具有唯一的 `case_id` 和 `confirmed_spec` 对象；可选字段及实验分组由该脚本的 `main`、`ARMS`、`run_case` 定义。[agent_b_smoke_cases.jsonl](../../benchmarks/agent_b_smoke_cases.jsonl)是边界测试样本。[agent_b_operand_purpose_cases.jsonl](../../benchmarks/agent_b_operand_purpose_cases.jsonl)包含 SFTL、WSFL、BMOV、CMP、IVCK、TCMP 各两个参数变体，以及起保停对照。

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

内置 `evaluate_synthetic_case` 用独立固定预期比较指定指令的每个操作数，包括常量、地址、长度/移动量、单位和连续区首地址；十进制与等值十六进制常量可等价。起保停复用 Core 控制结构和绑定谓词检查。其他领域评价通过 `--evaluator module:function` 接入已有评估器，不在 runner 复制 PLC 规则。

每次结果及时写入 JSONL；保留失败、重试、超时、缺失 usage 及全部分组结果。记录脚本版本、案例身份、模型/端点、实际请求参数、环境和执行日期。完成后产生 `.summary.json`；仓库报告只保留可公开的合成案例与汇总结果。

## 结果解释

`first_candidate` 单独解码第一次生成调用的首个完整 JSON；后续 JSON 或修复不能覆盖首次错误。`first_pass_semantic_correct` 统计独立预期通过数，`first_pass_usable` 还要求现有结构、IR、确认合同检查及生成流程通过。没有评估覆盖时不宣称首次语义正确。

请求计时、推理/输入/输出用量、调用次数、检索来源和程序检查分别报告。`json_complete_ms` 是首个完整候选到达时间，`end_to_end_ms` 包含交付和检查。CLI 复用预检的共同检索缓存，耗时不包含冷启动检索，需注明这一条件。`summarize` 保留分组及同案例/同重复的配对结果；供应商没有返回的用量保持缺失，不追加用量参数或改变预设参数来填补它。

结构检查、用途映射、原生接受与 PLC 运行各自标明范围。合成样本中的成功率不等于 227 个形式的生成正确率；用途覆盖审计也不等于语义正确率。少量重复的耗时差异需结合配对结果和起保停波动解释，不能据此自动接受性能收益或批量提升候选事实。

离线回放的方法见 [context-replay](../../evals/context-replay/README.md)，它测量交接和调用路径。真实模型的时间及用量结论只引用同一条件下的实际对照结果。带版本的结果放在[报告目录](../reports/README.md)。
