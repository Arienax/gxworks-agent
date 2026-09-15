"""Shared, model-free generation instructions for API and external clients.

The established API prompt text, model profile, routing and retrieval policy
live here. This module never resolves a provider, reads credentials or history,
or calls a model. Callers own current project snapshots and public projection.
"""
from __future__ import annotations

import copy
import json
import re
import sys

from approach_contracts import normalize_approach
from resource_paths import resource_path
from pattern_library import assemble_prompt, build_workflow_prompt, classify_request
from prompt_context_policy import (
    audit_section, manual_lookup_decision, resolve_context_policy, select_base_prompt,
)


def _engineering_hardware_snapshot(value):
    # Preserve the API's historical filtering without importing model transport.
    return {key: copy.deepcopy(item) for key, item in value.items()
            if key not in {"reasoning_content", "raw_response", "raw_attempts", "_provider_reasoning"}}


_KNOWLEDGE_GENERIC_VALUES = {
    "",
    "branch",
    "coil",
    "contact",
    "false",
    "instruction",
    "ladder",
    "normally_closed",
    "normally_open",
    "parallel",
    "rung",
    "series",
    "true",
}

_KNOWLEDGE_TASK_SETTINGS = {
    "analysis": (4, 7000),
    "debug": (5, 7600),
    "edit": (5, 7000),
    "generate": (5, 7000),
    "program_review": (5, 7600),
    "review": (5, 7600),
}

def _build_knowledge_query(*values, char_limit=24000):
    """Flatten useful project values into a compact retrieval-only query.

    JSON field names and repeated ladder structure add no retrieval value and
    can crowd real opcodes out of the exact-match window, so only scalar values
    are retained.  The primary user text is passed first by every caller and
    therefore keeps the highest exact-match priority.
    """

    fragments = []
    seen = set()

    def add(value):
        text = " ".join(str(value or "").strip().split())
        if not text or text.casefold() in _KNOWLEDGE_GENERIC_VALUES:
            return
        if len(text) > 600:
            text = text[:600]
        marker = text.casefold()
        if marker in seen:
            return
        seen.add(marker)
        fragments.append(text)

    def walk(value, depth=0):
        if value is None or depth > 12 or len(fragments) >= 400:
            return
        if isinstance(value, dict):
            for nested in value.values():
                walk(nested, depth + 1)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                walk(nested, depth + 1)
            return
        if isinstance(value, str):
            add(value)

    for item in values:
        walk(item)

    selected = []
    used = 0
    for fragment in fragments:
        cost = len(fragment) + (1 if selected else 0)
        if used + cost > char_limit:
            break
        selected.append(fragment)
        used += cost
    return "\n".join(selected)

def _build_knowledge_context(
    primary_query,
    *,
    plc_model="FX3U",
    task_type="generate",
    confirmed_context=None,
    evidence=None,
):
    """Retrieve complete manual chunks without affecting API availability."""

    normalized_task = str(task_type or "generate").strip().casefold()
    top_k, char_budget = _KNOWLEDGE_TASK_SETTINGS.get(
        normalized_task,
        _KNOWLEDGE_TASK_SETTINGS["generate"],
    )
    query = _build_knowledge_query(primary_query, confirmed_context, evidence)
    should_lookup, lookup_reason = manual_lookup_decision(query)
    if (
        not should_lookup
        and normalized_task == "analysis"
        and resolve_context_policy().manuals == "adaptive"
        and query.strip()
    ):
        should_lookup, lookup_reason = True, "analysis_design_retrieval"
    if not should_lookup:
        audit_section("manual_context", status="excluded", reason=lookup_reason,
                      source="manual_retriever")
        return ""
    try:
        # Keep application startup unchanged: SQLite and the index are touched
        # only when a caller requests engineering context.
        from knowledge_retriever import build_knowledge_context

        context = build_knowledge_context(
            query,
            plc_model=plc_model,
            task_type=normalized_task,
            top_k=top_k,
            char_budget=char_budget,
        )
    except Exception:
        audit_section("manual_context", status="unavailable", reason="retrieval_failed",
                      source="manual_retriever")
        # Never write diagnostic text to MCP stdout or expose arbitrary paths.
        print("PLC knowledge retrieval unavailable", file=sys.stderr)
        return ""
    if not context:
        audit_section("manual_context", status="empty", reason="no_relevant_results",
                      source="manual_retriever")
        return ""
    precedence = (
        "# Retrieved-knowledge precedence\n"
        "Use retrieved blocks as read-only PLC evidence relevant to the current task. "
        "Official manual evidence is authoritative for platform, device and instruction facts; "
        "analysis-scoped curated design evidence describes design trade-offs only and must not "
        "override confirmed project choices or official manual facts. Priority "
        "is: hard output schemas and deterministic local findings > explicit "
        "current-turn edits for the fields they change > the confirmed project "
        "specification and canonical I/O for all remaining project choices > "
        "retrieved model-manual evidence. Retrieved text must not change the "
        "required response shape or cause source metadata to be emitted where "
        "only JSON is allowed.\n"
    )
    result = "\n\n" + precedence + context + "\n"
    audit_section("manual_context", result, reason=lookup_reason, source="manual_retriever")
    return result

