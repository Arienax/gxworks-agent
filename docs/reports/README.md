# 版本与实验报告

报告记录被测提交或样本、日期、运行环境、输入、命令、实际结果、失败及结论。实现入口从[文档索引](../README.md)进入；尝试与未完成方案进入[过程目录](../process/README.md)。

## 运行时与交付

- [2026-10-06 统一能力发现与三回路报警系统对照](2026-10-06-capability-alarm.md)：共享检索/选择/本地审阅、API/MCP共12个报警旅程、token增量与范围偏离；后续共享条件归并优化及12份实际CSV离线复核。
- [2026-10-06 Direct 一次生成与 ModelScope 教学样本对照](2026-10-06-direct-modelscope.md)：首次24旅程/37次调用、完整失败记录、后续35次旅程尝试/56次响应及1次中断、24份IR/CSV恢复交付与软件验证边界。
- [2026-10-05 分析协议与首次通过率](2026-10-05-analysis-repair.md)：四种 target 合同、局部补丁边界、官方 Flash 首次输出与三组配对、工艺提问缺口及修复未提速的限制。
- [2026-10-05 起保停分析与首次确认入口](2026-10-05-analysis-entry.md)：共享同轮绑定、缺参阶段合同、真实 HTTP 路径、官方 Flash 五轮分析对照及确认回放边界。

- [2026-10-05 Core构造与反例检查](2026-10-05-core-construction-value.md)：保留20份ModelScope结果，以官方预设补52项，72项匿名审阅、接口配对边界、检查器误报修复及离线复核。
- [2026-10-05 ModelScope 手册证据诊断](2026-10-05-modelscope-evidence-value.md)：12例、三组各三轮共108次首次生成，匿名完整程序审阅、救回／弄错、证据交付与缓存计量边界。
- [2026-10-04 ModelScope 复杂需求用户路径](2026-10-04-modelscope-complex-user-path.md)：四份确认规格、构造范例开关对照、独立程序审阅、实际检索质量及操作路径修复。
- [2026-10-04 统一指令定义与效果绑定](2026-10-04-instruction-definition-binding.md)：720 个联合形式、3,925 个形式／型号 owner，67 个形式的独立验收与最终 196 次 DeepSeek 对照，Prompt cache 和 SQLite 旧索引构建修复。
- [2026-10-03 Agent B 操作数用途对照](2026-10-03-agent-b-operand-purpose.md)：ModelScope 两轮各实测 52 次及 CMP 四组 48 次，用途与必要关系的独立增量、剩余错误及验证边界。
- [2026-10-03 FX3U 操作数用途交付](2026-10-03-fx3u-operand-purpose.md)：227 个形式的候选仲裁、全量覆盖、通用解析修复及剩余缺口。
- [2026-09-21 运行时所有权回归](2026-09-21-runtime-ownership.md)：`f2f1781` 与基线的原始 CI 对照。
- [2026-09-21 FX3U 合同覆盖](2026-09-21-fx3u-contracts.md)：该批次审计分母、提升结果和未提升范围。
- [Web 迁移时期的验收](../process/web-migration-checklist.md)：2026-09-10 的包级结果、原生未验收项及后续反馈。
- [Qt 退役审查](../process/qt-retirement.md)：退役前后测试与依赖缺失记录。

## GXW 与原生实验

- [原生局部声明与 FBD 联合更新（2026-10-04）](gxw/2026-10-04-fbd-native-declarations.md)
- [FBD 标签作用域编辑与 CPU 扩测（2026-10-04）](gxw/2026-10-04-fbd-label-scope.md)
- [原生诊断源码映射与技能实测（2026-10-02）](gxw/2026-10-02-native-diagnostic-source-mapping.md)
- [FBD v2 应用贯通与当前代码检查（2026-10-02）](gxw/2026-10-02-fbd-v2-application-native-check.md)
- [临时分配与 ST 参数转移快照（2026-09-30）](gxw/2026-09-30-st-transfer-checkpoint.md)
- [当前代码检查与分配边界快照（2026-09-30）](gxw/2026-09-30-check-allocation-checkpoint.md)
- [源声明重建与 SFC 源图阶段快照（2026-09-27）](gxw/2026-09-27-source-sfc-checkpoint.md)
- [Q 系列源声明分配与 MAIN 重建快照（2026-09-27）](gxw/2026-09-27-source-allocation-checkpoint.md)
- [GX Works2 调用位置、数组与 REAL 转换阶段快照（2026-09-27）](gxw/2026-09-27-callsite-array-real-checkpoint.md)
- [GLM 需求分析 JSON 格式失败与纠正](gxw/2026-09-10-analysis-json-format-repair.md)
- [SQLite device heuristic warning review — 2026-09-13](gxw/device_heuristic_warning_review_20260913.md)
- [Web CSV 发送到 GX：隔离验收（2026-09-13）](gxw/gx_send_fast_path_20260913.md)
- [nested `_hdb` CFB 元数据比较（2026-09-08）](gxw/gxw_cfb_metadata_comparison.md)
- [GXW declaration binding 与 FB 显式连接（2026-09-10）](gxw/gxw_declaration_binding_and_fb_wire_20260910.md)
- [通用声明写入、分配表扩容与 Web FBD（2026-09-10）](gxw/gxw_declarations_allocation_web_fbd_20260910.md)
- [Structured Ladder/FBD block/network 边界研究（2026-09-13）](gxw/gxw_network_boundaries_20260913.md)
- [GXW 工程写回与串联 round-trip（2026-09-10）](gxw/gxw_project_write_pipeline_20260910.md)
- [GXW Structured FB ABI findings: samples 72-75, follow-ups 76-77](gxw/gxw_structured_fb_abi_72_75.md)
- [GX Works2 Structured Function ABI findings: samples 59-66](gxw/gxw_structured_function_abi_59_66.md)
- [GX Works2 Structured Function ABI findings: samples 67-71](gxw/gxw_structured_function_abi_67_71.md)
- [GX Works2 Structured Ladder/FBD write-back validation](gxw/gxw_structured_writeback_validation.md)
- [GX Works2 Structured Ladder/FBD wire-rendering isolation: `history.xml` / `iFileSize`](gxw/gxw_wire_rendering_history_isolation.md)
- [GX Works2 CSV / SVG native reproduction (2026-09-15)](gxw/gxworks2_csv_svg_native_reproduction_20260915.md)

## 记录规则

区分自动测试、隔离 Provider、人工报告、原生编译和真实设备执行。每个结果保留其程序、测试及产物身份，失败和跳过不并入通过数。重叠测试集合不相加为独立覆盖。缺少实测时写明具体未执行项目；发布状态通过对应标签和 Release 查询。
