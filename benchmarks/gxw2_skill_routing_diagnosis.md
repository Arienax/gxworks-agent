# GX Works2 supporting RAG 修复与验证

本报告记录 `feat/gxw2-skill-rag` 在本地完成路由修复时的诊断快照；该阶段没有新建分支、提交、推送或合并。基线代码为 `7352c33`，开始时已有的数据、dense 和发布元数据改动已留存快照并保留。

## 结果

| 阶段 | Recall@1 | Recall@5 | Recall@10 | MRR | negative_accuracy |
|---|---:|---:|---:|---:|---:|
| 专项：修复前 | 0.4000 | 0.7000 | 0.7500 | 0.5222 | —（无负例） |
| 专项：修复后 | 0.4000 | 0.9500 | 1.0000 | 0.6472 | —（无负例） |
| 固定官方：修复前 | 0.8725 | 0.9804 | 0.9951 | 0.9080 | 1.0000 |
| 固定官方：仅修 skill 后 | 0.8725 | 0.9804 | 0.9951 | 0.9080 | 1.0000 |
| 固定官方：motion 修复后 | 0.8725 | 0.9804 | 1.0000 | 0.9086 | 1.0000 |

专项 20/20 正例通过，Top5 19/20，Top1 8/20。官方固定基准 204/204 正例通过，16/16 负例保持为空。专项 Recall@1 仍为 0.40，未达到期望中的 0.50；满足召回优先的停止条件后，没有继续调权重。

## 修改文件与设计理由

| 文件 | 修改及理由 |
|---|---|
| `src/gxw2_skill_concepts.py`（新增） | tuner/runtime/reranker 共用一份概念定义。弱词采用有主题和 PLC/ST 上下文约束的 `GXW2_*` entity route；`__NEW`/`__DELETE` 保留完整标识符；区分 SR/RS 功能块和串口 RS 指令，区分 CASE 命名标签与 POU 命名。 |
| `tools/tune_gxw2_skill_ranking.py` | 从 importer 原始 `entities_json` 恢复 native entities，再重建 derived layer。兼容旧 schema 时使用历史词表及非派生索引恢复；清理覆盖所有目标 source chunks，包括已经失去 route 的块。弱概念只写有命名空间的 entity_index，不镜像进 FTS，避免 FTS 拆词重引污染。 |
| `src/knowledge_retriever_core.py` | 精确概念接入现有 entity pipeline，原 query、FTS 输入和 dense 输入不变。独立新增方向输出分配问题到官方 Output Numbers 章节的窄路由，复用原 manual_section 基础分 1540。 |
| `src/knowledge_retriever.py` | 复用共享概念解析，按任务决定扩展；Top40 以数量约束，字符预算延后到 rerank 后最终选择，避免正确规则被长手册段落挤掉。 |
| `src/knowledge_retriever_phase2c.py` | 所有原 boost 数值保持不变。如果精确概念命中的支持材料仍不在 Top5，最多将一条放到第 2 位；保留首条结果，记录 `gxw2_supporting_slot`，不伪造更高 score。 |
| `tests/test_gxw2_skill_ranking.py` | 覆盖旧数据迁移、route 删除后的彻底清理、native/derived 同词保留、幂等性及官方来源拒绝。 |
| `tests/test_gxw2_skill_reranker.py` | 覆盖普通软件请求隔离、上下文及 task scope、完整 __ 标识符、SR/RS 歧义、支持材料位置约束和延后的字符预算。 |
| `tests/test_fx3u_rag.py` | 加入中英文方向输出问题及高速适配器场景的真实索引回归检查，确保官方证据包含硬件区别。 |
| `tools/trace_rag_candidates.py`（新增） | 只读诊断 CLI，输出 entity/FTS/dense refs、完整 scorer 排名、Top40、rerank 后排名和最终 Top10；可指定 case 与候选预算。 |
| `resources/knowledge/fx3u_knowledge.sqlite` | 重新运行 tuner，重建派生路由和 FTS。当前 59 条路由、34 个概念、21 个关联块。 |
| `benchmarks/gxw2_skill_rag_benchmark_report.json`、`benchmarks/fx3u_rag_benchmark_pre_skill_report.json` | 运行原专项和固定官方 JSONL 后更新评估报告，不修改用例或期望。 |
| `benchmarks/gxw2_skill_candidate_trace_{before,after}.json`、`benchmarks/motion_direction_candidate_trace_{before,after}.json`（新增） | 保留修复前后可核查的逐层证据。 |
| `benchmarks/gxw2_skill_routing_validation.json`、本文档（新增） | 记录 20 个专项结果、数据库一致性审计、测试结果和根因说明。 |