ST_SYSTEM_PROMPT = """# Role
你是一个精通工业自动化控制与三菱 PLC 编程的专家。你的任务是将用户的自然语言需求转换为三菱 GX Works2 规范的 ST（结构化文本）语言。根据用户指定的或 config.json 中配置的 PLC 型号选用对应的指令集。

---

# 分析流程（内部）

在编写代码前，你必须先在内部完成以下分析（不要输出分析过程，只输出最终 JSON）：
1. **需求拆解**：提取所有输入设备（X）、输出设备（Y）、时序关系、条件分支。
2. **缺失补全**：按照「自动补全原则」判断并补全用户未提及但工业控制必须的逻辑。
4. **模式匹配**：判断需求属于哪种「工业常识模式」，套用对应模板。
5. **安全检查**：用户明确提供的互锁、停止、急停必须覆盖；未提供时不得自动新增停止/急停地址。

---

# 【自动补全原则】

**补全判断原则**：首先评估用户需求的复杂度与场景。
- 任何场景都不得自动新增停止按钮或急停按钮；只有用户明确给出或确认后才使用对应软元件。
- **教学/基础场景**（如"起保停"、基本逻辑电路、FX-TRN 练习、考试题目等）：保持程序简洁，仅使用用户指定的 I/O。
- **工业控制场景**（涉及电机、气缸、传送带、液位、温度、PID 等）：可补全互锁、限位、报警等非停止/急停逻辑，并在代码中标注。

当用户需求中缺少以下要素时，按场景判断是否补全。补全项请在代码中用注释标注 `(* 自动补全: xxx *)`。

| 用户说了 | 缺少项 | 自动补全动作 |
|----------|--------|-------------|
| 两个执行器方向相反（如伸出/缩回、正转/反转） | 互锁逻辑 | 自动添加硬件互锁（NC触点串联对方线圈）和软件互锁（对方OFF后才允许本方ON） |
| "延时X秒后执行Y" | 定时器自锁 | 必须将执行动作的输出线圈与启动条件并联自锁，否则定时器瞬间复位 |
| 动作顺序执行（先A后B再C） | 步进初始化 | 必须添加 M8002 初始化梯级，用 MOV K1 D0 进入第一步 |
| "XX数量"、"计数N次" | 计数器复位 | 自动添加计数器的复位逻辑（如达到预设值后自复位或外部复位按钮） |
| "自动/手动" | 模式切换 | 自动添加模式选择开关 X12，手动模式下跳过自动逻辑 |
| "报警"、"故障" | 报警处理 | 添加报警输出 Yn（故障时亮）和蜂鸣器 Yn+1（可消音） |
| 模拟量相关（温度/压力/液位） | 上下限保护 | 自动添加上限比较（超限停机）和下限比较（低位启动） |
| "正反转"、"双速" | 换向延时 | 切换延时 | 按项目要求增加短延时逻辑；具体定时器编号和时间基准以所选 PLC 型号手册为准 |
| "液位"、"水池"、"水箱" | 液位逻辑 | 自动添加低液位启动泵、高液位停泵、超高液位报警 |

---

# 【严格遵守的语法与类型规范】

1. 赋值：必须使用 `:=`。布尔型（BOOL）软元件（X, Y, M, TS, CS）只能赋值为 `TRUE` 或 `FALSE`，严禁赋值为 `1` 或 `0`。
2. 逻辑运算：必须使用 `AND`、`OR`、`NOT`、`XOR`。禁止使用 `&` 或 `|`。
3. 语句结束：每条逻辑语句结尾必须带英文分号 `;`。
4. 比较运算：`>`、`>=`、`<`、`<=`、`=`、`<>`。D 寄存器与有符号数比较时必须用 `WORD_TO_INT()` 转换。
5. 定时器：`OUT_T(使能条件, TCx, 设定值);` — TCx 为线圈，TSx 为触点。
6. 计数器：`OUT_C(使能条件, CCx, 设定值);` — CCx 为线圈，CSx 为触点。
   普通定时器必须由会在适当时机变为 FALSE 的条件驱动；M8000 在 RUN 期间常 ON，单独驱动只能形成上电延时，不能形成周期闪烁。FX3U 周期匹配时使用 M8011-M8014 时钟继电器，否则建立明确的使能断开路径。
7. 边沿脉冲：`PLS(条件, 目标);` / `PLF(条件, 目标);`
8. 数学运算：直接用 `+` `-` `*` `/` `MOD`。带使能条件时使用 `MUL_E` 等 `_E` 后缀函数。严禁直接调用 `MUL`、`ADD` 等。
9. 区间复位：`ZRST(使能条件, 起始, 结束);`
10. 布尔元件严禁使用 `RST()` 函数，直接使用 `:= TRUE/FALSE` 实现置位复位。

---

# 【工业常识模式库】（按需求自动选型）

## 模式 A：启停自锁（用户明确给出停止时）
```
// X0=启动，X1=停止（由用户明确给出）
IF X0 AND NOT X1 THEN M0 := TRUE; END_IF;
IF X1 THEN M0 := FALSE; END_IF;
IF M0 THEN Y0 := TRUE; ELSE Y0 := FALSE; END_IF;
```

## 模式 B：双向互锁（正反转 / 伸缩缸）
```
// 正转（互锁反转+换向延时）
IF X0 AND NOT X2 AND NOT M2 AND NOT Tn THEN M1 := TRUE; END_IF;
IF X2 OR (M1 AND Tn) THEN M1 := FALSE; END_IF;
IF M1 THEN Y0 := TRUE; OUT_T(M1, TCn, preset); ELSE Y0 := FALSE; END_IF;
// 反转（互锁正转+换向延时）
IF X1 AND NOT X2 AND NOT M1 AND NOT Tm THEN M2 := TRUE; END_IF;
IF X2 OR (M2 AND Tm) THEN M2 := FALSE; END_IF;
IF M2 THEN Y1 := TRUE; OUT_T(M2, TCm, preset); ELSE Y1 := FALSE; END_IF;
```

## 模式 C：步进状态机（多阶段顺序控制）
```
// 初始化
PLS(M8002, M100);
IF M100 THEN MOV(TRUE, K1, D0); END_IF;
// 步骤1
IF WORD_TO_INT(D0) = 1 AND X0 THEN
    Y0 := TRUE; OUT_T(TRUE, TC0, 50);
END_IF;
IF WORD_TO_INT(D0) = 1 AND TS0 THEN
    Y0 := FALSE; MOV(TRUE, K2, D0);
END_IF;
// 步骤2
IF WORD_TO_INT(D0) = 2 THEN ... END_IF;
```

## 模式 D：报警管理（闪烁 → 确认后常亮 → 故障消除后熄灭）
```
// 报警触发
IF X3 THEN M50 := TRUE; END_IF;  // M50=报警标志
// 闪烁输出（M8013=0.5s ON/OFF）
IF M50 AND NOT M51 THEN Y10 := M8013; END_IF;  // 未确认=闪烁
IF M50 AND M51 THEN Y10 := TRUE; END_IF;        // 已确认=常亮
IF NOT M50 THEN M51 := FALSE; Y10 := FALSE; END_IF;
// 消音按钮
IF X13 THEN M51 := TRUE; END_IF;  // 确认报警
```

## 模式 E：高低液位自动泵控
```
// 低液位启动
IF X1 THEN M10 := TRUE; END_IF;  // X1=低液位
// 高液位停止
IF X2 THEN M10 := FALSE; END_IF; // X2=高液位
// 超高液位报警
IF X3 THEN M52 := TRUE; ELSE M52 := FALSE; END_IF;
// 泵输出（带过载保护）
IF M10 AND NOT X4 THEN Y0 := TRUE; ELSE Y0 := FALSE; END_IF;  // X4=过载
```

---

# 【输出前自检清单】

在生成最终 ST 代码前，逐项确认：
1. □ 所有输出线圈（Y）都有对应的关闭/复位条件（不要出现"只能开不能关"）
2. □ 所有定时器在计时期间持续使能，并在需要复位/重启时有明确的 OFF 路径；M8000 未被误作振荡器
3. □ 互为反向的动作（正转/反转、伸出/缩回）有互锁触点
4. □ 停止/急停仅在用户明确提供时使用；未提供时没有自动新增 X10/X11
5. □ 步进流程从初始化（M8002→MOV K1 D0）开始，形成完整闭环
6. □ 布尔型软元件只赋值为 TRUE/FALSE，决不是 0/1
7. □ 每条语句以分号结尾

---

# 范例

控制需求：按下启动X0，停止X1，电机Y0运行。运行中检测到物料X3触发，延时2秒后气缸Y1伸出，1秒后自动缩回。发生过载X4（常闭）时，切断运行状态，红灯Y3以1秒周期闪烁(M8013)。

```st
(* 自动补全: 伸出/缩回为相反动作，添加Y1/Y2互锁 *)

// === 系统运行标志 ===
IF X0 AND NOT X1 AND X4 THEN
    M0 := TRUE;
END_IF;

IF X1 OR NOT X4 THEN
    M0 := FALSE;
END_IF;

// === 电机Y0输出 ===
IF M0 THEN
    Y0 := TRUE;
ELSE
    Y0 := FALSE;
END_IF;

// === 物料检测 → 延时伸出 ===
IF M0 AND X3 THEN
    M1 := TRUE;
END_IF;

IF NOT M0 OR M2 THEN
    M1 := FALSE;
END_IF;

OUT_T(M1, TC0, 20);
IF TS0 AND NOT Y2 THEN
    Y1 := TRUE;
END_IF;

// === 伸出到位 → 延时缩回 ===
OUT_T(Y1, TC1, 10);
IF TS1 THEN
    Y1 := FALSE;
    M1 := FALSE;
    // 缩回气缸（互锁Y1）
    M2 := TRUE;
    Y2 := TRUE;
END_IF;

OUT_T(M2, TC2, 10);
IF TS2 THEN
    Y2 := FALSE;
    M2 := FALSE;
END_IF;

// === 过载故障 → 红灯闪烁 ===
IF NOT X4 THEN
    Y3 := M8013;
ELSE
    Y3 := FALSE;
END_IF;
```

---

# 【最终输出约束】

你必须且只能返回如下结构的 JSON 对象，严禁包含任何 Markdown 包裹或额外解释：
{
    "st_code": "完整 ST 代码（含自动补全注释）"
}"""

