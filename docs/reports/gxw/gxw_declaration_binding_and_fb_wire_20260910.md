# GXW declaration binding 与 FB 显式连接（2026-09-10）

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

## 结论与范围

**confirmed**：在本机 GX Works2 `1.635.0.1`、FX3U/FX3UC 结构化工程中，FB 调用的实例名和类型名必须与程序局部标签声明一致。仅改 Program.pou 不会使编译器自动增加或修复声明。两个 TON 的 `Q → IN` 可以通过独立 wire 连接，端口不必重合；该工程全部编译、保存重开通过。

**confirmed**：内置 Function MOV 对照在局部标签表为空时全部编译成功，无需类似 FB 的每调用实例声明。**unknown**：自定义 Function/FB、其他库、全局实例及跨 POU 的完整名称解析规则。本研究不将内置 MOV 的结果推广到任意函数。

该实验直接复用此前 Function/FB ABI、几何连接和 history 因果研究。工程写回流程、完整审查清单及串联闭环见 [工程写回记录](gxw_project_write_pipeline_20260910.md)。所有下面的结果来自离线工程副本，没有真实 PLC 操作。

## 实验样本与可复核结果

基线为原始 `76_STRUCT_TWO_TON.gxw`，本地不可变副本 `b0.gxw`；原调用是 `timer_a: TON`、`timer_b: TON`。POU 修改由统一 project writer 完成，声明修改通过 GX Works2 的“局部标签”表完成。每次“转换+全部编译”后的结果单独保存。

| 样本 | 受控修改及关系 | 实际全部编译结果 | 证据 |
| --- | --- | --- | --- |
| b1 → bu | 仅 POU 实例名改为 TON_A / TON_B，声明仍 timer_a / timer_b | 3 errors / 1 warning；含 C2015 未声明 | [差异](../../../research/results/binding_undeclared_compile.json)、[截图](../../../research/evidence/binding-undeclared.png) |
| bu → b2 | UI 中将局部标签名改成 TON_A / TON_B，仍是 TON | 编译前快照；POU 不变 | [差异](../../../research/results/binding_labels_fixed.json) |
| b2 → b2c | 名称和类型已一致，执行全部编译 | 0 errors / 1 warning C2034 | [差异](../../../research/results/binding_declared_compile.json)、[截图](../../../research/evidence/binding-declared-compile.png) |
| t1 → tu | 基于 b2c，仅 TON_A 的 POU 类型 TON → TOF，声明仍 TON | 1 error C2015 / 0 warnings | [差异](../../../research/results/binding_type_mismatch_compile.json)、[截图](../../../research/evidence/binding-type-mismatch.png) |
| tu → t2 → t2c | UI 仅改 TON_A 的声明类型为 TOF，随后全部编译 | 0 errors / 1 warning C2034 | [声明差异](../../../research/results/binding_type_labels_fixed.json)、[编译差异](../../../research/results/binding_type_fixed_compile.json)、[截图](../../../research/evidence/binding-type-fixed-compile.png) |
| fn → fnc | 原始 53_STRUCT_MOV_K10_D1.gxw 的 MOV Function 对照 | 0 errors / 0 warnings / 0 check warnings；局部标签为空 | [差异](../../../research/results/function_compile.json)、[截图](../../../research/evidence/function-empty-local-labels.png) |

表中的成功编译均为 0 check warnings。C2034 是同一 block 中多个梯形图的警告，没有记作完全无警告。bu 中另有 C2010 未连接梯形图、C2020 划线未与母线连接；修复名称后同时消失，不能凭此把三个错误逐个归因到独立 binary field。

`b1c.gxw`、`t1c.gxw` 是操作期间重复使用的工作文件，不作为不可变证据；未声明/类型不匹配的最终快照分别是 `bu.gxw`、`tu.gxw`。证据包和 JSON 均使用这些明确的快照。所有样本 hash、选定 stream 长度/摘要和计数字符串对见 [binding_observations.json](../../../research/results/binding_observations.json)。

## 绑定在哪里、如何关联

| 状态 | 观察及解释 |
| --- | --- |
| **confirmed** | Program.pou 的 FB NODE 已有独立实例名和类型名；该实验没有重新解释该 ABI。 |
| **confirmed** | 当前样本的程序局部声明在逻辑 stream `1.Labels.lh`。仅在 UI 改声明名时，POU 原字节不变，Labels 从 708 变为 700 字节。 |
| **confirmed** | Labels 内出现相邻的计数字符串 `TON_A`、`TON`，以及 `TON_B`、`TON`；改类型后对应变为 `TON_A`、`TOF`。名称和类型可在同一局部声明中共同观察到。 |
| **confirmed** | POU 名称或类型与局部声明不一致均触发 C2015；修复局部声明后可编译。编译失败后的 Labels 不会自行增加 TON_A/TON_B 或把 TON 改成 TOF。 |
| **strongly inferred** | 编译阶段以局部声明的实例标识和数据类型核对 FB 调用；具体内部查找键、大小写和别名规则尚未隔离。 |
| **unknown** | Labels 的完整记录边界、字段含义、其他作用域、状态对象分配与库解析机制。未实现通用 declaration writer，也不靠搜索替换未知 Labels 二进制。 |

以 `b2.gxw` 的 `1.Labels.lh` 为例，offset 86 是 DWORD 字符数 6，后接 UTF-16LE `TON_A\0`；随后 DWORD 字符数 4 和 `TON\0`。`TON_B` 的相邻字符串对从 offset 284 开始。`t2.gxw` 同一区域类型为 `TOF`。这些 offset 是**样本定位信息**，不是生产写入地址；完整 Labels 夹具已保存供后续逐字段实验。

