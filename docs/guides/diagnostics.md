# 诊断与交互记录

## 先确认结果属于哪个版本

查看任务 ID、工程、基础版本、模型配置及当前产物。失败候选可能保留梯形图或 JSON 供排查，但只有已接受版本才是活动程序。刷新页面后读取已有任务结果，不要重新提交生成来找回输出。

“刷新结果 / 重绘梯形图”从保存的 IR 派生产物，不调用模型。旧版本报告使用它绑定的规格、测试和产物；不要用后来编辑的活动规格解释旧结果。

## 导出交互记录

任务交互导出适用于成功和失败任务。导出包保留可用的用户操作、分析与生成交互、决策回执及任务结果；缺失的阶段保持缺失，不补造响应。导出接口与字段见 [HTTP 参考](../integrations/http-api.md)，实现从 [application.jobs](../../src/application/jobs.py)及 [shared.diagnostics](../../src/shared/diagnostics.py)查询。

工程交付摘要可单独下载为 Markdown；它汇总版本、产物指纹、测试及原生验证记录。交付摘要与完整交互包用途不同，不能用摘要代替问题复现所需的原始记录。

共享导出包前检查需求文本、工程名称、模型响应、端点及附件。保留问题所需证据，移除凭据和无关私人数据；本地完整导出不应直接作为公开测试夹具。

## 离线回放

离线回放使用记录中的响应或固定夹具检查本地调用链。用下面的命令把结果写入独立 JSON，保留源 ZIP 不变：

```powershell
.\.venv\Scripts\python.exe scripts/context_replay.py --help
.\.venv\Scripts\python.exe scripts/context_replay.py --archive "D:\Diagnostics\interaction.zip" --output "D:\Diagnostics\offline-replay.json"
```

具体参数由 [scripts/context_replay.py](../../scripts/context_replay.py)定义。指定 `--output` 时生成新的独立 JSON 文件并保留源 ZIP。仅指定 `--archive` 时，结果以 `offline_replay.json` 原子写回该 ZIP，重复执行替换同名结果，保留其余成员。不带归档及输出参数时才向标准输出打印结果。夹具、断言及无网络执行说明见 [context-replay](../../evals/context-replay/README.md)。

## 检查耗时和模型输出

比较同一确认规格、模型、端点和参数设置下的请求数、首次有效输出时间、总耗时、推理及输出用量，同时检查产物是否正确。缺少供应商用量时记录缺失，不以字符数代替 token 用量。

[benchmark_agent_b.py](../../scripts/benchmark_agent_b.py)提供测量入口，先运行 `--help` 选择输入和输出。实时模型实验会发送 API 请求；离线回放检查的是编排和数据传递。实测报告应记录两者中的哪一种，并保留失败和跳过的结果。