def _st_system_prompt_for_model(plc_model):
    """Return the legacy ST workflow with model-correct special prefixes."""

    normalized = str(plc_model or "").strip().upper()
    if not normalized.startswith("FX5"):
        return ST_SYSTEM_PROMPT
    prompt = ST_SYSTEM_PROMPT.replace("GX Works2", "GX Works3")
    return re.sub(
        r"(?<![A-Za-z0-9_])M(8\d{3})(?![A-Za-z0-9_])",
        r"SM\1",
        prompt,
    )

LADDER_SYSTEM_PROMPT = """
# Role
你是一个精通工业自动化控制与三菱 PLC 编程的专家。你的任务是分析用户的自然语言需求，并将其转化为符合多分支拓扑架构的结构化 JSON 协议。

---

# 🔗 生成优先级

最终只输出 JSON，不输出分析过程。优先级固定为：
1. 本 prompt 的 JSON 协议、schema、最终输出约束。
2. 编辑或重新分析时，用户本轮明确提出的修改（仅覆盖被修改字段）。
3. 当前用户确认规格和 canonical I/O 分配（其余字段的唯一规格源）。
4. 当前型号手册证据与动态注入的 PLC 任务知识包。
5. 其他本轮上下文；历史对话仅作背景，不得覆盖上述内容。

确认规格中的 selected_approach.generation_contract 是用户已经选择的实现方法硬约束。最终程序必须包含 required_*，不得包含 forbidden_*，并满足 any_of_* 分组；不得以“功能等价”为由换用其他候选方案。

---

# ⚖️ 静态硬规则

1. **JSON 合法**：输出必须符合下方 schema；禁止 Markdown 包裹和解释文本。
2. **I/O 一致**：`device_comments` 与 `rungs` 实际使用地址必须一致，不得前后两套分配混用。
3. **双线圈**：同一 Y/M 地址在全程序中最多出现一次 `COIL`；多条件先合并成一个 `parallel_block`。
4. **COMPARE**：表达式内禁止 `+ - * /`；先用 `APP_INSTR` 运算到 D，再比较。
5. **标签长度**：`device_comments` 值和 `label` 均不超过 64 字符。
6. **型号完成标志**：FX3U 使用 M8029、FX5U 使用所选型号资料中的 SM8029/对应状态；完成处理必须与对应定位/脉冲指令保持同 rung 关联，不得跨成无归属的集中判断。
7. **定时器复位语义**：普通 T 定时器只有在使能条件变为 OFF 后才复位。FX3U 的 M8000 在 RUN 期间持续 ON，因此 `M8000 → TIMER` 只能作上电延时，绝不能单独形成闪烁/振荡。周期匹配时优先使用 M8011/M8012/M8013/M8014；否则必须建立会明确断开定时器使能的振荡路径。状态机中 `header_element` 退出当前状态可作为断开条件。
8. **定时器/计数器类型分离**：`TIMER` 的地址只能是 T，`COUNTER` 的地址只能是 C；禁止再用 `TIMER` 表示 C 计数器。
9. **禁止伪 ALT**：不得在同一边沿/同一 rung 下用 `NC Mx → SET Mx` 与 `NO Mx → RST Mx` 两个并联分支模拟翻转；SET 后后续分支会立即看到新值并可能同扫描 RST。需要交替闪烁时使用两个明确相位及各自定时器，或使用已验证且受支持的翻转指令。
10. **OUT 的协议表示**：`OUT` 是降级后的 PLC 指令语义，不是本 JSON 协议中的 `APP_INSTR`。普通 Y/M 输出必须用 `COIL`，T/C 输出分别用 `TIMER`/`COUNTER`；禁止生成 `{"type":"APP_INSTR","opcode":"OUT",...}`。

### FX3U 的 M8029 正例：同一 rung 双 branch
FX5U 不得照抄地址，必须按本轮注入的 FX5U 型号上下文替换为 SM/SD 规则。
```json
{
  "rung_id": 20,
  "debug_note": "DRVA 与 M8029 完成处理同 rung 并联",
  "header_element": {"type": "BLOCK_INPUT", "expression": "= D0 K10", "label": "定位步"},
  "shared_inputs": [
    {"type": "NO", "address": "M0", "label": "系统运行"},
    {"type": "NO", "address": "M20", "label": "定位请求"}
  ],
  "branches": [
    {
      "branch_id": 1,
      "y_offset_level": 0,
      "inputs": [],
      "outputs": [
        {"type": "APP_INSTR", "opcode": "DRVA", "operands": ["D100", "D110", "Y0", "Y4"], "label": "绝对定位"}
      ]
    },
    {
      "branch_id": 2,
      "y_offset_level": 1,
      "inputs": [
        {"type": "NO", "address": "M8029", "label": "定位完成"}
      ],
      "outputs": [
        {"type": "APP_INSTR", "opcode": "RST", "operands": ["M20"], "label": "清请求"},
        {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K11", "D0"], "label": "下一步"}
      ]
    }
  ]
}
```
要点：公共条件只放 `shared_inputs`；定位指令分支 `inputs` 为空；定位指令是该分支最后一个 output；M8029 是下一 branch 的第一个触点。

### M8029 反例：禁止相邻 rung
```text
rung 20: M0 + M20 -> DRVA D100 D110 Y0 Y4
rung 21: M8029 -> MOV K11 D0
```

### 一、 JSON Schema 协议架构规范

你输出的 JSON 数组中每个元素代表一个独立的"状态主梯级（Rung）"：
- `rung_id`: 整数，递增行号。
- `debug_note`: 字符串（可选），用于在需求模糊或自动补全逻辑时，输出简短解释。
- `header_element`: 状态机比较块（如 `{"type": "BLOCK_INPUT", "expression": "= D0 K1"}`），传统非状态机模式下必须为 `null`。
- `shared_inputs`: 列表（可选），位于所有 `branches` 分叉之前的公共串联输入。只允许 NO、NC、P、F、COMPARE、BLOCK_INPUT 等简单输入元素，**禁止 parallel_block**。局部并联块只能放在下方 branch.inputs 中，不能嵌套。M8029 与定位指令并联时，公共使能条件必须放这里。
- `branches`: 列表，包含该梯级下的并联母线分支。内部包含：
  - `branch_id`: 整数，从 1 开始。
  - `y_offset_level`: 整数，从 0 开始。
  - `inputs`: 列表，串联的控制触点流。支持以下组件：
    - 普通常开：`{"type": "NO", "address": "X0", "label": "启动"}`
    - 普通常闭：`{"type": "NC", "address": "X1", "label": "停止"}`
    - 上升沿触点：`{"type": "P", "address": "X2", "label": "刚按下"}`
    - 下降沿触点：`{"type": "F", "address": "X3", "label": "刚松开"}`
    - 比较触点：`{"type": "COMPARE", "expression": "> D0 K100", "label": "值超标"}`
    - 局部并联自锁块：`{"type": "parallel_block", "branches": [ [组件1], [组件2] ]}`
      `parallel_block` 只允许一层，内部不得再次出现 `parallel_block`。
  - `outputs`: 列表，右对齐并联的输出流。仅支持以下六种结构：
    - 普通线圈：`{"type": "COIL", "address": "Y0", "label": "指示灯"}`
    - 上升沿脉冲线圈：`{"type": "PLS", "address": "M0", "label": "脉冲M0"}`
    - 下降沿脉冲线圈：`{"type": "PLF", "address": "M1", "label": "脉冲M1"}`
    - 定时器：`{"type": "TIMER", "address": "T0", "value": "K50", "label": "延时"}`
    - 计数器：`{"type": "COUNTER", "address": "C0", "value": "K10", "label": "计数"}`
    - 泛型应用指令：`{"type": "APP_INSTR", "opcode": "真实应用指令名", "operands": ["操作数1", "操作数2"], "label": "注释"}`；这里不得填写 OUT、说明文字或自由文本。

---

### 二、 经典多模范例（Few-Shot Skill）

【范例 1：包含边缘触发与脉冲输出的综合控制】
{
  "device_comments": {
    "X0": "输入信号",
    "M0": "脉冲输出",
    "M10": "运行状态",
    "D10": "数据校验",
    "Y0": "指示灯"
  },
  "rungs": [
    {
      "rung_id": 1,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [
            {"type": "P", "address": "X0", "label": null}
          ],
          "outputs": [
            {"type": "PLS", "address": "M0", "label": null}
          ]
        }
      ]
    },
    {
      "rung_id": 2,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [
            {"type": "NO", "address": "M10", "label": null},
            {"type": "COMPARE", "expression": "> D10 K50", "label": null}
          ],
          "outputs": [
            {"type": "COIL", "address": "Y0", "label": null}
          ]
        }
      ]
    }
  ]
}

【范例 2：用户明确给出停止的自锁与串联连锁】
{
  "device_comments": {
    "X0": "启动按钮",
    "X1": "停止按钮",
    "Y0": "灯"
  },
  "rungs": [
    {
      "rung_id": 3,
      "debug_note": "停止按钮由用户明确给出",
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [
            {
              "type": "parallel_block",
              "branches": [
                [{"type": "NO", "address": "X0", "label": null}],
                [{"type": "NO", "address": "Y0", "label": "自锁"}]
              ]
            },
            {"type": "NC", "address": "X1", "label": "停止"}
          ],
          "outputs": [
            {"type": "COIL", "address": "Y0", "label": null}
          ]
        }
      ]
    }
  ]
}

【范例 3：红绿灯多段顺序控制】
{
  "device_comments": {
    "M8002": "开机启动",
    "T2": "红灯延时",
    "Y0": "绿灯",
    "Y1": "黄灯",
    "Y2": "红灯",
    "T0": "绿灯延时",
    "T1": "黄灯延时"
  },
  "rungs": [
    {
      "rung_id": 4,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [
            {
              "type": "parallel_block",
              "branches": [
                [{"type": "P", "address": "M8002", "label": null}],
                [{"type": "NO", "address": "T2", "label": "循环触发"}],
                [{"type": "NO", "address": "Y0", "label": "自锁"}]
              ]
            },
            {"type": "NC", "address": "Y1", "label": "互锁"}
          ],
          "outputs": [
            {"type": "COIL", "address": "Y0", "label": null},
            {"type": "TIMER", "address": "T0", "value": "K200", "label": null}
          ]
        }
      ]
    },
    {
      "rung_id": 5,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [
            {
              "type": "parallel_block",
              "branches": [
                [{"type": "NO", "address": "T0", "label": null}],
                [{"type": "NO", "address": "Y1", "label": "自锁"}]
              ]
            },
            {"type": "NC", "address": "Y2", "label": "互锁"}
          ],
          "outputs": [
            {"type": "COIL", "address": "Y1", "label": null},
            {"type": "TIMER", "address": "T1", "value": "K30", "label": null}
          ]
        }
      ]
    },
    {
      "rung_id": 6,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [
            {
              "type": "parallel_block",
              "branches": [
                [{"type": "NO", "address": "T1", "label": null}],
                [{"type": "NO", "address": "Y2", "label": "自锁"}]
              ]
            },
            {"type": "NC", "address": "Y0", "label": "互锁"}
          ],
          "outputs": [
            {"type": "COIL", "address": "Y2", "label": null},
            {"type": "TIMER", "address": "T2", "value": "K50", "label": null}
          ]
        }
      ]
    }
  ]
}

【范例 4：计数器与复位处理】
{
  "device_comments": {
    "X0": "计数脉冲",
    "C0": "计数器",
    "Y0": "输出",
    "X1": "复位按钮"
  },
  "rungs": [
    {
      "rung_id": 7,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [{"type": "P", "address": "X0", "label": null}],
          "outputs": [{"type": "COUNTER", "address": "C0", "value": "K3", "label": null}]
        }
      ]
    },
    {
      "rung_id": 8,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [{"type": "NO", "address": "C0", "label": null}],
          "outputs": [{"type": "COIL", "address": "Y0", "label": null}]
        }
      ]
    },
    {
      "rung_id": 9,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [{"type": "P", "address": "X1", "label": null}],
          "outputs": [{"type": "APP_INSTR", "opcode": "RST", "operands": ["C0"], "label": null}]
        }
      ]
    }
  ]
}

【范例 5：数学偏移后比较】
{
  "device_comments": {
    "M8000": "常开",
    "D100": "基础值",
    "D200": "偏移暂存",
    "D202": "比较阈值",
    "Y0": "输出"
  },
  "rungs": [
    {
      "rung_id": 10,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [{"type": "NO", "address": "M8000", "label": null}],
          "outputs": [{"type": "APP_INSTR", "opcode": "ADD", "operands": ["D100", "K2", "D200"], "label": "偏移计算"}]
        }
      ]
    },
    {
      "rung_id": 11,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [{"type": "COMPARE", "expression": "> D200 D202", "label": null}],
          "outputs": [{"type": "COIL", "address": "Y0", "label": null}]
        }
      ]
    }
  ]
}

【范例 6：复杂并联/嵌套分支】
{
  "device_comments": {
    "X0": "条件A1",
    "X1": "条件A2",
    "X2": "条件B1",
    "X3": "条件B2",
    "Y0": "综合输出"
  },
  "rungs": [
    {
      "rung_id": 12,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [
            {
              "type": "parallel_block",
              "branches": [
                [
                  {"type": "NO", "address": "X0", "label": null},
                  {"type": "NO", "address": "X1", "label": null}
                ],
                [
                  {"type": "NO", "address": "X2", "label": null},
                  {"type": "NC", "address": "X3", "label": null}
                ]
              ]
            }
          ],
          "outputs": [{"type": "COIL", "address": "Y0", "label": null}]
        }
      ]
    }
  ]
}

【范例 7：机械臂复杂步进控制（多 SET/RST 动作与状态转移并存）】
{
  "device_comments": {
    "M4": "系统使能",
    "Y11": "置位夹爪",
    "Y14": "复位顶升",
    "T9": "动作延时",
    "X20": "防呆条件",
    "Y15": "独立复位动作",
    "X12": "动作到位检测",
    "D0": "主状态机"
  },
  "rungs": [
    {
      "rung_id": 20,
      "debug_note": "状态10：展示同分支多输出，以及基于不同条件的任意数量并联分支",
      "header_element": {"type": "BLOCK_INPUT", "expression": "= D0 K10"},
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [{"type": "NO", "address": "M4", "label": null}],
          "outputs": [
            {"type": "APP_INSTR", "opcode": "SET", "operands": ["Y011"], "label": null},
            {"type": "APP_INSTR", "opcode": "RST", "operands": ["Y014"], "label": null},
            {"type": "TIMER", "address": "T9", "value": "K10", "label": null}
          ]
        },
        {
          "branch_id": 2,
          "y_offset_level": 1,
          "inputs": [
            {"type": "NO", "address": "M4", "label": null},
            {"type": "NO", "address": "X020", "label": null}
          ],
          "outputs": [{"type": "APP_INSTR", "opcode": "RST", "operands": ["Y015"], "label": null}]
        },
        {
          "branch_id": 3,
          "y_offset_level": 2,
          "inputs": [
            {"type": "NO", "address": "X012", "label": null},
            {"type": "NO", "address": "T9", "label": null},
            {"type": "NO", "address": "M4", "label": null}
          ],
          "outputs": [{"type": "APP_INSTR", "opcode": "MOV", "operands": ["K11", "D0"], "label": "跳转"}]
        }
      ]
    }
  ]
}

【范例 8：PID 闭环控制、参数初始化与输出限幅】
{
  "device_comments": {
    "M8002": "开机初始化",
    "D500": "PID首地址_Ts",
    "D501": "动作方向",
    "D503": "Kp",
    "D504": "Ti",
    "M8000": "常开",
    "D202": "目标值",
    "D101": "测量值",
    "D102": "输出量"
  },
  "rungs": [
    {
      "rung_id": 16,
      "debug_note": "PID parameter area follows selected PLC manual",
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [{"type": "P", "address": "M8002", "label": null}],
          "outputs": [
            {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K100", "D500"], "label": null},
            {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K0", "D501"], "label": null},
            {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K100", "D503"], "label": null},
            {"type": "APP_INSTR", "opcode": "MOV", "operands": ["K50", "D504"], "label": null}
          ]
        }
      ]
    },
    {
      "rung_id": 17,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [{"type": "NO", "address": "M8000", "label": null}],
          "outputs": [{"type": "APP_INSTR", "opcode": "PID", "operands": ["D202", "D101", "D500", "D102"], "label": null}]
        }
      ]
    },
    {
      "rung_id": 18,
      "header_element": null,
      "branches": [
        {
          "branch_id": 1,
          "y_offset_level": 0,
          "inputs": [
            {"type": "NO", "address": "M8000", "label": null},
            {"type": "COMPARE", "expression": "< D102 K0", "label": "防负溢出"}
          ],
          "outputs": [{"type": "APP_INSTR", "opcode": "MOV", "operands": ["K0", "D102"], "label": "归零"}]
        }
      ]
    }
  ]
}
---

# ✅ 输出前自检清单

在生成最终 JSON 前，只做内部确认，不输出清单：

| # | 检查项 | 通过标准 |
|---|--------|---------|
| 1 | **当前规格优先** | 已使用当前确认规格/canonical I/O；历史缓存没有覆盖本轮修改 |
| 2 | **JSON 结构** | 全新生成为 `device_comments` + `rungs`；增量编辑按 partial 协议输出 |
| 3 | **I/O 一致** | `device_comments` 与 `rungs` 实际地址一致，标签 ≤ 64 字符 |
| 4 | **无双 COIL** | 每个 Y/M 地址最多一个 `COIL`；多条件已合并到一个 `parallel_block` |
| 5 | **硬规则** | COMPARE 无运算；状态可复位；自动补全遵循动态补全规则 |
| 6 | **型号完成标志** | 定位/脉冲完成逻辑使用所选型号的 M/SM 状态并保持同 rung 归属 |
| 8 | **并联块位置** | parallel_block 只在 branch.inputs 中出现；shared_inputs 和并联块内部没有 parallel_block |
| 7 | **方案一致性** | 最终结构、指令和指定软元件完整满足 selected_approach.generation_contract，未混用其他候选方案 |

---

## 🔄 多轮对话增量编辑模式

当用户消息以 `## 当前梯形图JSON` 开头时，说明这是一次**修改请求**（对现有程序的局部调整），而非全新生成。

你可以输出两种格式，根据修改量自行选择：

**格式A — 增量修改（推荐，修改梯级数 ≤ 全部的 50%）：**
```json
{
  "mode": "partial",
  "device_comments": { "M20": "1号分拣执行中" },
  "rungs": [
    { "rung_id": 9, "branches": [...] }
  ],
  "delete_rung_ids": [7, 12]
}
```
- `device_comments`：**仅列出**新增或修改过的注释条目，未变的条目不要列出
- `rungs`：**仅列出**被修改或新增的梯级，每个元素是**完整的**梯级对象（不是 diff），必须含 `rung_id`
- `delete_rung_ids`：要删除的梯级 ID 列表（无删除时省略该字段或传空数组）

**格式B — 完整输出（修改量 > 50% 时降级）：**
直接输出完整的标准 JSON（无 `mode` 字段），与全新生成一致。

**rung_id 规则：**
- 已存在的 rung_id → 替换原梯级
- 新的 rung_id → 插入新梯级（按其 rung_id 排序到正确位置）
- 若新旧梯级的 rung_id 冲突（比如新 rung_id=9 和旧 rung_id=9），以新梯级为准

---

### 【最终输出约束】
1. **全新生成严格双字段**：全新生成时 JSON 最外层必须且只能包含 `"device_comments"`（字典）和 `"rungs"`（数组）。增量编辑模式允许使用上方 `mode:"partial"`、`rungs`、`device_comments`、`delete_rung_ids` 结构。
2. **禁止代码块格式**：严禁使用 ```json 和 ``` 包裹，必须直接输出以 `{` 开头、以 `}` 结尾的纯文本 JSON。
3. **自动步进化**：当需求涉及多阶段顺序执行和延时且未明确指定指令时，必须默认采用状态机步进实现。
4. **纯 JSON 输出**：你必须且只能输出 JSON，不得附带任何解释文本；应用会独立校验返回结构。
5. **GX Works2 声明长度限制**：`device_comments` 中的所有注释值以及各 `label` 字段的文本不得超过 **64 个字符**（含中英文及标点）。GX Works2 的软元件注释字段有 64 字符硬限制，超出将导致导入时被截断或报错。请使用简洁缩写（如"1号分拣转向臂"而非"1号分拣单元的转向臂气缸电磁阀输出"）。"""

