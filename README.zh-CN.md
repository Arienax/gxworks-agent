# GXWorks Agent

[English](README.md) | 简体中文

> **面向 Mitsubishi MELSEC PLC 开发的 AI 原生工程工作台与 Agent Runtime。**  
> 自然语言 → 已确认控制规格 → SQLite 证据检索 + CPU 范围指令契约 → 按任务组装 Prompt → 有边界的候选处理 → PLC IR → GX Works2 / GXW → 仿真与验证证据。

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)
![PLC](https://img.shields.io/badge/demo-FX3U-orange.svg)
![Status](https://img.shields.io/badge/status-active%20development-yellow.svg)

<p align="center">
  <img src="resources/assets/demo.gif" alt="GXWorks Agent 演示" width="1200">
</p>

**GXWorks Agent** 是一个面向 Mitsubishi MELSEC PLC 开发的实验性 AI 原生工程工作台。当前架构的核心不是“把大量 PLC 文档直接塞给模型”，而是由三个层次协同工作：本地 **SQLite/FTS5 PLC 证据库**、确定性的 **instruction registry / generation contract**，以及只为当前任务注入必要工程上下文的 **Prompt 组装层**。模型生成、PLC IR、版本化工程状态、GX Works2 集成、仿真/调试工作流，以及 MCP 外部 Agent 都建立在同一套工程核心之上。

项目遵循一个核心原则：**LLM 输出本身不等于工程结果。** 模型负责理解需求并提出程序或动作；GXWorks Agent 负责工程状态、知识来源权威性、指令/软元件契约、输出 schema、结构验收、修改范围、版本管理、审批、证据、工程产物以及外部副作用。

当前实现主要聚焦 **FX3U + GX Works2**。Ladder CSV 是目前最成熟的后端；原生 **GXW / 结构化梯形图 / FBD** 仍属于基于可复现实验证据持续推进的实验性方向。

> 开发状态快照：**2026-09-16**。下文状态标记保持保守。

---

## 这个项目为什么不是普通“LLM 生成 PLC 代码”

最简单的 LLM PLC 工作流通常是：

```text
Prompt → 模型 → PLC 代码
```

GXWorks Agent 则把 **工程证据、可执行指令契约、模型上下文组装** 明确分开：

```text
自然语言需求
      ↓
需求分析
      ↓
已确认控制规格
      ↓
┌─────────────────────────────────────────────────────┐
│ 按任务组装的生成上下文                              │
│                                                     │
│ SQLite 证据层                 Instruction Registry  │
│ 手册 / 设备 / 错误 /          CPU 支持 / 参数数量 / │
│ 调试案例 / 设计模式           操作数角色 / D-P 形式 │
│          │                            │              │
│          └──────────┬─────────────────┘              │
│                     ↓                                │
│ 语义内核 + 已确认规格 + 输出 schema                 │
│ + 当前程序/修改范围 + 定向检索证据                  │
└─────────────────────┬───────────────────────────────┘
                      ↓
              模型 / 外部 Agent
                      ↓
                 候选程序
                      ↓
      同一指令契约 + 确定性 Validator
                      ↓
                    PLC IR
                      ↓
             版本化工程 + Diff
            ┌─────────┼─────────┐
            ▼         ▼         ▼
       GX Works2    仿真      评审/证据
```

模型不需要自己“记住”整套三菱指令集，也不需要从自由文本里猜 CPU 支持范围，更不需要从聊天历史重建整个工程。工程事实和可执行边界由应用维护，再根据任务选择性注入模型上下文。

---

## 当前功能状态

| 领域 | 状态 |
| --- | --- |
| 基于已确认规格的自然语言分析与 Ladder 生成 | ✅ 可用 |
| SQLite schema-v3 PLC 知识库 | ✅ 可用 |
| 精确结构化检索 + entity + BM25/FTS5 + 本地 dense 检索 | ✅ 可用 |
| 按 PLC/任务范围检索、来源优先级与确定性 rerank | ✅ 可用 |
| 通过 SQLite 提供分析阶段控制架构知识 | ✅ 可用 |
| 指令 / 软元件 / 错误 / 调试案例结构化证据库 | ✅ 可用 |
| 生成、校验、导入、IR 共用的 Mitsubishi instruction registry | ✅ 可用 |
| 根据当前 CPU 从 registry 派生 APP_INSTR opcode 生成契约 | ✅ 可用 |
| 可审计的按任务 Prompt / context 组装机制 | ✅ 可用 |
| Repair 路径主动缩减上下文，而不是重放完整生成 Prompt | ✅ 可用 |
| Program Explorer、地址/注释搜索与引用跳转 | ✅ 可用 |
| PLC IR、结构验收、静态检查、Diff 与版本管理 | ✅ 可用 |
| 紧凑 Ladder 候选与确定性本地展开 | ✅ 可用 |
| 无效候选保留与有边界的局部修复 | ✅ 可用 |
| GX Works2 Ladder CSV 导入 / 导出 / 同步 | ✅ 可用 |
| 从已保存 PLC IR 无模型重新导出最新 CSV | ✅ 可用 |
| 对受支持超大 Ladder 结构进行 GX Works2 原生 CSV 降级 | ✅ 可用 |
| 本地 Web 工程工作台与持久化任务 | ✅ 可用 |
| 内置工程 Agent 与结构化 Tool Runtime | ✅ 可用 |
| 独立 MCP Server 与 Web Service Bridge | ✅ 可用 |
| Codex App MCP 接入与真实客户端活动显示 | ✅ 可用 |
| 多个内置模型 Profile 与自定义 OpenAI-compatible Profile | ✅ 可用 |
| 模型发现 + tool calling / JSON-object 能力探测 | ✅ 可用 |
| 可编辑仿真工作台与测试方案工作流 | 🧪 实验性 |
| GX Simulator2 自动化执行与证据采集 | 🧪 实验性 |
| 基于证据的调试规划与限定范围 Patch | 🧪 实验性 |
| 高级维护模式下的真实 PLC 只读观测 | 🧪 实验性 |
| GXW 检查与受控往返写入 | 🧪 实验性 |
| 结构化梯形图 / FBD 生成与编辑 | 🧪 实验性 |
| Structured Text 生成 | 🧪 实验性 |
| 原生 GXW 自动编译命令 | 🚧 尚未完成 |
| 完整任意 GXW / IEC FBD 支持 | 🚧 尚未完成 |
| GX Works3 适配器 | 📋 计划中 |
| 通用真实 PLC 写入路径 | 📋 当前 Web 工作流不开放 |

“实验性”表示已经存在受限实现和测试证据，**不代表**对任意 PLC 程序、GX Works 版本、CPU 家族、函数库或 Windows 环境都具备生产级通用支持。

---

# SQLite PLC 知识引擎

本地 SQLite 知识层是当前 GXWorks Agent 最核心的架构组件之一。

项目不是把完整手册直接放进 Prompt，也不依赖远端向量数据库，而是随程序打包一个 **schema-v3 SQLite 知识索引**，把权威结构化工程记录、可检索文本和本地 dense retrieval 放在同一知识系统中。

```text
Mitsubishi 官方手册          人工整理设计知识           gxw2-skill 支持语料
       │                            │                          │
       ├─ PDF/layout/table 解析     ├─ 仅 analysis 使用       ├─ Markdown/examples
       │                            │                          │
       ├─ authoritative stores      │                          │
       │   ├─ instructions          │                          │
       │   ├─ instruction_aliases   │                          │
       │   ├─ device_records        │                          │
       │   ├─ error_records         │                          │
       │   └─ debug_cases           │                          │
       │                            │                          │
       └──────────────────────── schema-v3 SQLite ────────────┘
                                ├─ chunks
                                ├─ entity_index
                                ├─ FTS5
                                └─ vector metadata
                                        │
                                        ▼
                              本地 NumPy LSA 索引
```

正常运行时不会重新解析 PDF，也不会启动时重建 embedding。PDF 解析、第三方语料导入、结构化知识抽取和 dense 索引构建都属于离线构建流程。

## 运行时检索链路

```text
Query understanding
  ├─ exact instruction / device / error / debug lookup
  ├─ entity lookup
  ├─ BM25 / SQLite FTS5
  └─ lazy local dense search
             ↓
   weighted reciprocal-rank fusion
             ↓
 deterministic cross-signal reranker
             ↓
 source priority + PLC/task scope
             ↓
      final character budget
```

当前检索遵循几个重要原则：

- Mitsubishi 官方结构化存储继续作为指令/软元件事实的 authoritative source；
- 随包提供的 `gxw2-skill` 是 **supporting source**，不是官方手册替代品；
- 控制架构设计模式同样写入 SQLite，但限定在 `analysis` 任务，不会被当成指令事实；
- 精确 opcode / device 证据优先于普通全文匹配；
- source priority 是显式的，支持性示例不能静默压过已审计的官方事实；
- 内置模型流程和 MCP / 外部 Agent 共用同一检索策略。

## SQLite 运行时优化

`src/knowledge_retriever_core.py` 针对低延迟本地检索进行了专门设计：

- 第一次真正需要检索时才打开 SQLite；
- 每个调用线程维护自己复用的 connection 和 schema snapshot；
- 打包数据库使用 `mode=ro&immutable=1` 打开；
- `PRAGMA query_only=ON` 防止意外写入；
- SQLite 临时工作区放在内存中；
- 在支持的平台请求 256 MiB mmap；
- 重复检索使用有上限的 LRU cache；
- dense artifact 只有实际需要时才加载，不进入应用启动关键路径；
- 在最终 rerank 前先按 PLC/任务和候选数量进行范围限制。

因此正常使用不需要额外部署远端向量数据库，也不需要为了打包知识检索调用云 embedding 服务。

## 当前知识库快照

仓库 manifest 当前大致记录：

| 指标 | 当前快照 |
| --- | ---: |
| SQLite 数据库大小 | ~141 MB |
| Knowledge chunks | 4,714 |
| Entity index rows | 67,835 |
| Structured instructions | 285 |
| Instruction aliases | 905 |
| Device records | 2,184 |
| Error records | 376 |
| Debug cases | 26 |
| Dense dimensions | 192 |
| Dense features | 8,192 |
| 主 benchmark Recall@10 | 1.0000 |
| 主 benchmark Recall@5 | 0.9853 |
| 主 benchmark negative accuracy | 1.0000 |
| 记录的平均检索延迟 | ~125 ms |

这些数字是仓库构建和评估结果，不代表每一台电脑上的固定性能保证。

知识来源、重建流程、benchmark、来源权威级别和第三方语料策略见 [知识库文档](resources/knowledge/README.md)。

---

# Instruction Registry：证据层不等于可执行契约层

SQLite 和 instruction registry 是刻意分开的两个层次。

**SQLite 回答：**“当前任务需要哪些有出处的工程事实？”  
**Instruction Registry 回答：**“当前程序允许输出哪些指令形式，确定性代码应该如何解释它们？”

`src/instruction_registry.py` 是一个数据驱动的 Mitsubishi 指令目录，并被 **生成、校验、导入和 PLC IR 分析** 共用。它集中维护：

- mnemonic 与 canonical operation；
- 指令 category / semantic kind；
- 操作数数量；
- operand role；
- read / write / read-write 参数位置；
- 已知软元件前缀限制；
- CPU support；
- contract level；
- Mitsubishi D/P modifier 与自动派生形式。

模型可生成的指令集合不是直接从 RAG 文本里“看懂后自由输出”，而是从 registry 根据当前 CPU 派生。

普通线圈、定时器、计数器继续使用专门的 Ladder node；应用指令的 `APP_INSTR.opcode` 则被限制在当前 CPU 允许的指令集合中，而不是接受任意字符串。

对于从 GX Works2 导入但本地尚未掌握完整语义的 vendor instruction，系统仍可以保守表示和往返保存；但不会凭猜测补出 write target 或其他工程语义。

这个分层很重要：**手册检索本质上是带排序的工程证据，而指令合法性、操作数角色和 CPU 支持必须是确定性的。**

---

# Prompt 组装：只给当前任务最小充分上下文

`src/plc_generation_context.py` 负责把知识证据层和确定性契约层连接起来。

当前 Prompt 架构不会对每个请求都重放一份越来越大的历史 PLC Prompt，而是按任务动态组装：

```text
已确认规格
    │
    ├────────────────┐
    │                │
    ▼                ▼
SQLite retrieval   Instruction Registry
相关工程证据       当前 CPU opcode 集合
    │                │
    │                ▼
    │        machine-readable Ladder schema
    │                │
    └────────┬───────┘
             ▼
       compact semantic kernel
               +
       已确认规格安全投影
               +
       当前程序 / 修改范围
               +
       定向 specialist delta
               +
       有字符预算的检索证据
               ↓
          模型 / MCP 客户端
```

对于 Ladder 生成，`plc_generation_contract.py` 会把 machine-readable output schema 注入 system context。已知 PLC 型号时，`APP_INSTR.opcode` 的 enum 直接来自：

```text
generation_app_instr_mnemonics(plc_model)
```

因此模型看到的 CPU 范围指令子集，与后端确定性校验真正接受的指令子集来自同一个 registry，而不是两份人工维护的清单。

Prompt 内部也存在明确的权威顺序：输出 schema 和本轮明确修改优先；其后才是已确认规格和 generation contract；PLC 型号证据和 specialist context 用于补充工程事实，而不能覆盖更高优先级约束。

组装机制还会根据任务主动控制上下文：

- 普通 generation：注入一个紧凑 semantic kernel + 定向 SQLite 证据；
- analysis：可从 SQLite 获取架构选择知识，而不是长期硬编码一大段方案 Prompt；
- edit：加入当前程序和修改范围，不重建无关上下文；
- contract repair：只给不可变失败 baseline、validator 证据和允许修复范围，不再注入正常 generation 知识包；
- format repair：完全不注入 PLC knowledge，只允许恢复可解析 JSON 结构；
- motion / VFD / SFC 等 specialist prompt 只在匹配对应任务时按预算注入；
- Prompt context policy 会记录哪些 section 被 included / excluded，便于审计实际模型输入。

这里的目标不是“给模型更多 Prompt”，而是让上下文大小和权威关系可预测：**事实从 SQLite 检索，可执行边界从 registry 派生，Prompt 只组装当前任务真正需要的内容。**

---

# 一套工程核心，多种 AI 入口

内置模型和外部 Agent 最终都会进入同一工程层。

```text
                 ┌──────────────────────────────┐
                 │ Built-in ModelProvider       │
                 │ DeepSeek / GLM / compatible │
                 └──────────────┬───────────────┘
                                │
外部 AI Agent                   │
Codex / MCP 客户端              │
          │                     │
          ▼                     ▼
        MCP             共享 generation context
          │     spec / SQLite evidence / schema / scope
          └────────────────┬────────────────────┘
                           ▼
                     候选程序处理
               compatibility / scope / structure
                           ▼
                 registry + validators
                           ▼
                         PLC IR
                           ▼
                 Tool Runtime / PLC Core
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          GX Works2      仿真器       GXW / FBD
```

因此无论候选来自内置 API 还是 MCP 客户端，普通 Ladder 生成和编辑都共用同一份已确认规格、检索策略、指令契约、兼容处理、结构验收、PLC IR 构建、产物渲染和版本化工程状态。

---

# 模型 Profile 与能力检测

当前配置层使用 OpenAI-compatible transport，并支持多个模型 Profile。内置项目预设目前包括：

| Provider family | 内置 Profile |
| --- | --- |
| DeepSeek | `deepseek-v4-pro`, `deepseek-v4-flash`, `deepseek-v4-flash-vision-exp` |
| 智谱 GLM | `glm-5.3-flash`, `glm-5.3`, `glm-5.2` |
| Custom | 用户自定义 OpenAI-compatible endpoint/model |

设置页可以发现 endpoint 暴露的 model id，并主动探测适合通过通用兼容接口安全测试的能力：

- tool/function calling；
- JSON-object structured output。

reasoning 支持、reasoning intensity、多模态语义，以及 provider-specific thinking controls 不会仅凭通用文本请求进行猜测；除非存在可靠的专用探测，否则保留为显式 Profile / Provider 配置。

```text
model capability      = Provider / Model 可以支持什么
profile configuration = GXWorks Agent 当前请求什么
runtime probe          = 可以在运行时安全验证什么
```

best-effort probe 一次失败，不会自动抹掉已有的 known-good capability 配置。

---

# 已确认规格与生成链

GXWorks Agent 不假定用户第一次自然语言输入就是完整 PLC 需求。

分析阶段可以生成可审查控制规格，包括需求摘要、候选控制架构、待确认问题、参数、I/O 分配和显式用户规则。

架构选择知识通过 SQLite 检索提供，而不是把一整套控制方案永久复制进 system prompt。

规格确认后，当前 Ladder 生成路径为：

```text
已确认规格
    ↓
按任务 Prompt 组装
 SQLite evidence + registry-derived schema
    ↓
紧凑 Ladder 候选
    ↓
兼容 / 语法规范化
    ↓
基于 registry 的结构 / 设备 / 指令验收
    ↓
PLC IR
    ↓
JSON / ST / SVG / CSV 产物
    ↓
版本化工程 + Diff
```

生成 Agent 与自由形式 analysis prose 隔离，最终约束生成的应当是已确认工程事实，而不是此前聊天叙述本身。

---

# 无效候选与有边界 Repair

生成失败时，系统尽量保留候选，而不是只返回一个不透明的模型错误。Web 工作台可以显示 rejected Ladder preview 和 diagnostics，供检查或 repair。

当前 repair 策略刻意保持较窄范围：

- validator evidence 决定哪些字段允许修；
- 优先使用 path-addressed field patch，而不是整对象重写；
- 可确定性恢复的 syntax / format 问题留在本地处理；
- structure-only repair 时冻结 instruction semantics；
- 不用猜 opcode / device 作为通用 fallback；
- 无法修复时保留原始 validation reason；
- 修复后重新经过完整校验才进入正常交付。

这样可以避免所谓“repair”实际退化成一次隐式的语义重新生成。

---

# PLC Intermediate Representation

内部 **PLC IR** 是通过验收后的候选程序与工程操作之间的语义层。

```text
                                      ┌─ Ladder CSV
                                      ├─ Structured Text
模型 → accepted candidate → PLC IR ───┼─ SVG preview
                                      ├─ static inspection
                                      ├─ Diff / scoped change analysis
                                      ├─ test planning
                                      └─ GX Works2 adapters
```

已知指令的 read/write 行为和语义来自应用自身维护的契约，而不是每次再从模型输出解释一次。

保存后的 IR 可以在不再次调用模型的情况下重用。因此 Web 工作台可以从已保存版本直接重新渲染 Ladder，并重新生成一份 **fresh GX Works2 CSV bundle**。

---

# GX Works2 Ladder 集成

目前最成熟的 GX Works2 后端仍然是 **Ladder CSV 工作流**。

当前包括：

- Ladder CSV 与 device-comment CSV 生成；
- GX Works2 导入 / 导出；
- 程序与注释同步；
- 覆盖前备份；
- 同步 baseline 与外部手工修改冲突保护；
- 可选 round-trip verification；
- 从已保存 PLC IR 无模型重新导出 fresh CSV；
- 对支持的超大 Ladder 结构执行确定性 GX Works2 原生 CSV 降级。

最近的工作重点之一是收紧 GX Works2 原生 CSV 渲染。对于支持的超大 OR 风格结构，会在导出前进行拆分 / lowering，使 native Ladder block 落在 GX Works2 预期的 row / step 边界内；部分指令的 native step 宽度也显式建模。

本地版本保存成功**不代表**已经通过原生 GX 编译、仿真，或者真实 PLC 执行。

---

# 原生 GXW / 结构化梯形图 / FBD 方向

GXWorks Agent 还包含一条基于受控逆向实验推进的实验性 **GXW** pipeline。

对于已经掌握的特定布局，目前可以检查 `Program.pou` 记录、保留未知记录、编辑已知 declaration、生成受支持的结构化梯形图/FBD object 与 wire、同步部分 Function Block instance、更新已知 metadata，并通过 Web 工作台导入、预览、版本化和下载受控 GXW 候选。

已有证据支持的模板包括部分 contact、coil、terminal、`MOV`、`TON`、`TON_E`、`CTU`、`CTU_E`，以及部分已保存 Function / Function Block ABI 模式。

这仍属于有边界的研究工作。任意第三方库、无限制 IEC FBD graph、所有 GX Works2 工程变体，以及通用原生 compile command 都尚未完成。

逆向研究证据位于 `docs/research/`。

---

# 仿真、调试与硬件边界

工作台包含可编辑测试方案、issue → network → test 可追踪关系，以及实验性的 GX Simulator2 自动执行 / 证据采集。

Debug 保持 evidence-bound 和 scoped，不开放无限制语义重写。

真实 PLC 权限更严格。目前高级维护路径可以在配置后执行限定范围的 **只读观测**；当前 Web 工作流不开放通用真实 PLC 写入能力。

---

# 快速开始

## Windows Web 工作台

使用 Windows 打包版本时，解压完整的 `GXWorks-Agent-Web` 目录并运行：

```text
start-web.cmd
```

选择工作区。在 **设置 → 常规 → 操作审批** 中，可以为受支持的 GX / 仿真 / 调试外部动作选择 **逐项审批**（默认）、**替我审批** 或 **完全访问**。无论采用哪种模式，PLC 校验都不会关闭。

启动工作台**不会**自动启动 GX Works2、GX Simulator2、仿真 gateway，也不会自动连接真实 PLC。

详见 [Web 工作台指南](docs/integrations/web.md)。

## 通过 MCP 连接 Codex

1. 启动 GXWorks Agent Web 并打开 PLC 工程。
2. 进入 **设置 → 模型 → Integrations / MCP**。
3. 点击 **Connect Codex / 连接 Codex**。
4. 重启 Codex App，新建任务。
5. 直接提出 PLC 工程任务。

Codex 使用的仍是与内置流程相同的当前工程、已确认规格、SQLite 证据层、registry 派生指令契约、Prompt 组装策略、候选 pipeline 和 PLC IR。

详见 [Codex 集成](docs/integrations/codex.md) 与 [MCP 集成](docs/integrations/mcp.md)。

## 源码安装

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements/web.txt

.\build-web.bat --no-pause

$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m integrations.web --workspace "D:\PLCWorkspaces\my-workspace" --port 8765 --open-browser
```

运行测试：

```powershell
pytest -q
```

API Key 通过 Windows Credential Manager 保存，不提交到仓库配置文件。

---

# 仓库结构

```text
src/                    Python 工程核心、Prompt 组装与运行时
web/                    React/Vite Web 工作台
resources/knowledge/    打包 SQLite/FTS5 知识索引与 manifest
resources/instructions/ 确定性 Mitsubishi 指令 catalogue
tools/                  知识构建、导入、审计与工程工具
hardware_reader/        有边界的硬件观测 helper
docs/                   集成、架构与研究文档
docs/research/          GXW / 结构化梯形图 / FBD 逆向证据
benchmarks/              RAG 与工程 benchmark 资源
packaging/               Windows 打包支持
requirements/            运行依赖集合
```

重要文档：

- [本地 PLC 知识引擎](resources/knowledge/README.md)
- [Web 工作台](docs/integrations/web.md)
- [MCP 集成](docs/integrations/mcp.md)
- [Codex 集成](docs/integrations/codex.md)

---

# 开发原则

- 工程状态属于应用，不属于聊天历史；
- SQLite retrieval 提供的是有排序的工程证据，不是最终可执行权威；
- instruction registry 提供确定性可执行契约，不是普通 prose knowledge；
- Prompt 应按任务组装，只注入最小充分上下文；
- output schema 和 validator 必须基于同一 CPU 范围指令子集；
- 官方工程证据优先于 supporting example；
- 模型输出必须经过确定性验收边界；
- repair 由证据限定，而不是依赖语义猜测；
- 已保存产物应尽可能可以在无模型调用时重现；
- 外部副作用必须受到 policy / approval 控制；
- 未知 GXW 结构优先保留，不凭空构造；
- 软件校验不会被描述成原生 GX 验证或真实硬件验证。

---

# Roadmap

近期工作重点：

- 在不削弱来源权威性的前提下继续扩展和审计 SQLite FX3U 知识层；
- 继续收紧 SQLite 结构化证据与 instruction registry 之间的一致性；
- 扩展 verified instruction/device contract 与 CPU-specific generation subset；
- 优化 task-shaped Prompt 组装、检索预算与 Prompt context 可观测性；
- 改进模型 / Provider capability resolution，同时保持 provider-specific controls 显式化；
- 继续加强 GX Works2 CSV / native evidence 工作流；
- 基于可复现实验证据扩展 GXW / 结构化梯形图 / FBD 支持；
- 提高仿真 / diagnostics 证据追踪能力；
- 继续围绕 Web 工作台整合产品入口。

---

# 安全与适用范围

GXWorks Agent 仍处于积极开发阶段。生成的 PLC 逻辑在部署前必须结合真实设备、CPU、接线、安全回路、运行模式和适用标准进行人工审查与验证。

急停、防护门、联锁、运动、压力、温度等安全关键功能不能只依赖生成的应用逻辑或软件仿真。

---

# License

本项目采用 [Apache License 2.0](LICENSE)。