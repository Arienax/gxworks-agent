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

规范入口在证据构建后调用 [instruction_compiler](../../src/knowledge/instruction_compiler.py)，将现有 Registry 与官方目录的联合清单编译为 [instruction_definitions.json.gz](../instructions/mitsubishi/instruction_definitions.json.gz)。文件名保留兼容性，型号范围由 `sources.json` 和 Registry 提供。仅重建语义产物时，可复用现有 SQLite。配置了 `instruction_layout.model_badges` 的手册还会从本地 PDF 读取型号标注的几何信息，需要 `pdfplumber`；缺少 PDF 或该依赖时，构建回执明确记录未读取，不能据此认定型号适用：

```powershell
python tools/build_fx3u_knowledge.py --compile-definitions-only --source-dir <本地完整手册目录> --definitions-report <私有报告路径>
```

`instruction_behavior_facts.json` 保存逐事实核对的形式化数据；它不是整条指令的核验结论。产物使用公共定义、集中型号 profile、形式 diff 和型号 diff，保持展开前后的具体型号、形式、来源和状态相等。完整章节、明确引用、未解释内容以及型号扩展规则见[指令事实交付](../../docs/architecture/instruction-fact-delivery.md)。

不指定 `--source-dir` 时，语义阶段只使用已索引的原文与结构，不能复现额外的 PDF 型号标注。需要逐字节复现含标注的产物时，使用同一来源配置、本地手册版本和 PDF 解析环境。

结构化官方表、支持性正文、设备实体清洗、编码及分块规则分别由构建器和导入器拥有。调整规则时保留原文、来源版本和失败样本；运行对应回归，不用针对单个地址的补丁代替通用规则。

指令主记录按明确的目录标题及祖先章节归属，原生参数表与 ST 签名分别绑定。设备表不新增操作数；缺失符号、竞争定义和冲突值保持未知。构建器不再把派生摘要写回原文 chunk，运行时从结构化记录交付摘要。

旧库的结构化索引可在独立副本中重建，无需重新解析 PDF，也不修改原文、表格、FTS 或向量。该入口要求新的输出路径，拒绝覆盖来源库和现有输出；生成的参数行是抽取候选，不自动提升核验状态：

```powershell
python tools/build_fx3u_knowledge.py --refresh-instruction-index-only --index <原始SQLite> --output <新的私有SQLite副本> --index-report <私有回执.json>
```

已发布 signature／operand 核验记录绑定原始提取快照。检查新索引时保留这些快照，不直接用重建结果改写历史核验输入。独立副本与运行中证据库的替换是单独的资料迁移，不能把索引抽取成功当作完成该迁移。

## 检索和评价

运行时唯一公共入口为 [knowledge.retriever](../../src/knowledge/retriever.py)。任务/source scope、预算与指令证据传递分别见[运行时所有权](../../docs/architecture/runtime-ownership.md)和[事实交付](../../docs/architecture/instruction-fact-delivery.md)，这里不另建检索规则表。

[evaluate_rag_benchmark.py](../../tools/evaluate_rag_benchmark.py)定义评价参数和 manifest 更新条件。固定相同题集、来源版本及指标进行比较：

```powershell
python tools/evaluate_rag_benchmark.py --benchmark benchmarks/gxw2_skill_rag_benchmark.jsonl --output benchmarks/gxw2_skill_rag_benchmark_report.json --fail-under-recall-10 0.90
python tools/evaluate_rag_benchmark.py --benchmark benchmarks/fx3u_rag_benchmark_pre_skill.jsonl --output benchmarks/fx3u_rag_benchmark_pre_skill_report.json --fail-under-recall-10 0.98
```

这些命令重写指定评估输出，执行前保留已有结果。正式基准更新发布指标的规则由评估脚本定义；专项报告保持独立。历史分层诊断见[过程记录](../../docs/process/gxw2-skill-routing.md)。
