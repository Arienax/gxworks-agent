# Q 系列源声明分配与 MAIN 重建快照

日期：2026-09-27。基线提交：`d1f5f3bd8c5cc14a7056590994d555f1d9da917b`。本次固定的是该基线之后的研究源码与原始观察，未提交、推送或发布。环境为 Windows、Python 3.13、本机 GX Works2 和独立 x86 辅助进程；原生编译只使用隔离工程副本，没有 PLC 或仿真器执行。

## 已记录结果

从源工程的声明表、分配范围和调用连接预测地址，再替换源程序中的标签，可以重建 Project2 的 MAIN 和 10 个 SCPI 调用者／分配范围变体的完整 MAIN。11 个结果均与独立原生编译输出逐字节一致；598 个预测分配记录均与原生结果一致。它们来自两个真实项目，不能当作 11 个独立真实项目。将输入声明表中的全部已保存 `UserInfo` 字段替换为 `FE` 后，预测保持不变。

地址预测不读取编译后的分配表，但仍读取源工程已有的声明 `CGTable.dat`。指令宽度修正仍调用 Q 原生公开接口 `CheckInstructionCode2`；因此这不是完全脱离厂商库的编译器。重建不证明 PLC 执行语义或设备运行安全。

原生分配路径的证据覆盖类型尺寸、连续空闲位搜索、位图写入和紧凑分配记录：普通字分配按 D、W 顺序搜索，普通位分配按 M、B 顺序搜索；地址从范围高端向低端安排。每次请求重新搜索前面的存储区。六字 D 区对照中，`rPrm` 占用 D7002～D7005，后续三字 `sPrm` 转用 W，而两字 `crLfStr` 又取得 D7000～D7001。

四字 D 区且无备用字区的失败对照，在 `MAIN/SCPI_1.sPrm` 得到原生诊断 `0x050f1035`；预测也因容量不足拒绝。辅助进程返回零、进度到达 100% 并不代表编译成功，该例保持失败状态。早期内部指令位置挂钩造成的超时和异常输出也保留在包中；成功比较使用函数边界挂钩，并核对有／无挂钩的 PCode 一致性。

当前可移植模型只覆盖本批 Q03UDV 观察到的类型、普通全局／局部声明、数组和有界 SCPI 直接连接形式。R/ZR、保持区、显式地址与自动范围重叠、一般临时变量生命周期及其他功能块连接模板仍拒绝或未建模。证据状态为本批 `native-validated`，未提升为 `production-safe`。

## 固定与核验

- [机器可读清单](../../../research/results/gxw-source-allocation-20260927/manifest.json)：逐文件哈希、原生 DLL 身份、每个输入及候选／原生输出路径。
- [可分发证据包](../../../research/evidence/gxw-source-allocation-20260927.zip)：1,320 个文件，含 SCPI 原始 MIT 许可、当前读取器／辅助程序源码、原生输出和失败记录；不含厂商 DLL 或 EXE。
- 本地补充包：`research/experiments/sfc-graph-20260926/public-corpus-discovery/gxw-source-allocation-local-20260927.zip`，254 个文件。Project2、BoxID 和 Noeul 的再分发许可未确认，原始工程及衍生输出留在本机，身份与哈希进入清单。
- [核验脚本](../../../research/verify_gxw_source_allocation_checkpoint.py)：重新检查归档清单、所有成员哈希、输入身份和已保存的候选／原生 PCode 字节相等性，不运行归档代码或厂商库。

```text
python research/verify_gxw_source_allocation_checkpoint.py
```

本机核验原始结果保存于 [verification.json](../../../research/results/gxw-source-allocation-20260927/verification.json)。缺少本地补充包时，Project2 的比较明确返回 `not-checkable`，不会计为通过。归档源码字节是本次快照的依据，后续工作区编辑不会改变该快照；重做原生实验须使用新的输出目录，保留旧产物。