**confirmed**：`Global1.gh` 在该实验实例和类型声明对照中保持相同字节。它不承担该实验局部实例的修改；这不证明 FB 永远不能全局声明。`MAIN.res` 在 b2 编译前为 98 字节，b2c 编译后为 854 字节，是该实验观察到的编译派生变化；writer 没有伪造它。其他 Labels 字节、logic 文件及 XML 的实际变化均由比较器记录，未猜测字段含义。

**unknown**：TOF 的完整独立 ABI。该实验只对 TON_A/TOF 同端口布局做最小类型绑定控制，不向 ABI 注册表添加未经充分研究的 TOF 规则。

**confirmed**：t2 → t2c、fn → fnc 编译保存时，POU 的 offset 50 / 0x32 从 01 变为 00，record 字节和语义保持不变。**unknown**：该 header 字节的完整语义；不能将其命名为已确认的编译状态字段。它由 GX Works2 改写，project writer 仍保留源值。

## FB 显式 wire 实验

成功样本 `f2.gxw` 基于 b2c，保留 TON_A、TON_B 及各自 PT/ET 端子，移除中间 Y1/X2 端子并移动对象；保留首端 X1、末端 Y2。使用已知 44 字节 wire 模板建立：

```text
TON_A.Q (13,4) -------- (21,4) TON_B.IN
```

这两个端点相距 8 个网格，没有端口重合。坐标用于定义实验图形，不是长度阈值或分配规则。

| 项目 | 实际结果与状态 |
| --- | --- |
| source | **confirmed**：TON_A，Q，port index 2，已有 kind code 0，坐标 (13,4) |
| sink | **confirmed**：TON_B，IN，port index 0，已有 kind code 1，坐标 (21,4) |
| wire | **confirmed**：独立 44 字节 record，生成 POU 内 offset 807 / 0x327，端点如上 |
| 对象数 | **confirmed**：10 nodes / 1 wire → 8 nodes / 2 wires；另一 wire 是保留的母线 |
| GX Works2 | **confirmed**：f2c 全部编译 Error 0 / Warning 0 / CheckWarning 0，保存并关闭重开 |
| round-trip | **confirmed**：f2 与 f2c 的 Program.pou 字节完全相同；devices/topology/wires/semantic_graph/unknown_records 比较全部一致 |
| object reference | **confirmed**：现有解析器依据端口坐标和显式 wire 还原连接；报告的 node_offset/wire_offset 仅标识当前 POU 内记录 |
| wire 隐藏引用 | **unknown**：未知 wire 前缀是否在其他布局含对象 ID 等引用。本实验不为这些字节赋予新含义 |

证据：[写入报告](../../../research/results/fb_explicit_data_write.json)、[round-trip](../../../research/results/fb_explicit_roundtrip.json)、[编译截图](../../../research/evidence/fb-explicit-compile.png)、[重开截图](../../../research/evidence/fb-explicit-reopened.png)。

失败对照 `f1 → f1c` 将普通 contact/coil 与 FB 数据端口混合，全部编译出现 C2028、C2009。该尝试同时改变母线和多处连接，没有隔离唯一原因，因此**unknown**：是哪一处连接触发该错误；不将它推广为“FB 不能显式连线”。[失败报告](../../../research/results/fb_mixed_rejected_compile.json)、[截图](../../../research/evidence/fb-mixed-ladder-rejected.png) 一并保留。`fb_explicit_write.json` 对应此失败对照，成功案例是 `fb_explicit_data_write.json`。

## Connectivity model 与复现实验

[fb_connectivity.py](../../../src/gxw/fb_connectivity.py) 复用现有几何 net 与语义模型，输出 FB source/sink、formal、port index/kind、坐标、当前记录 offset、整条 conductive net 的 wire offsets；区分重合端口与 wire network，并暴露多驱动和未知 formal。这里没有引入新的 PLC IR，也没有发明序列化 object reference。wire 列表是整个 net 的成员，不能当作唯一最短路径。

回归夹具验证删除该 wire 后 Q→IN 关系消失，错移到相邻行会变成 ET→PT，避免只比较“有两个 FB”而漏掉端口错配。录制夹具测试不等于重新运行 GX 编译；GUI 结果单独保存在 [gxworks_validation.json](../../../research/results/gxworks_validation.json)。

[reproduce_fb_controls.py](../../../research/reproduce_fb_controls.py) 可从证据包重建三种 POU 改动；所需局部声明基线来自保存的 GX 文件，脚本不自动修改声明：

```powershell
.venv\Scripts\python.exe research/reproduce_fb_controls.py rename b0.gxw -o renamed.gxw --report renamed.json
.venv\Scripts\python.exe research/reproduce_fb_controls.py type b2c.gxw -o typed.gxw --report typed.json
.venv\Scripts\python.exe research/reproduce_fb_controls.py connect b2c.gxw --wire-template s.gxw -o connected.gxw --report connected.json
```

原始及编译后工程保存在 [gxw-20260910.zip](../../../research/evidence/gxw-20260910.zip)，逐文件 hash 见 [evidence_manifest.json](../../../research/results/evidence_manifest.json)。解压到新目录即可复核，A–F 原始工程也包含在内。本地可编辑工作目录 `research/experiments/` 已忽略入库，原文件没有删除。
