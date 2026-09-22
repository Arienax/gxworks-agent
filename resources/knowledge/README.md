# 知识库维护

## 来源和产物

[sources.json](sources.json)记录官方源文件、版本、地址和 SHA-256；[external_sources.json](external_sources.json)固定第三方来源提交及其导入属性。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)保留第三方归属和使用范围。资料版本、源优先级及数量从这些文件查询，不在说明中重复维护。

[manifest.json](manifest.json)记录数据库、向量索引及构建信息。SQLite/FTS 与本地 LSA 由运行时按需读取；源资料解析和向量构建在离线构建阶段完成。[design_patterns.json](design_patterns.json)是设计参考，指令事实保留独立来源身份。

## 构建

在仓库根目录按顺序执行：

```powershell
python tools/build_fx3u_knowledge.py
python tools/import_design_patterns.py
python tools/import_gxw2_skill.py
python tools/tune_gxw2_skill_ranking.py
python tools/build_dense_embeddings.py
```

规范入口是 [build_fx3u_knowledge.py](../../tools/build_fx3u_knowledge.py)。第三方导入器 [import_gxw2_skill.py](../../tools/import_gxw2_skill.py)使用固定来源，也接受 `--source-dir` 指定本地源目录；下载只发生在需要获取来源时。重新导入语料后重建向量，核对 manifest 与最终数据库、索引的哈希及字节数。

结构化官方表、支持性正文、设备实体清洗、编码及分块规则分别由构建器和导入器拥有。调整规则时保留原文、来源版本和失败样本；运行对应回归，不用针对单个地址的补丁代替通用规则。

## 检索和评价

运行时唯一公共入口为 [knowledge.retriever](../../src/knowledge/retriever.py)。任务/source scope、预算与指令证据传递分别见[运行时所有权](../../docs/architecture/runtime-ownership.md)和[事实交付](../../docs/architecture/instruction-fact-delivery.md)，这里不另建检索规则表。

[evaluate_rag_benchmark.py](../../tools/evaluate_rag_benchmark.py)定义评价参数和 manifest 更新条件。固定相同题集、来源版本及指标进行比较：

```powershell
python tools/evaluate_rag_benchmark.py --benchmark benchmarks/gxw2_skill_rag_benchmark.jsonl --output benchmarks/gxw2_skill_rag_benchmark_report.json --fail-under-recall-10 0.90
python tools/evaluate_rag_benchmark.py --benchmark benchmarks/fx3u_rag_benchmark_pre_skill.jsonl --output benchmarks/fx3u_rag_benchmark_pre_skill_report.json --fail-under-recall-10 0.98
```

这些命令重写指定评估输出，执行前保留已有结果。正式基准更新发布指标的规则由评估脚本定义；专项报告保持独立。历史分层诊断见[过程记录](../../docs/process/gxw2-skill-routing.md)。
