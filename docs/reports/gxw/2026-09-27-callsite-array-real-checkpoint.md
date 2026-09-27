# GX Works2 调用位置、数组与 REAL 转换阶段快照

日期：2026-09-27。代码基线：`d1f5f3bd8c5cc14a7056590994d555f1d9da917b`；其后的调用输入读取器修订和用例以证据包内源码字节为准。运行环境为 Windows、本机 GX Works2、独立 x86 辅助进程和 Python 3.13。全部新编译均在隔离工程副本中离线执行，没有运行 PLC、仿真器或真实设备。

## 已固定的读取能力

功能块的最终编译表不能代表所有调用位置。重复调用同一实例时，后一次调用既能覆盖引脚地址，也能把其分配形式从紧凑分配改成内嵌设备描述符，或反向改变。仅按实例名、POU 名或最终 `UserInfo` 还原早先调用，会得到错误地址。

现在按原始调用片段、对应的调用前代码区和独立解码的功能块代码逐项核对。`research/probe_gxw_callsite_inputs.py` 从调用前代码取得实际复制目标，识别已观察到的直接 BOOL/D 设备别名；最终分配表只保留为对照数据。表达式、端口类型或原生指令组不匹配，以及存在未消费的指令组时，结果保持 `opaque-preserved`，部分候选不得作为可用绑定。

SCPI 样本来自 `rain-yukizora/SCPI_FB`，源树为 `172dc7a69efed8b614e86dc4a54d00708f091904`，工程 SHA-256 为 `50cf82bf4c19b0a06b004b39f4402f587cf358d2e2df411e6cd7d3ace2931d7a`。工程沿用原始 MIT 许可。控制样本修改调用者，保留 SCPI 功能块源体和声明；原生导出对已观察的信封字节 50 作规范化，差异单独记录。

| 对照 | 实际结果 |
| --- | --- |
| 两个实例、同实例两次调用、复合 BOOL 条件三组已有样本 | 6 处展开各 163 组源记录与原生结果一致；同实例第一处的 19 组最终表地址差异通过调用位置连接恢复 |
| 复合 BOOL 与直接输入，两种调用顺序 | 4 处展开各 163 组一致；第一处分别恢复 19 组地址差异 |
| INT、DINT、STRING 的字面量／设备输入，两种调用顺序 | 12 处展开各 163 组一致；第一处分别恢复 20 组地址差异 |
| 8 个新增控制中的调用前代码交换 | 16 个错误对应均被拒绝 |

这些是一个真实功能块上的调用者变体，共享同一组 163 条源记录；不能算作 22 个独立真实工程。这里的“一致”覆盖有序源记录、标签替换、对应原生指令与操作数，不证明控制流执行、间接目标值或设备运行安全。

另一个反例是完全消除的调用输入：原生 DebugInformation2 保留仅含 `(0,0,0,-1,7,-1)` 的标记表，父区间起止与功能块入口重合。这个单点不能当作父区间中的一条机器指令。两个 INT 控制保存了该情形；当前实验按标记表、原始调用位置和功能块边界联合识别。

`research/probe_gxw_operand_bindings.py` 保留具体实例 POU 的成员归属，读取成员自身的绝对分配，不按同名 POU 合并或叠加实例基址。Simple Ladder 标签支持已观察的 ASCII 大小写差异和有界十进制数组下标。`UserInfo` 标签 7 的内嵌 25 字节设备描述符由 ECCompiler `0x51680..0x516C9` 分支确认；当前投影仅覆盖本批观察的 M、D、X、SM 标量形式。其他标志继续保留原始字节。

## 一并冻结的前序原生证据

数组和调用转移的读取器位于 `research/probe_gxw_index_arithmetic.py`、`research/probe_gxw_template_operands.py` 和 `research/probe_gxw_call_transfers.py`。12 个 packed BOOL 输出重写已将原始 AST、CG 表与独立原生指令解码对齐，其中包括直接、索引及 AND/NOT/OR/XOR 使能表达式。读取器验证使能快照、地址计算和条件回写；有限位宽溢出、运行时下标范围与实际执行仍未证明。

PLS 库体反事实进一步区分了文本与编译器内建行为：四种调用目标中，将 Q `PLS_M` 模板体的 PLS 改成 PLF，会改变三个调用，动态 packed BOOL 输出仍生成 PLS。把同一已修改定义复制为另一个名字后，最后一个调用也改成 PLF。原生保存后库体存在并不能证明所有调用都执行该库体；名称相关的特殊分支仍是必要证据。

REAL 的 13 个工程字面量完成了 CG binary64 常量、原生描述符、CRT 文本转换和最终 binary32 token 的逐级对照。路径包含 `_gcvt(value,15)`、重新解析、旧 CRT 的 `%+7.6E` 舍入及范围检查。直接 binary32 转换只有 2/13 与原生结果一致，Python 默认格式化只有 11/13。`16777217.0` 和负零等反例保留在原始结果中。

独立标量 CRT 辅助程序又保存了 1,178 个邻域样本；基于精确二进制值的可移植 15 位十进制舍入候选仍有 4 个反例，因此没有发布通用 REAL 转换规则。`1.1754944E-38` 在七位有效数字舍入后低于原生正常 binary32 下限，被原生编译拒绝；失败输入、返回值和诊断没有并入通过数。

上述 BoxID 衍生实验的完整工程、原始日志与失败保留在本地证据包，因来源树未发现许可，不进入仓库分发包。来源身份与本地包哈希均进入清单。

## 证据与复跑

- [机器可读清单](../../../research/results/gxw-checkpoint-20260927/manifest.json)：两个证据包、逐文件 SHA-256、基线和结论计数。
- [SCPI 原始证据包](../../../research/evidence/gxw-callsite-inputs-20260927.zip)：502 个文件，包含原始许可、工程副本、原生返回码、编译表、DebugInformation2、PCode、独立解码结果、未修正与修正后的比较及对应脚本源码。
- [本机原生环境](../../../research/results/gxw-checkpoint-20260927/native-environment.json)：实际 DLL 版本与 SHA-256；厂商 DLL、EXE 不入包。
- [调用输入用例](../../../tests/fixtures/gxw_callsite_inputs.json)：16 处原生调用观察、原始片段、调用前代码、最终分配和错误调用者对照。
- [测试原始输出](../../../research/results/gxw-checkpoint-20260927/pytest-compiler.txt)：33 passed，其中新增参数化用例 19 个；其余 14 个属于既有编译器读取测试。

```text
python -m pytest -q tests/test_gxw_compiler.py
```

该命令只回放归档观察，不加载 GX Works2 DLL。原生编译与纯 Python 测试分开计数。SCPI 包按仓库相对路径保存；在独立的基线检出目录解包后，实验脚本入口位于 `research/experiments/sfc-graph-20260926/public-corpus-discovery/`。`probe_scpi_repeated_complex_enable.py` 和 `probe_scpi_repeated_value_inputs.py` 是新增控制的创建脚本，要求新的输出目录；`correlate_scpi_operand_bindings.py` 接受 `--output-dir`，后续比较由 `correlate_scpi_complex_callers.py` 执行。归档目录用于核验，重新编译应使用单独副本，保留历史失败和输出。

这是研究读取能力的快照。产品 Core 的地址策略、审批、资源锁和原生写入许可没有放宽；研究模块没有接入生产写回路径。下一步优先验证带条件的数值／字符串连接，以及这类连接在重复调用时的值复制与地址引用差异。
