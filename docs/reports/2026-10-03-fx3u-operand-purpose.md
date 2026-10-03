# FX3U 操作数用途交付审计 · 2026-10-03

被测基线为 `b462507cb8c4dcfd23197ea79ef17fbbd120cead`，结果对应其上的本轮工作树修改。范围是既有冻结签名账本中的 **227 个 FX3U 指令形式、631 个操作数槽位**；每个形式先核对 Core 槽位顺序与账本，再通过共享 `knowledge.retriever.build_knowledge_context` 测量用途证据交付。预算为 `char_budget=24000`、`top_k=8`。

签名来源为 `JY997D16601 R`、`JY997D34701 M`；运行时沿用现有来源权威规则，定位指令还可使用 `JY997D16801 K`。索引为 `resources/knowledge/fx3u_knowledge.sqlite`。环境为 Windows、Python 3.13.14，未调用真实模型、GX Works2 或 PLC。下列完整用途证据计数包含候选证据，不代表用途已验证；最终 prompt 编译还有独立预算。

## 结果与修复

| 指标 | 解析修复前 | 解析修复后 |
| --- | ---: | ---: |
| 既有 `source_verified` 用途槽位 | 27 | 27 |
| `candidate_evidence` 用途槽位 | 447 | 580 |
| `unresolved` 用途槽位 | 157 | 24 |
| 用途证据齐全的形式 | 152 | 217 |
| 全部用途已验证的形式 | 5 | 5 |

“修复前”是已加入本轮冲突仲裁、尚未修表格解析和来源定位的中间状态，并非已发布基线的覆盖率。原始结果见[修复前摘要](../../research/results/fx3u-operand-purpose-20261003-before.json)与[修复后摘要](../../research/results/fx3u-operand-purpose-20261003-after.json)。初版诊断回查漏传 `base_opcode`，曾将部分 D/P 形式误分为缺表；保存的修复前摘要已纠正该诊断，状态计数与分母不变。

最大失败桶是 77 个“表中有行但未绑定”的槽位。通用解析现在通过显式原生符号绑定跨行描述，处理字体占位符和错位类型列；索引描述提示不能覆盖原文符号。现有已验证 Core 合同的手册、修订与定义页同时用于找回共用 D/P 定义，避免概览示例替代定义。这些修复未加入逐指令用途规则。

Core 按 `(position, facet)` 仲裁：等价候选合并来源；值或条件作用域不同则保持缺口；已有验证事实维持其所有权。仲裁在预算选择前进行，冲突原文单元及重叠呈现整体隔离。缓存的 `OPERANDS` 摘要不再绕过仲裁，最终 prompt 接收缺口、冲突原因和候选数量，完整候选与原文定位保留在诊断元数据。

修复后交付的 **581 条候选 witness** 对应 580 个槽位，其中一个槽位有多条来源。逐条回查原始 `chunks.text`：手册、修订、页码及行/上下文区间均有效，63 条跨行 witness 可由 `value_spans` 重建原值。

## 剩余缺口

| 失败桶 | 修复前槽位数 | 修复后槽位数 |
| --- | ---: | ---: |
| `operand_row_not_bound` | 77 | 2 |
| `operand_table_not_recovered` | 40 | 4 |
| `operand_symbol_not_recovered` | 21 | 0 |
| `candidate_conflict` | 10 | 9 |
| `candidate_unit_quarantined_or_not_selected` | 9 | 9 |

剩余 24 个槽位分布如下。冲突单元中其他槽位的用途也可能被隔离，故缺口数大于直接冲突数。

| 形式 | 未解决符号 | 原因 |
| --- | --- | --- |
| CML、CMLP、DCML、DCMLP | S、D | D 候选冲突，S 所在单元隔离 |
| CRC | N | 表行未绑定 |
| DHSZ | S1、S2、D | 候选冲突 |
| DVIT | S1、S2、D1、D2 | 未恢复操作数表 |
| MTR | S、D1、D2、N | N 候选冲突，其余单元隔离 |
| SAVER | S | 表行未绑定 |
| SORT | M1、M2、N | N 候选冲突，其余单元隔离 |

