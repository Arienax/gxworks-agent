# 当前代码检查与分配边界阶段快照

日期：2026-09-30。本快照固定 2026-09-27 源声明／SFC 快照之后的实验及当前源码；基线提交、工作树差异、Python、Windows 和本机 GX Works2 DLL 的身份见[清单](../../../research/results/gxw-check-allocation-20260930/manifest.json)。实验使用隔离工程副本和自有 x86 辅助进程，没有 PLC 或仿真器执行。归档本身不重跑原生实验，不提交、推送或发布。

## 当前代码与原生检查的对应

[新鲜检查矩阵](../../../research/results/program-check-matrix-20260930.json)包含 9 个样本、14 个阶段。通过原生调用的只读采样，将输入源码、当前生成的 PCode、检查器实际读取的资源 ID／字节和导出资源对应起来。库体 TRUE／FALSE、两个 SFC 局部修改及 MAIN 源码替换共 5 个正向样本完成检查、导出和重开，形成 10 个接受阶段；重开后生成代码保持一致。

两个重复调用样本的源码模型预测与当前 PCode 完全相同，但新鲜检查拒绝，导出被阻止。双线圈反例在旧调用顺序下检查旧字节并误判通过，随后导出另一份当前代码；更新资源后则读取当前字节并正确拒绝。合计为 10 个新鲜检查接受阶段、3 个新鲜检查拒绝阶段和 1 个旧缓存误接受阶段。编译输出一致不等于完整检查接受。

[资源采样工具](../../../research/probe_gxw_program_check_resources.py)保留实际读取的三个通道、调用位置和资源身份。早期启动失败、ST 末尾 NUL 和 SFC 空代码区导致的分析修正也归档；原始记录不覆盖。历史报告中的 `ProgramCheckCompleted` 需结合当前字节对应关系使用，本快照不追认未经复核的旧结论。

## 源声明分配与尚未闭合的边界

[显式地址对照](../../../research/results/source-allocation-boundaries-20260930.json)保留 10 次原生运行。在当前 Q03UDV 研究模型中修正两条规则：显式全局变量须检查整个占用区间与自动分配区的交集；普通程序局部声明的 device／IEC 地址字段会被原生前端丢弃，模型应保留其来源信息并正常自动分配。DINT 从自动区下界前一字开始的反例被原生以 `0x500f1028` 拒绝，不能仅检查起始地址。

修正后记录了 7 个工程变体的 12 个程序输出逐字节一致，以及 3 个重叠地址拒绝。第一份工程基线仍有 Y21 重复线圈错误；另一来源的 Project2 局部 BOOL 地址对照通过新鲜检查和导出。这里的工程变体不是 7 个独立真实项目；保存文件中的局部地址字段仍在，也不表示它参与编译绑定。

[普通与保持池观察](../../../research/results/gxw-check-allocation-20260930/pool-observations.json)追加 9 个单一工程上的对照。普通字池搜索顺序对应 D、W、R、ZR；R 或 ZR 单独启用时可以分配。但同配 R 和 ZR 时本例只初始化 R，ZR 描述符为空；去掉 R 后才观察到 D、W、ZR 接续使用。因此尚不能把四个地址族直接加入通用回退模型。

保持区参数可初始化独立池：D1 与 D2 同配时本例选 D1，W1／W2 单独配置也可初始化。将源声明 class 改成 6 则被 SICConverter 以 `0x500c5046` 拒绝，尚未产生保持变量分配。编译器分配类别 6／13 选择保持池的静态证据，不代表源声明也使用同样的类别编号。R／ZR 与保持变量均未提升为产品支持。

## 其他已保存成果与失败

[PCode 转梯形图](../../../research/results/pcode-ladder-native-20260928.json)保存真实 FX3G 工程 454 次原生转换调用的重放比较，其中 6 次原调用本身失败，返回码及输出均保持一致。后续单元绑定、布尔图和生成对照的脚本、原始缓冲区、比较结果一并固定；反向梯形图转 PCode 的两次原型均发生 AccessViolation，仍未解决。局部图转换不代替完整工程检查。

从上次快照之后的 ST 文本读取、库源码权威性、FX／Q02 读取实验，以及其失败尝试，也按完整实验目录归档。研究模型保持限定范围；一般临时变量生命周期、条件数值／字符串写回、IN_OUT、数组输出及调用顺序仍需继续验证。REAL 文本显示不等于二进制重编码等价。

## 恢复与复核

- [源码证据包](../../../research/evidence/gxw-check-allocation-20260930.zip)保存当前 Core、研究脚本、原生辅助源码、相关测试及结果清单，不包含厂商二进制。
- 本地证据包是 `research/experiments/sfc-graph-20260926/public-corpus-discovery/gxw-check-allocation-local-20260930.zip`，保留未确认再分发许可的真实工程及其衍生证据。完整恢复需要同时保留此包。
- [冻结脚本](../../../research/freeze_gxw_check_allocation_checkpoint.py)拒绝覆盖已有输出；[清单](../../../research/results/gxw-check-allocation-20260930/manifest.json)记录每个成员的长度和 SHA-256，以及检查读取／预测／重开代码的保存字节配对。

```text
python research/verify_gxw_source_allocation_checkpoint.py --manifest research/results/gxw-check-allocation-20260930/manifest.json
```

[verification.json](../../../research/results/gxw-check-allocation-20260930/verification.json)记录本机核验结果。核验器不导入实验源码，不加载厂商库；缺少本地证据时配对为 `not-checkable`。恢复时按清单取出所需成员，避免覆盖后续工作。下一步先追踪有效源声明到保持分配类别的映射，再用隔离对照检验，不能把此前 class 6 的拒绝当成保持功能不存在。