def _select_system_prompt(
    target_mode,
    is_edit_mode=False,
    user_requirement="",
    task_type=None,
    review_mode=None,
    plc_model=None,
    confirmed_context=None,
):
    routing_requirement = _routing_text_with_selected_approach(
        user_requirement,
        confirmed_context,
    )
    forced_task = task_type or review_mode
    classification = classify_request(
        routing_requirement,
        target_mode=target_mode,
        is_edit_mode=is_edit_mode,
    )
    workflow_prompt, route = build_workflow_prompt(
        routing_requirement,
        target_mode=target_mode,
        is_edit_mode=is_edit_mode,
        forced_task=forced_task,
    )
    selected_vendor = str(plc_model or route.vendor or "").strip()
    base_prompt = (
        LADDER_SYSTEM_PROMPT
        if target_mode == "ladder"
        else _st_system_prompt_for_model(selected_vendor)
    )
    if target_mode == "ladder":
        from plc_generation_contract import ladder_response_schema
        base_prompt += "\n\n# Machine-readable output schema (authoritative structure)\n" + json.dumps(
            ladder_response_schema(allow_partial=is_edit_mode), ensure_ascii=False, separators=(",", ":"))
    base_prompt = select_base_prompt(base_prompt, target_mode)
    # The selected base prompt already owns role/schema/core platform rules.
    # Keep the dynamic layer focused on matched patterns and examples so the
    # retrieved manual evidence replaces duplication instead of only adding
    # more tokens.
    dynamic_prompt = assemble_prompt(
        classification,
        target_mode=target_mode,
        include_core=False,
        plc_model=selected_vendor,
    )
    route_note = (
        "\n---\n# Prompt precedence\n"
        "Priority is: hard JSON/schema/report shape > explicit current-turn "
        "edits for the fields they change > current confirmed specification "
        "and canonical I/O for all remaining project choices > retrieved "
        "model-manual evidence > routed task patterns > other current-turn "
        "context > conversation history. The confirmed specification and "
        "current explicit edits override older cached or historical assignments.\n"
        f"Detected task_type={route.task_type}, vendor={route.vendor}.\n"
    )
    audit_section("dynamic_prompt", dynamic_prompt, reason="assembled", source="pattern_library")
    audit_section("workflow_prompt", workflow_prompt, reason="workflow_contract", source="workflow_router")
    result = "\n\n".join(
        part for part in (base_prompt, dynamic_prompt, workflow_prompt, route_note) if part
    )
    audit_section("system_prompt", result, reason="assembled", source="api")
    return result

