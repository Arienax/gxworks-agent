# FX3U RAG 知识库

当前知识库使用 schema v3。运行时使用 SQLite/FTS5 与随程序打包、按需加载的
NumPy LSA 向量索引；PDF 解析、第三方 Markdown 导入和向量构建只在离线构建时使用，
不进入启动关键路径。

## 知识源

官方三菱资料：

- JY997D16601 Rev.R：FX3S/FX3G/FX3GC/FX3U/FX3UC Basic & Applied Instruction
- JY997D16801 Rev.K：FX3S/FX3G/FX3GC/FX3U/FX3UC Positioning Control
- JY997D26001 Rev.L：FXCPU Structured Programming [Device & Common]
- JY997D34701 Rev.M：FXCPU Structured Programming [Basic & Applied Instruction]
- JY997D34801 Rev.K：FXCPU Structured Programming [Application Functions]
- SH-080781ENG Rev.AG：GX Works2 Structured Project
- SH-080782ENG Rev.O：Structured Programming Fundamentals

官方源文件、地址和 SHA-256 记录在 `sources.json`。构建时会强制校验文件哈希。

当前内置的分析设计知识源：

- `design_patterns.json`：人工整理的 PLC 控制架构选择知识，仅以 `task_types=analysis` 写入 SQLite；它描述方案之间的结构差异、适用条件与取舍，不作为 PLC 型号/指令事实的权威来源。

当前打包的第三方支持知识源：

- `Serhioromano/gxw2-skill` 1.6.1：GX Works 2 / FX 系列 ST、CSV Label Editor、设备、数据类型、兼容性、指令说明和 `.iecst/.csv` 示例。

第三方源固定在 `external_sources.json` 中的具体 commit，当前检索优先级为 52，低于所有已配置的官方手册。它被定义为 `supporting` source，而不是 authoritative structured source。导入器不会把 `00_Instruction_List.md` 作为普通全文 chunk，以避免它与单指令文档重复抢占检索结果。第三方许可与归属见 `THIRD_PARTY_NOTICES.md`。

## 构建流程

```text
Mitsubishi PDF manuals                 gxw2-skill Markdown/examples
        │                                         │
        ├─ text/layout/table parsing              ├─ Markdown heading chunking
        │                                         └─ supporting entity/opcode extraction
        │
        ├─ authoritative structured stores
        │    ├─ instructions / aliases
        │    ├─ device_records
        │    ├─ error_records
        │    └─ debug_cases
        │
        └──────────────────┬──────────────────────┐
                           ↓                      │
                schema-v3 unified SQLite         │
                  ├─ chunks + FTS5  ←────────────┘
                  └─ entity_index
                           ↓
                 local dense LSA embeddings
```

主要结构化表：`instructions`、`instruction_aliases`、`device_records`、`error_records`、`debug_cases`。

`gxw2-skill` 仅作为 supporting corpus 写入 `chunks` 和 `entity_index`，并参与 FTS5 与 dense retrieval。其 `references/DB/*.md` 使用 `skill_instruction` chunk type，并保留 `instruction_opcode`，但不会写入 `instructions` / `instruction_aliases`。这样官方 Mitsubishi manuals 继续独占 structured instruction 的 authoritative boost，第三方内容只提供 ST 写法、工程模式和示例补充。

暂不写第三方 `device_records`，因为当前 `device_records` 的唯一键没有 source 维度，直接合并可能覆盖或折叠官方设备记录。

`S1/S2` 根据上下文分为 `operand_placeholder` 或 `device`；例如 `skill_instruction` 文档中的 `S1/S2` 作为 operand，而设备文档中的状态继电器地址保持为 device。

设备实体构建还会执行语义清洗：小写参数变量（例如 `2 <= n <= 512`）不会被归一化为 `N512` 设备；MC/MCR 的 `N0`～`N7` 保留为 nesting-level 语义；`D | 17 steps | DABSD` 一类 PDF 表格压平结果不会生成伪 `D17`。该清洗只处理有明确语义证据的误识别，不使用具体设备地址黑名单，因此程序示例中的 `D307`、`M599`、`P10` 等仍作为真实设备/指针实体保留。