CML 的候选原文包含 `Word device number storing inverted data` 与首字符丢失的 `ord device number storing inverted data`，仲裁未猜测其等价性。其他冲突继续保留原始作用域及来源，待解析证据充分后处理。

4 个候选槽位的 `native_definition_match=false` 分别是 DRVA/S1、DRVI/S1、PLSV/D2、ZRN/S3：它们使用现有权威定位手册 `JY997D16801 K`，而冻结签名引用编程手册 R 页码。该字段只比较冻结签名的定义来源，不能作为这些候选来源错误的判定。

本轮未执行用途批量 promotion。`source_verified` 用途仍为 27 个槽位，候选交付改善不改变验证状态。审计测量 purpose，不能据此宣称所有其他 facet、整个 FX 系列或 PLC 执行语义已经覆盖。

## 验证与复现

现有 CI [run 37110310900](https://github.com/Arienax/gxworks-agent/actions/runs/37110310900) 的唯一失败是 `test_section_fallback_keeps_manual_bytes_and_instance_step_facts` 仍期待 `STEP_WIDTH` 文本注入。本轮更新断言，检查结构化 step-width 元数据、过滤后的文本和原始手册字节；该模块 33 项本地测试通过。

完整本地回归结果：**4402 passed、1 failed、20 skipped、9 subtests passed**，耗时 241.93 秒。唯一失败是本轮未修改的 `tests/test_model_observations.py::test_corrupt_cache_and_expired_samples_are_ignored`，在读取损坏 SQLite 缓存后删除文件触发 Windows `WinError 32`。19 项因 Linux Bash 工作流而跳过，1 项因 Windows 符号链接权限跳过；另显式排除 2 项会读取当前 GX Works2 会话的 live 测试。未将这些项目计入通过数。

初次执行受系统 GBK 编码与缺少项目已声明的 `filelock` 影响；最终运行启用 `PYTHONUTF8=1`，在临时虚拟环境补齐 `filelock 3.32.7`。Windows checkout 的签名账本为 CRLF，既有字节审计使用临时文件中的 `HEAD` 原始 LF 字节，先断言 JSON 内容与工作树一致；冻结账本未修改。本轮审计工具也修复了临时账本位于仓库外时的路径表示。

新增回归覆盖候选排列及增量交付不变量、等价来源合并、条件作用域冲突、预算和来源顺序变化、跨行原文定位、共用定义与 CPU 边界。隔离 Provider 测试检查真实最终 Agent B 请求不含两种冲突用途，handoff 保留未解决槽位。离线 MCP smoke 通过，15 项工具的注册和生成候选流程保持其结果契约。

用途审计可复现为：

```powershell
$env:PYTHONUTF8 = '1'
python tools/audit_operand_semantics.py --purpose-coverage --report research/results/fx3u-operand-purpose-20261003-after.json
python scripts/mcp_smoke.py
```

本次完整 Windows 回归使用以下入口，Python 来自上述补齐依赖的临时环境：

```powershell
@'
import json, subprocess, tempfile
from pathlib import Path
import pytest
from tools import audit_operand_semantics as audit

raw = subprocess.check_output(['git', 'show', 'HEAD:resources/instructions/mitsubishi/fx3u_contract_promotions.json'])
assert json.loads(raw) == json.loads(audit.SIGNATURE_LEDGER.read_bytes())
with tempfile.TemporaryDirectory(prefix='gxw-purpose-suite-') as directory:
    ledger = Path(directory) / 'fx3u_contract_promotions.json'
    ledger.write_bytes(raw)
    audit.SIGNATURE_LEDGER = ledger
    raise SystemExit(pytest.main(['-q', '-rs', '--ignore=tests/test_gxworks2_live_boundaries.py', '--tb=short']))
'@ | python -
```

编译检查、差异空白检查及 50 个本地文档链接检查通过。对本轮 15 个文件使用隔离 Git 索引执行 `python scripts/check_repository_storage.py --staged` 通过，保留用户实际暂存区；新文件按研究摘要与小型 witness 的存储规则保留在普通 Git。远端 CI 尚未运行本轮工作树修改。