## 原 5 个失败的精确分类

没有发现错误的 benchmark chunk_type 期望，也没有 task scope 不匹配。4 个是 B（排序失败），另 1 个是 A（传入 reranker 的候选池缺失），但它的 entity 召回本身成功。

| case | 对应正确块 | 原 scorer 排名 | 原 Top40 / rerank 后 | 修复后 Top40 / rerank 后 / 最终排名 | 分类与根因 |
|---|---|---:|---|---|---|
| skill_st_002 | 3296 / Comment Style | 13 | 13 / 13 | 11 / 2 / 2 | B：STRUCTURED/TEXT 是环境词，却触发了官方编辑器章节的高分 manual_section；评论规则 entity 成功，FTS 原始召回但 coverage gate 拒绝，dense 未命中。原 +320 仍不足以进 Top10。 |
| skill_st_004 | 3302 / CASE Statement | 17 | 17 / 11 | 17 / 2 / 2 | B：CASE entity 与 dense 都命中，FTS coverage 被拒绝；大量官方泛章节排在前面，+320 仅从第 17 位推到第 11 位。 |
| skill_st_007 | 3295 / FB/FUN/PRG POU and File Naming | 12 | 12 / 11 | 12 / 2 / 2 | B：FB/FUN/PROGRAM 已召回正确命名块，dense 也命中；原 +280 后仍为第 11 位。PROGRAM 裸词还污染普通软件请求。 |
| skill_compat_001 | 3313 / Feature Matrix | 12 | 12 / 13 | 11 / 2 / 2 | B：Feature Matrix 通过 STRING entity 与 dense 召回，FTS Top200 未包含它；STRUCTURED/TEXT 错把注释规则一起引入，其 st_rule 加分又高于 compatibility，矩阵从第 12 位落到第 13 位。 |
| skill_st_008 | 3291 / Mandatory Constraints | 36 | 无 / 无 | 36 / 2 / 2 | A：SR entity 已召回 3291/3292，原 scorer 排名为 36/37；160000 字符预算在 rerank 前消耗完，实际候选仅 33 条。RS 的 527 条索引记录受每词 64 条限制，未单独召回这两个规则；FTS 原始命中被 coverage gate 拒绝，dense 未命中。主因是预算截断，不是三层都漏召回。 |

以上排名均从运行时真实结果取出；原 Top40 使用原来的 160000 字符预算，修复后的 Top40 在最终选择前不截断字符。基准匹配本身较宽（来源及 chunk_type），表中额外选取了实际对应主题的块进行核查。

## 原失败用例的 Top10 与各层证据

下表列出修复前最终 Top10；完整 entity refs、FTS refs、dense refs、core Top40、phase2c reranked 及对应正确块信息保存在 `gxw2_skill_candidate_trace_before.json`。修复后数据见同名 `after` 文件。

### skill_st_002

GX Works2 FX3U Structured Text 注释应该使用 // 还是 (* ... *)？

category=st_rule；task_type=st；expected chunk_type=st_rule；expected manual_id=gxw2_skill_1_6_1。

正确块 3296：entity=命中，FTS raw=命中，FTS quality=0.0，dense=未命中，scope=匹配。

