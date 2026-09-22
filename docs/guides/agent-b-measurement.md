# Agent B 对照测量

## 准备输入

使用 [benchmark_agent_b.py](../../scripts/benchmark_agent_b.py) 的 `load_cases` 读取 JSONL。每行具有唯一的 `case_id` 和 `confirmed_spec` 对象；可选字段及实验分组由该脚本的 `main`、`ARMS`、`run_case` 定义。[agent_b_smoke_cases.jsonl](../../benchmarks/agent_b_smoke_cases.jsonl)是边界测试样本，正式测量使用独立审核的工程案例。

固定确认规格、模型配置、服务端点及评估器。原生或行为预期由对应工程证据提供；`oracle_evidence` 使用人工审核的资料。调优值在[模型配置](model-settings.md)中设置，已废弃的 `--effort` 参数不改变请求。

## 查看实验计划

在仓库根目录运行：

```powershell
python scripts/benchmark_agent_b.py benchmarks/agent_b_smoke_cases.jsonl
```

不带 `--live` 时只打印计划，不加载凭据或请求模型。`--arms`、`--repeat`、`--seed` 控制对照组、重复次数和排列；通过 `--help` 查询默认值。

## 执行测量

以下命令会使用已保存的活动模型配置请求模型，产生相应费用。输出使用新的私有文件路径：

```powershell
python scripts/benchmark_agent_b.py benchmarks/agent_b_smoke_cases.jsonl --live --output agent-b-results.jsonl
```

需要领域行为评价时，以 `--evaluator module:function` 指定既有评估器。保留每次失败、重试、缺失 usage 及全部分组结果，不只统计成功输出。记录脚本版本、案例指纹、模型/端点、配置、环境和执行日期；分享前检查内容和凭据脱敏。

## 结果解释

请求计时、推理/输出用量、检索来源和程序检查分别报告。`summarize` 汇总已有观测；供应商没有返回的用量保持缺失。结构检查、领域评估与原生运行各自标明执行范围，不能互相代替。

离线回放的方法见 [context-replay](../../evals/context-replay/README.md)，它测量交接和调用路径。真实模型的时间及用量结论只引用同一条件下的实际对照结果。带版本的结果放在[报告目录](../reports/README.md)。