导入器支持 UTF-8/UTF-8 BOM，以及带 BOM 的 UTF-16 LE/BE 文本，以兼容 gxw2-skill 中 GX Works2 Label CSV 示例的实际编码。

## 检索

当前运行时链路：

```text
Query understanding
  ├─ instruction/device/error/debug structured lookup
  ├─ entity lookup
  ├─ BM25/FTS5
  └─ dense vector search
          ↓
weighted reciprocal-rank fusion
          ↓
deterministic cross-signal reranker + source priority + task-aware ranking
```

其中 structured instruction lookup 只来自 authoritative structured stores。`gxw2-skill` 可通过 entity/BM25/dense 被召回，但不会获得 `structured_instruction` 的高权重。

`tune_gxw2_skill_ranking.py` 为 ST 规则、数据类型和兼容性添加精确概念路由。
弱词只在明确的 PLC/ST 上下文和对应主题下转换为带命名空间的 entity，避免裸
`PROGRAM` 等词污染普通软件查询。重复运行会先恢复 importer 原始实体，再重建派生层。
运行时在相关任务中取 Top40 候选，先 rerank，再应用最终字符预算；若精确命中的支持
材料仍不在 Top5，最多将一条放到第 2 位，保留首条结果及既有 score 权重。

当前内置 `fx3u_multilingual_lsa_v1` dense embedding，维度、语料摘要、构建时间、
产物 SHA-256 和 benchmark 指标均记录在 `manifest.json`。向量文件仅在首次实际检索时
加载，以免拖慢程序启动。

## 重建与评估

完整重建顺序：

```powershell
python tools/build_fx3u_knowledge.py
python tools/import_design_patterns.py
python tools/import_gxw2_skill.py
python tools/tune_gxw2_skill_ranking.py
python tools/build_dense_embeddings.py
python tools/evaluate_rag_benchmark.py --fail-under-recall-10 0.98
```

`build_fx3u_knowledge.py` 是规范入口；它复用 schema-v3 builder，并在写入 `entity_index` / `device_records` 前应用设备实体语义清洗。不要绕过该入口直接调用内部 `build_fx3u_knowledge_v3.py` 进行正式重建。

`import_gxw2_skill.py` 默认下载 `external_sources.json` 固定的 commit。已有本地 checkout 时可离线导入：

```powershell
python tools/import_gxw2_skill.py --source-dir C:\path\to\gxw2-skill
```

导入第三方语料后，脚本会重建 FTS5，并将已有 dense 向量标记为 `stale`；因此必须随后重新运行 `build_dense_embeddings.py`。

仅修改概念路由时，重新运行 tuner 即可；它不改正文或 text_sha256，无需重建 dense。
发布时应核对 manifest 的数据库 SHA-256、字节数及实际表计数与最终打包文件一致。

基准集位于 `benchmarks/fx3u_rag_benchmark.jsonl`，包含指令、设备、错误码、调试案例、伺服/步进定位、结构化编程和负例。评估第三方语料的回归影响时，应固定使用接入前的同一 benchmark，而不是重新生成题集后再比较。

```powershell
python tools/evaluate_rag_benchmark.py --benchmark benchmarks/gxw2_skill_rag_benchmark.jsonl --output benchmarks/gxw2_skill_rag_benchmark_report.json --fail-under-recall-10 0.90
python tools/evaluate_rag_benchmark.py --benchmark benchmarks/fx3u_rag_benchmark_pre_skill.jsonl --output benchmarks/fx3u_rag_benchmark_pre_skill_report.json --fail-under-recall-10 0.98
```

只有正式 `fx3u_rag_benchmark.jsonl` 的评估允许更新 manifest 中的发布指标；上述自定义评估保持隔离。
本次固定官方 Recall@10 为 1.0000，专项 Recall@10 为 1.0000；逐层诊断见
`benchmarks/gxw2_skill_routing_diagnosis.md`。