| rank | chunk id | actual chunk_type | actual manual_id | matched_entity | retrieval_signals | score | gxw2_supporting_boost |
|---:|---|---|---|---|---|---:|---:|
| 1 | 2985 | section | gxworks2_structured_ag | 1.3.2 List of functions for editing in Structured Text | manual_section, vector | 2331.8 | 0 |
| 2 | 3091 | section | gxworks2_structured_ag | 10.4.8 Using function blocks and inline structured text created in other programming languages | manual_section, vector | 2292.1077 | 0 |
| 3 | 2983 | section | gxworks2_structured_ag | 1.2 Features of Structured Project | manual_section, vector | 2286.8 | 0 |
| 4 | 3044 | section | gxworks2_structured_ag | 7.1.5 Switching text color on an ST editor | manual_section, vector | 2237.6571 | 0 |
| 5 | 2984 | section | gxworks2_structured_ag | 1.3.1 List of functions common to Simple project and Structured project | manual_section, vector | 2200.8 | 0 |
| 6 | 3045 | section | gxworks2_structured_ag | 8 EDITING STRUCTURED LADDER/FBD PROGRAMS | manual_section, vector | 2186.8 | 0 |
| 7 | 2993 | section | gxworks2_structured_ag | 4.1 Program Configurations of Structured Project | manual_section, vector | 2158.1333 | 0 |
| 8 | 3112 | section | gxworks2_structured_ag | 12.5 Monitoring Programs in Structured Ladder/FBD Editor | manual_section, vector | 2137.6571 | 0 |
| 9 | 2987 | section | gxworks2_structured_ag | 1.3.3 List of functions for editing in Structured Ladder/FBD | manual_section, vector | 2100.8 | 0 |
| 10 | 2986 | section | gxworks2_structured_ag | 1.3.3 List of functions for editing in Structured Ladder/FBD | manual_section | 1835.8 | 0 |

### skill_st_004

GX Works2 ST 的 CASE 能不能写 1..5 这样的范围标签或者命名状态标签？

category=st_rule；task_type=st；expected chunk_type=st_rule；expected manual_id=gxw2_skill_1_6_1。

正确块 3302：entity=命中，FTS raw=命中，FTS quality=0.0，dense=命中，scope=匹配。

| rank | chunk id | actual chunk_type | actual manual_id | matched_entity | retrieval_signals | score | gxw2_supporting_boost |
|---:|---|---|---|---|---|---:|---:|
| 1 | 2982 | section | gxworks2_structured_ag | 1 OVERVIEW | manual_section, vector | 2299.8 | 0 |
| 2 | 3005 | section | gxworks2_structured_ag | 5 SETTING LABELS | manual_section, vector | 2156.4667 | 0 |
| 3 | 3018 | section | gxworks2_structured_ag | 5.6.1 Setting structures | manual_section, vector | 2084.8 | 0 |
| 4 | 3048 | section | gxworks2_structured_ag | 8.2.1 Entering elements | manual_section, vector | 2084.8 | 0 |
| 5 | 3030 | section | gxworks2_structured_ag | 6.1.1 Available programming languages | manual_section, vector | 2041.8 | 0 |
| 6 | 3035 | section | gxworks2_structured_ag | 6.2.5 Splitting editing screen | manual_section, vector | 2041.8 | 0 |
| 7 | 3047 | section | gxworks2_structured_ag | 8.1.1 Selecting editing modes | manual_section, vector | 2041.8 | 0 |
| 8 | 3073 | section | gxworks2_structured_ag | 8.10.1 Overwrite mode and insert mode | manual_section, vector | 2041.8 | 0 |
| 9 | 3100 | section | gxworks2_structured_ag | 10.6.1 Correcting errors and warnings | manual_section, vector | 2041.8 | 0 |
| 10 | 2998 | section | gxworks2_structured_ag | 4.3.1 Procedure for creating POUs | manual_section, vector | 2013.1333 | 0 |

### skill_st_007

GX Works2 项目里的 FB、FUN、Program 文件名和 FB 实例名应该采用什么命名规则？

category=st_rule；task_type=generate；expected chunk_type=st_rule；expected manual_id=gxw2_skill_1_6_1。

正确块 3295：entity=命中，FTS raw=命中，FTS quality=0.0，dense=命中，scope=匹配。

