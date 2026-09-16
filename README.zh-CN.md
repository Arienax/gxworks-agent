# GXWorks Agent

[English](README.md) | 简体中文

> **面向 Mitsubishi MELSEC PLC 开发的 AI 原生工程工作台与 Agent Runtime。**

![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)
![PLC](https://img.shields.io/badge/focus-FX3U-orange.svg)
![Status](https://img.shields.io/badge/status-active%20development-yellow.svg)

<p align="center">
  <img src="resources/assets/demo.gif" alt="GXWorks Agent 演示" width="1200">
</p>

**GXWorks Agent** 是一个主要面向 **FX3U + GX Works2** 的实验性 PLC 工程工作台。它把自然语言需求分析、已确认控制规格、本地 PLC 知识检索、模型辅助 Ladder 生成、确定性校验、PLC IR、版本化工程状态、GX Works2 集成，以及供内置模型和 Codex 等 MCP 客户端共用的工程 Runtime 放在同一套系统中。

项目遵循一个原则：**LLM 输出只是候选结果，不等于工程结果。** 工程事实、指令边界、校验、工程状态、版本管理和外部副作用仍由应用本身负责。

---

## 项目目前最核心的设计

### SQLite 证据层 + 确定性指令契约

本地知识层使用随程序打包的 **SQLite schema-v3 索引**，而不是把整本手册直接塞进 Prompt。运行时会组合结构化的指令 / 软元件 / 错误 / 调试记录、entity 检索、BM25/FTS5、本地 dense LSA、来源优先级，以及按 PLC 型号和任务类型的范围过滤。

SQLite 与 `instruction_registry` 刻意承担不同职责：

- **SQLite** 回答：当前任务需要哪些有出处的工程证据？
- **Instruction Registry** 回答：当前程序实际允许输出哪些指令形式，确定性代码应该如何解释它们？

`instruction_registry` 被生成、校验、导入和 PLC IR 分析共用。CPU support、操作数数量与角色、读写语义，以及 Mitsubishi D/P 指令形式都统一维护在这里。生成阶段允许输出的 `APP_INSTR` opcode 集合，也来自后续 validator 使用的同一个 registry。

### 按任务组装 Prompt

`plc_generation_context.py` 不再为每次请求重放一个巨大的历史 Prompt，而是根据当前操作组装模型上下文。普通 Ladder 生成可以包含已确认规格、当前程序或修改范围、定向检索到的 SQLite 证据、必要的专用上下文，以及由指令契约派生的机器可读输出 schema。

Repair 路径会主动缩减上下文：contract repair 只拿失败 baseline 和允许修复的范围；format repair 则不会注入 PLC 知识上下文。

### PLC 工程核心，而不只是代码生成

通过校验的候选会进入 PLC IR，并作为版本化工程状态保存。Diff、限定范围修改、有边界的 repair、工程产物生成、GX Works2 CSV 操作，以及外部 MCP 客户端都建立在同一套工程层之上。内置模型和 Codex 不维护两套不同的 PLC 实现。

---

## 当前能力状态

| 领域 | 当前状态 |
| --- | --- |
| 基于已确认规格的 Ladder 生成与编辑 | ✅ 可用 |
| SQLite 驱动的 PLC 知识检索 | ✅ 可用 |
| Instruction Registry + CPU 范围生成契约 | ✅ 可用 |
| 按任务组装 Prompt / context | ✅ 可用 |
| PLC IR、校验、Diff、版本管理与限定修复 | ✅ 可用 |
| GX Works2 Ladder CSV 导入 / 导出 / 同步 | ✅ 可用 |
| 从已保存 PLC IR 无模型重新导出 CSV | ✅ 可用 |
| Web 工程工作台 | ✅ 可用 |
| MCP Server / Codex 集成 | ✅ 可用 |
| 仿真与测试方案工具 | 🧪 实验性 |
| 结构化梯形图 / FBD 原生格式生成 | 🧪 **目前仅为块级研究**：只能从少量已验证模板中生成一个受支持的块，尚不能生成完整程序 |
| GXW 原生格式逆向 | 🧪 研究中，仅覆盖已确认结构和可复现实验证据 |
| 原生 GXW 编译 | 🚧 尚未形成通用编译工作流 |
| 真实 PLC 只读观测 | 🔧 已有底层只读 helper，但**当前 Web GUI 尚未适配** |
| 真实 PLC 写入路径 | ❌ 当前不开放 |
| GX Works3 适配器 | 📋 计划中 |

这里的“实验性”只表示已经存在有限实现或可复现实验证据，**不代表已经形成完整用户工作流，也不代表支持任意程序。**

---

## GX Works2 主线

目前最成熟的端到端路径仍然是 **Ladder → PLC IR → GX Works2 CSV**。

当前已经覆盖 Ladder / device-comment CSV 生成、导入导出同步、冲突保护、从已保存 IR 无模型重新导出，以及对部分超出 GX Works2 原生 CSV 布局限制的 Ladder 结构做确定性降级。

本地通过校验或成功导出的版本，并不等于已经完成 GX 原生编译、仿真器执行或真实 PLC 执行。

---

## 原生 GXW / 结构化梯形图 / FBD 研究边界

这一部分目前仍属于逆向研究，而不是完整编程后端。

现有证据只覆盖部分 GX Works2 Structured Ladder/FBD 记录，以及少量已确认的 block / object 模板。当前实现可以在已知布局下生成或改写**单个受支持块**，但还不能生成完整的结构化梯形图 / FBD 程序、任意 Network 拓扑，也不能处理通用 IEC FBD 图。

相关实验记录和证据位于 [`docs/research/`](docs/research/)。

---

## 快速开始

### Windows Web 工作台

解压完整的 `GXWorks-Agent-Web` 目录后运行：

```text
start-web.cmd
```

选择工作区即可进入 Web 工作台。操作审批、工作区行为和源码运行说明见 [Web 集成文档](docs/integrations/web.md)。

### 源码安装

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

### Codex / MCP

启动 GXWorks Agent Web 并打开 PLC 工程后，在 **Settings → Model → Integrations / MCP** 中连接 Codex。外部 MCP 客户端与内置流程共用同一工程状态、检索策略、生成契约、候选处理链和 PLC IR。

详见 [Codex 集成](docs/integrations/codex.md) 和 [MCP 集成](docs/integrations/mcp.md)。

---

## 文档入口

- [PLC 知识引擎](resources/knowledge/README.md)
- [Web 工作台](docs/integrations/web.md)
- [MCP 集成](docs/integrations/mcp.md)
- [Codex 集成](docs/integrations/codex.md)
- [GXW / 结构化梯形图 / FBD 逆向研究](docs/research/)

---

## 安全与范围

GXWorks Agent 仍处于持续开发中。生成的 PLC 逻辑在部署前必须结合实际 CPU、接线、设备行为、安全回路、运行模式和适用标准进行人工审查与验证。

急停、防护、运动限位、压力、温度等安全关键功能，不能只依赖生成的应用逻辑或软件仿真。

---

## License

Licensed under the [Apache License 2.0](LICENSE).