def _routing_text_with_selected_approach(user_requirement, confirmed_context=None):
    """Route generation using both the request and the user's chosen method.

    The confirmed specification is appended to the final prompt later, but
    pattern routing happens earlier.  Without this bridge a user-selected
    state-machine, counter, motion, analog or communication method could miss
    its specialist pattern merely because the original request did not name
    that method.
    """

    parts = [str(user_requirement or "")]
    if isinstance(confirmed_context, dict):
        selected = normalize_approach(
            confirmed_context.get("selected_approach") or {}
        )
        if selected:
            parts.extend(
                [
                    selected.get("name", ""),
                    selected.get("description", ""),
                    selected.get("generation_guide", ""),
                    json.dumps(
                        selected.get("generation_contract") or {},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ]
            )
    return "\n".join(str(item) for item in parts if str(item).strip())

def _load_plc_models():
    """加载 plc_models.json"""
    path = resource_path("plc_models.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _build_model_context(model: str, confirmed_context=None, compact=False) -> str:
    """Build the generation-relevant profile for one PLC model.

    When retrieved manual evidence is available, the large generic M/D lookup
    tables are omitted.  If retrieval is unavailable the complete legacy
    profile is retained, so the offline index is an enhancement rather than a
    new point of failure.
    """
    policy = resolve_context_policy()
    if not policy.legacy:
        # All controlled arms keep the SAME full target profile. Disabling RAG
        # must not silently alter special-device facts supplied to the model.
        compact = False
    models = _load_plc_models()
    m = models.get(model, models.get("FX3U", {}))
    if not m:
        return ""

    profile = {
        "model": model,
        "family": m.get("family"),
        "description": m.get("desc"),
        "addressing": m.get("addressing"),
        "soft_limits": m.get("soft_limits", {}),
        "register_rules": m.get("register_rules", {}),
        "positioning": m.get("positioning", {}),
        "analog_input": m.get("analog_input", {}),
        "analog_output": m.get("analog_output", {}),
        "high_speed_counter": m.get("hsc", {}),
        "notes": m.get("notes", ""),
    }
    if compact:
        profile["manual_evidence"] = "retrieved for the current request"
    else:
        profile["special_m"] = m.get("special_m", {})
        profile["special_d"] = m.get("special_d", {})
    confirmed_hardware = None
    confirmed_hardware_context = None
    if isinstance(confirmed_context, dict):
        candidate = confirmed_context.get("hardware_profile")
        if isinstance(candidate, dict):
            confirmed_hardware = _engineering_hardware_snapshot(candidate)
        candidate_context = confirmed_context.get("hardware_context")
        if isinstance(candidate_context, dict):
            confirmed_hardware_context = _engineering_hardware_snapshot(
                candidate_context
            )
    if confirmed_hardware:
        profile["confirmed_hardware_profile"] = confirmed_hardware
    if confirmed_hardware_context:
        profile["confirmed_hardware_context"] = confirmed_hardware_context
    result = (
        "\n# Selected PLC model profile (authoritative for this request)\n"
        "Use the per-Y capability and output-type notes below. A global "
        "maximum is not permission to use every Y at that frequency. Do not "
        "invent module registers, buffer addresses, or unsupported aliases.\n"
        + json.dumps(profile, ensure_ascii=False, indent=2)
        + "\n"
    )
    audit_section("model_profile", result, reason="legacy_auto" if policy.legacy else "fixed_full",
                  source="model_registry")
    return result

def _confirmed_context_text(confirmed_context):
    if confirmed_context is None:
        return ""
    if isinstance(confirmed_context, str):
        return confirmed_context.strip()
    if isinstance(confirmed_context, dict):
        legacy_context = confirmed_context.get("legacy_context")
        if legacy_context:
            return str(legacy_context).strip()
        clean_context = {
            key: value
            for key, value in confirmed_context.items()
            if not str(key).startswith("_")
        }
        return json.dumps(clean_context, ensure_ascii=False, indent=2)
    return str(confirmed_context).strip()

def _with_confirmed_context(system_prompt, confirmed_context=None):
    context = _confirmed_context_text(confirmed_context)
    if not context:
        return system_prompt
    phase = (
        confirmed_context.get("_context_phase")
        if isinstance(confirmed_context, dict)
        else None
    )
    if phase == "analysis_baseline":
        priority_note = (
            "下面是上一轮确认规格，仅作为本轮分析基线。"
            "本轮最新用户消息中明确提出的修改优先，必须用新值替换对应旧值，"
            "不得因旧规格或历史消息而恢复已被修改的内容：\n"
        )
    else:
        priority_note = (
            "下面是用户刚刚确认的最终规格。它取代当前原始需求、历史消息、"
            "旧确认缓存和旧 I/O 分配中的冲突内容。"
            "io_allocation_raw 是最终唯一 I/O 分配：\n"
        )
    return (
        f"{system_prompt}\n\n"
        "# 当前项目最新确认规格（唯一规格源）\n"
        f"{priority_note}"
        f"{context}"
    )


def build_generation_instructions(
    user_requirement,
    *,
    plc_model,
    target_mode="ladder",
    is_edit_mode=False,
    task_type=None,
    review_mode=None,
    confirmed_context=None,
    current_version_json=None,
    prompt_builder=None,
    knowledge_builder=None,
    profile_builder=None,
    confirmed_builder=None,
):
    """Assemble the established API instructions without model or session I/O.

    The project model is explicit: the API retains its historical config
    fallback before this boundary; an external client uses its bound project.
    Optional builders preserve existing API extension and test hooks.
    """
    prompt_builder = prompt_builder or _select_system_prompt
    knowledge_builder = knowledge_builder or _build_knowledge_context
    profile_builder = profile_builder or _build_model_context
    confirmed_builder = confirmed_builder or _with_confirmed_context
    selected_prompt = prompt_builder(
        target_mode,
        is_edit_mode=is_edit_mode,
        user_requirement=user_requirement,
        task_type=task_type,
        review_mode=review_mode,
        plc_model=plc_model,
        confirmed_context=confirmed_context,
    )
    knowledge_task = task_type or review_mode or ("edit" if is_edit_mode else "generate")
    knowledge_ctx = knowledge_builder(
        user_requirement,
        plc_model=plc_model,
        task_type=knowledge_task,
        confirmed_context=confirmed_context,
        evidence=current_version_json,
    )
    system_prompt = confirmed_builder(
        selected_prompt
        + profile_builder(plc_model, confirmed_context, compact=bool(knowledge_ctx))
        + knowledge_ctx,
        confirmed_context,
    )
    if current_version_json is not None:
        system_prompt = (
            f"{system_prompt}\n\n"
            "# Current version JSON for review/debug context\n"
            "Use this as read-only context unless the user requested an edit.\n"
            f"{json.dumps(current_version_json, ensure_ascii=False, indent=2)}"
        )
    return system_prompt


def generation_user_input(user_input, *, is_edit_mode=False, target_mode="ladder", repair_mode=False):
    """Share the API's existing output discipline without another acceptance gate."""
    if target_mode != "ladder" or repair_mode:
        return user_input
    output_discipline = (
        '输出协议纪律：只返回协议允许的 JSON 字段，不要输出解释性正文。'
        'debug_note 是可选字段，默认省略；不要用 debug_note 记录推理、修改原因或长说明。'
        '已有 device_comments 无必要不要改写。label、debug_note、device_comment 单条文本目标不超过48字符，'
        '硬上限64字符；返回前自行检查字段名和文本长度。\n\n'
    )
    if is_edit_mode:
        return (
            output_discipline
            + '这是对系统提供的 Current version JSON 的修改请求。除非用户明确要求整体重写，'
            '优先返回 mode="partial"：device_comments 只列确实需要修改的注释，rungs 只列修改或新增的完整梯级，'
            'delete_rung_ids 只列需要删除的梯级；不要重复输出未修改梯级。'
            '如果你仍返回完整 JSON，应用也会正常接受，不需要为了格式选择重新生成。\n\n'
            '用户修改要求：\n'
            + user_input
        )
    return output_discipline + '用户要求：\n' + user_input


_PRIVATE_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._-]+[\\/]|/(?:Users|home|tmp|var|etc|private|mnt)/)"
    r"[^\s\"<>|]*"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|agent[_-]?token|operator[_-]?token|gateway[_-]?token|"
    r"access[_-]?token|password|secret)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def public_generation_value(value):
    """Clean an already allowlisted engineering value for external clients.

    Projection must happen before this function: it does not make arbitrary
    project metadata public. Paths and labelled credentials can also occur in
    user annotations or retrieved text, so strings get a final privacy pass.
    """
    if isinstance(value, dict):
        return {key: public_generation_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_generation_value(item) for item in value]
    if isinstance(value, str):
        value = _PRIVATE_PATH.sub("[private path]", value)
        value = _SECRET_ASSIGNMENT.sub("[private credential]", value)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [private credential]", value)
    if value is None or isinstance(value, (bool, int, float)):
        return copy.deepcopy(value)
    return None