| rank | chunk id | actual chunk_type | actual manual_id | matched_entity | retrieval_signals | score | gxw2_supporting_boost |
|---:|---|---|---|---|---|---:|---:|
| 1 | 2993 | section | gxworks2_structured_ag | 4.1 Program Configurations of Structured Project | manual_section, vector | 2358.1333 | 0 |
| 2 | 2995 | section | gxworks2_structured_ag | 4.2.2 Registering program blocks to tasks | manual_section, vector | 2337.6571 | 0 |
| 3 | 3011 | section | gxworks2_structured_ag | 5.3 Setting Local Labels for Program Blocks | manual_section, vector | 2337.6571 | 0 |
| 4 | 3033 | section | gxworks2_structured_ag | 6.2.2 Using labels in the program | manual_section, vector | 2337.6571 | 0 |
| 5 | 3137 | section | gxworks2_structured_ag | Appendix 1.3 Toolbar icons and shortcut keys for program editors | manual_section, vector | 2310.3556 | 0 |
| 6 | 2992 | section | gxworks2_structured_ag | 4 PROGRAM CONFIGURATIONS | manual_section, vector | 2301.4667 | 0 |
| 7 | 3038 | section | gxworks2_structured_ag | 6.2.9 Opening label setting and program screens for selected POU | manual_section, vector | 2300.8 | 0 |
| 8 | 3029 | section | gxworks2_structured_ag | 6.1 Types of Program Editor | manual_section, vector | 2286.8 | 0 |
| 9 | 2994 | section | gxworks2_structured_ag | 4.2.1 Procedure for creating program files and tasks | manual_section, vector | 2222.3 | 0 |
| 10 | 3028 | section | gxworks2_structured_ag | 6 COMMON OPERATIONS OF PROGRAM EDITORS | manual_section, vector | 2158.1333 | 0 |

### skill_compat_001

FX3G 在 GX Works2 Structured Text 中支持 STRING 和字符串函数吗？

category=compatibility；task_type=st；expected chunk_type=compatibility；expected manual_id=gxw2_skill_1_6_1。

正确块 3313：entity=命中，FTS raw=未命中，FTS quality=0.0，dense=命中，scope=匹配。

| rank | chunk id | actual chunk_type | actual manual_id | matched_entity | retrieval_signals | score | gxw2_supporting_boost |
|---:|---|---|---|---|---|---:|---:|
| 1 | 2985 | section | gxworks2_structured_ag | 1.3.2 List of functions for editing in Structured Text | manual_section, vector | 2431.8 | 0 |
| 2 | 3091 | section | gxworks2_structured_ag | 10.4.8 Using function blocks and inline structured text created in other programming languages | manual_section, vector | 2292.1077 | 0 |
| 3 | 2983 | section | gxworks2_structured_ag | 1.2 Features of Structured Project | manual_section, vector | 2286.8 | 0 |
| 4 | 3044 | section | gxworks2_structured_ag | 7.1.5 Switching text color on an ST editor | manual_section, vector | 2237.6571 | 0 |
| 5 | 2984 | section | gxworks2_structured_ag | 1.3.1 List of functions common to Simple project and Structured project | manual_section, vector | 2200.8 | 0 |
| 6 | 3045 | section | gxworks2_structured_ag | 8 EDITING STRUCTURED LADDER/FBD PROGRAMS | manual_section, vector | 2186.8 | 0 |
| 7 | 2993 | section | gxworks2_structured_ag | 4.1 Program Configurations of Structured Project | manual_section, vector | 2158.1333 | 0 |
| 8 | 3112 | section | gxworks2_structured_ag | 12.5 Monitoring Programs in Structured Ladder/FBD Editor | manual_section, vector | 2137.6571 | 0 |
| 9 | 2987 | section | gxworks2_structured_ag | 1.3.3 List of functions for editing in Structured Ladder/FBD | manual_section, vector | 2100.8 | 0 |
| 10 | 3291 | st_rule | gxw2_skill_1_6_1 | STRUCTURED | entity, entity, vector | 1851.8 | 320.0 |

### skill_st_008

GX Works2 FX3U ST 是否支持 SR/RS 双稳态功能块？推荐替代方案是什么？

category=st_rule；task_type=st；expected chunk_type=st_rule；expected manual_id=gxw2_skill_1_6_1。

正确块 3291：entity=命中，FTS raw=命中，FTS quality=0.0，dense=未命中，scope=匹配。

