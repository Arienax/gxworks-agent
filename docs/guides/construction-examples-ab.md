# Agent B 构造范例对照试验

构造范例是可选的 Agent B 上下文，不是新的生成路线。默认关闭。当前范例集只适用于 FX3U；其他型号即使请求开启，也不会收到 FX3U 范例，回执标记 `unsupported_model`。

## 开关

程序调用使用 [`generate_confirmed_ladder`](../../src/application/generation_agent.py) 的关键字参数：

```python
result = generate_confirmed_ladder(
    confirmed_spec,
    plc_model="FX3U",
    construction_examples=True,  # False：关闭；None：读取进程环境变量
)
```

显式布尔参数优先于环境变量。参数不接受字符串 `"false"` 或整数，避免真假值隐式转换。

Web 对照试验使用 **设置 → 开发者** 中的两个选项：

- **Agent B 构造范例**：控制 OFF/ON。
- **从确认规格重新生成（A/B 重跑）**：开启后，即使项目已经有保存版本，也不把当前程序作为 `previous_json/previous_ir` 生成基线；不重新运行 Agent A，直接复用当前确认规格再次进入 fresh confirmed Agent B。结果仍按正常流程保存成新版本。

界面明确显示下一次 Web 生成的范例状态与路径。每个新的 generation job 都会显式携带这两个布尔值，后端在提交时冻结进 job snapshot；之后再切换设置不会改变已经提交或正在运行的任务。Web 因此不再依赖后端进程是否成功继承环境变量。

程序调用仍可直接传 `construction_examples=True/False`。未显式传值的脚本或其他非 Web 入口继续兼容环境变量：

```powershell
$env:GXWORKS_CONSTRUCTION_EXAMPLES = "0"  # OFF
$env:GXWORKS_CONSTRUCTION_EXAMPLES = "1"  # ON
```

开关解析和环境变量后备由 [`resolve_construction_examples`](../../src/application/construction_examples.py) 持有。环境变量也支持 `true/false`、`on/off`、`yes/no`，不区分大小写。非法值会明确报错，不会被静默解释为开启。

范围仍然只是内置确认规格生成的 Agent B；Agent A、外部上下文准备、调试及修复提示词不增加该范例块。

## 范例及格式

[`construction_examples_ir`](../../src/application/construction_examples.py) 返回六个独立的当前 PLC IR 程序：组合条件、停止优先保持、公共使能多分支、边沿与电平、先运算后比较、计数复位。

范例的网络源使用 Core 当前接受的梯级结构，调用 [`build_plc_ir` 和 `validate_plc_ir`](../../src/plc/ir.py) 生成并验证 `plc_program_ir` v3。派生的指令、读写关系、设备表和分析字段不另存一套手写版本。计数器使用当前 `COUNTER` 节点，不沿用旧提示词中的 `TIMER C0`。

发送给 B 时，从已验证 IR 的网络拓扑确定性投影为它当前的 `compact_ladder/1.1` 格式，不切换输出协议。范例是独立参考，地址和数值不是项目分配。没有特殊继电器、特殊寄存器、运动或 PID 范例，也没有按需求关键词强行套用范例的规则。

检查完整 IR（不调用模型、不修改项目）：

```powershell
$env:PYTHONPATH = "src"
python -m application.construction_examples
```

## 对照边界和回执

复用同一份确认规格，在同一提交、型号、模型和模型参数下分别测试两组。先开启 **A/B 重跑模式**，再执行：

1. 构造范例 OFF → 生成一次；
2. 不修改确认规格、不重新运行 Agent A；
3. 构造范例 ON → 再生成一次。

两次都会走 `compact_ladder` 的 fresh confirmed Agent B；已有版本只用于版本历史和结果比较，不进入 Agent B 的编辑基线。原有紧凑协议的最小格式示例在两组中都保留；实验输入的预期差异只有完整构造范例块。

范例在 wire renderer 内、上下文编译和 token 计量前加入。它不进入确认规格、RAG 查询、结构约束或设备分配，也不改变模型参数、重试次数、解码或校验逻辑。范例集合与顺序固定，不增加选择范例的模型请求。开关每次生成只解析一次，编译器重新计量时仍使用同一参考块。

`generation_handoff.construction_examples` 和现有 `on_context` 回调包含 `requested`、实际 `enabled`、`reason`、`pack_version`、范例 ID、IR 哈希、块字符数和块哈希。关闭时块为空；启用且适用时为 `included`。整包 `wire_sha256` 及 `budget_report` 覆盖真正发送的请求，不能只凭界面或环境变量判断注入是否成功。

范例会增加输入长度。原有上下文预算与压缩机制仍然生效；接近窗口上限的任务可能因此需要更多压缩，不保证两组耗时或压缩次数相同。对简单任务做首轮对照时应留足上下文余量，并同时查看压缩回执。

## 验证范围

回归测试归入现有 [`test_generation_agent_boundary.py`](../../tests/test_generation_agent_boundary.py)：关闭路径保持原 wire、开关参数和环境变量优先级、型号适用范围、IR 验证与完整拓扑往返、缓存隔离、实际 B 请求差异、RAG 查询隔离以及最终 token/hash 计量。原生成边界测试保留。

```text
python -m pytest -q tests/test_generation_agent_boundary.py
```

这些检查不等于 GX Simulator2 或真实 PLC 的行为认证，也不证明启用范例一定更快或生成得更好。生成质量、耗时及路线稳定性由相同测试集的多次实测比较。