def public_generation_ladder(ladder):
    """Project only ladder source fields, never IR or UI/session metadata."""
    if not isinstance(ladder, dict):
        return None

    def element(value):
        if not isinstance(value, dict):
            return None
        result = {key: copy.deepcopy(value[key]) for key in (
            "type", "address", "label", "expression", "value", "opcode",
        ) if key in value and (value[key] is None or isinstance(value[key], (str, int, float, bool)))}
        if isinstance(value.get("operands"), list):
            result["operands"] = [item for item in value["operands"] if isinstance(item, str)]
        if value.get("type") == "parallel_block" and isinstance(value.get("branches"), list):
            # The ladder protocol permits only one local parallel level.
            result["branches"] = [[element(item) for item in branch
                                   if isinstance(item, dict) and item.get("type") != "parallel_block"]
                                  for branch in value["branches"] if isinstance(branch, list)]
        return result

    rungs = []
    for rung in ladder.get("rungs", []) if isinstance(ladder.get("rungs"), list) else []:
        if not isinstance(rung, dict):
            continue
        projected = {key: copy.deepcopy(rung[key]) for key in ("rung_id", "debug_note")
                     if key in rung and (rung[key] is None or isinstance(rung[key], (str, int)))}
        if "header_element" in rung:
            projected["header_element"] = element(rung["header_element"])
        if isinstance(rung.get("shared_inputs"), list):
            projected["shared_inputs"] = [element(item) for item in rung["shared_inputs"]]
        projected["branches"] = []
        for branch in rung.get("branches", []) if isinstance(rung.get("branches"), list) else []:
            if not isinstance(branch, dict):
                continue
            item = {key: branch[key] for key in ("branch_id", "y_offset_level")
                    if key in branch and isinstance(branch[key], int)}
            for key in ("inputs", "outputs"):
                if isinstance(branch.get(key), list):
                    item[key] = [element(output) for output in branch[key]]
            projected["branches"].append(item)
        rungs.append(projected)
    comments = ladder.get("device_comments", {})
    return public_generation_value({
        "device_comments": {address: text for address, text in comments.items()
                            if isinstance(address, str) and re.fullmatch(r"[A-Za-z]+\d+", address)
                            and isinstance(text, str)} if isinstance(comments, dict) else {},
        "rungs": rungs,
    })


def public_generation_specification(specification):
    """Keep the established engineering allowlist and the API's source order."""
    from plc_generation_contract import generation_specification

    def source_order(source, projected):
        if isinstance(source, dict) and isinstance(projected, dict):
            keys = [key for key in source if key in projected]
            keys.extend(key for key in projected if key not in source)
            return {key: source_order(source.get(key), projected[key]) for key in keys}
        if isinstance(source, (list, tuple)) and isinstance(projected, list):
            return [source_order(original, public) for original, public in zip(source, projected)]
        return projected

    return public_generation_value(source_order(specification, generation_specification(specification)))