| rank | chunk id | actual chunk_type | actual manual_id | matched_entity | retrieval_signals | score | gxw2_supporting_boost |
|---:|---|---|---|---|---|---:|---:|
| 1 | 538 | instruction | fx3_programming_r | RS | structured_instruction, entity | 2760.0 | 0 |
| 2 | 1886 | instruction | fxcpu_basic_applied_m | RS | structured_instruction, entity | 2755.5 | 0 |
| 3 | 4306 | skill_instruction | gxw2_skill_1_6_1 | RS | entity, vector | 1984.8 | 0 |
| 4 | 539 | instruction | fx3_programming_r | RS | entity | 1964.0 | 0 |
| 5 | 541 | instruction | fx3_programming_r | RS | entity | 1961.0 | 0 |
| 6 | 540 | instruction | fx3_programming_r | RS | entity | 1958.0 | 0 |
| 7 | 1888 | instruction | fxcpu_basic_applied_m | RS | entity | 1954.5 | 0 |
| 8 | 1887 | instruction | fxcpu_basic_applied_m | RS | entity | 1952.5 | 0 |
| 9 | 1889 | instruction | fxcpu_basic_applied_m | RS | entity | 1950.5 | 0 |
| 10 | 4304 | skill_instruction | gxw2_skill_1_6_1 | RS | entity | 1762.8 | 0 |

## 官方方向输出问题

`motion_direction_arbitrary`：正确的官方章节是 `fx3_positioning_k` / chunk 1353 / “1.5.2 Assignment of Output Numbers”，PDF 页 56–57。它同时区分主机晶体管输出和高速输出适配器的分配规则；不能把适配器的固定映射泛化到主机输出。

原 Top40 中该块排第 23（score 1788.1）。窄范围章节路由复用既有 manual_section 基础分，修复后排第 9，原首位 debug case 保留；官方 Recall@10 从 203/204 恢复为 204/204。该修复与 skill 路由独立，未改数据库正文或 benchmark 期望。

## 20 个专项用例的最终结果

| case | category | 首次合格结果排名 | chunk id | section |
|---|---|---:|---|---|
| skill_st_001 | st_rule | 1 | 3291 | Common Rules — GX Works 2 ST (Mandatory) > Mandatory Constraints |
| skill_st_002 | st_rule | 2 | 3296 | Common Rules — GX Works 2 ST (Mandatory) > Comment Style |
| skill_st_003 | st_rule | 1 | 3291 | Common Rules — GX Works 2 ST (Mandatory) > Mandatory Constraints |
| skill_st_004 | st_rule | 2 | 3302 | Common Rules — GX Works 2 ST (Mandatory) > Selection > CASE Statement |
| skill_st_005 | st_rule | 1 | 3299 | Common Rules — GX Works 2 ST (Mandatory) > Assignment Operator |
| skill_st_006 | st_rule | 1 | 3309 | Common Rules — GX Works 2 ST (Mandatory) > 3-Program Structure (Default Project Layout) |
| skill_st_007 | st_rule | 2 | 3295 | Common Rules — GX Works 2 ST (Mandatory) > Naming Conventions > FB/FUN/PRG POU and File Naming |
| skill_type_001 | data_type | 1 | 3341 | Data Types — GX Works 2 ST (FX Series) > Unsupported Types (FX Series) |
| skill_type_002 | data_type | 1 | 3343 | Data Types — GX Works 2 ST (FX Series) > Memory Consumption |
| skill_type_003 | data_type | 1 | 3339 | Data Types — GX Works 2 ST (FX Series) > Elementary Types |
| skill_type_004 | data_type | 2 | 3350 | Data Types — GX Works 2 ST (FX Series) > `_E` Postfix Pattern |
| skill_compat_001 | compatibility | 2 | 3313 | Compatibility Matrix — FX Series Models (GX Works 2) > Feature Matrix |
| skill_compat_002 | compatibility | 1 | 3314 | Compatibility Matrix — FX Series Models (GX Works 2) > Device Ranges |
| skill_compat_003 | compatibility | 2 | 3320 | Compatibility Matrix — FX Series Models (GX Works 2) > GX Works 2 vs GX Works 3 |
| skill_mov_001 | skill_instruction | 4 | 4091 | MOV — MOV / Move > ST Syntax (GX Works 2) |
| skill_mov_002 | skill_instruction | 9 | 4095 | MOV — MOV / Move > Key Rules |
| skill_mov_003 | skill_instruction | 4 | 4091 | MOV — MOV / Move > ST Syntax (GX Works 2) |
| skill_st_008 | st_rule | 2 | 3291 | Common Rules — GX Works 2 ST (Mandatory) > Mandatory Constraints |
| skill_st_009 | st_rule | 3 | 3291 | Common Rules — GX Works 2 ST (Mandatory) > Mandatory Constraints |
| skill_st_010 | st_rule | 2 | 3295 | Common Rules — GX Works 2 ST (Mandatory) > Naming Conventions > FB/FUN/PRG POU and File Naming |

## 验证与边界

- 全仓库 `python -m pytest -q -rs`：730 passed，3 skipped，0 failed。指定的 5 个测试文件均包含在此次通过的测试集中。
- 3 项跳过：缺少可选 MCP 依赖；GX Works2 没有运行；没有可读取的打开程序。本次未验证 MCP 实际集成、GX/Simulator/MX 软件链路，也未操作真实 PLC。
- SQLite integrity_check=ok。逐表双向比较确认 manuals、instructions、instruction_aliases、device_records、debug_cases、vector_embeddings、所有官方 chunks 和非 skill_concept 索引记录没有新增/删除/修改。
- 所有 chunks 的 text、text_sha256、entities_json、manual_priority 与接手时一致；dense 文件哈希一致，没有重新 build dense。第三方 structured instruction 数量仍为 0。
- entity_index 中裸 PROGRAM 派生条数=0；第三方 chunks.entities 中 PROGRAM token 数量=0。普通查询 `please revise this program`（FX3U/edit）返回 `[]`。
- `GX Works2 FX3U ST program structure`（FX3U/st）能返回 st_rule / chunk 3309。
- 固定官方及专项 benchmark JSONL 的哈希没有变化；release manifest、原 canonical benchmark meta/report、dense 文件均保留用户接手前已有改动，本次没有再次改写。自定义 evaluator 的 manifest 隔离逻辑未改。
- UTF-16 CSV importer、JSON→CSV renderer、skill_instruction 身份及第三方低优先级均保持原有设计。

## 复现命令

```powershell
python tools/tune_gxw2_skill_ranking.py
python -m pytest tests/test_gxw2_skill_import.py tests/test_gxw2_skill_ranking.py tests/test_gxw2_skill_reranker.py tests/test_fx3u_rag.py -v
python -m pytest -q -rs
python tools/evaluate_rag_benchmark.py --benchmark benchmarks/gxw2_skill_rag_benchmark.jsonl --output benchmarks/gxw2_skill_rag_benchmark_report.json --fail-under-recall-10 0.90
python tools/evaluate_rag_benchmark.py --benchmark benchmarks/fx3u_rag_benchmark_pre_skill.jsonl --output benchmarks/fx3u_rag_benchmark_pre_skill_report.json --fail-under-recall-10 0.98
python tools/trace_rag_candidates.py --case-id skill_st_002 --case-id skill_st_004 --case-id skill_st_007 --case-id skill_compat_001 --case-id skill_st_008 --output benchmarks/gxw2_skill_candidate_trace_after.json
python tools/trace_rag_candidates.py --benchmark benchmarks/fx3u_rag_benchmark_pre_skill.jsonl --case-id motion_direction_arbitrary --output benchmarks/motion_direction_candidate_trace_after.json
```

## PR #3 合并建议

建议将这批已验证的本地改动提交到 PR #3 后合并。召回目标、官方不退化约束、弱词清理和方向输出回归均满足；没有为提升 Top1 继续扩大权重。诊断阶段尚未提交或推送。后续用户授权比较远端与本地后合并，合并准备时重新运行正式 benchmark，并刷新发布 manifest 的数据库哈希、统计计数和正式评估结果；固定专项及官方用例、dense 和数据库正文不变。
