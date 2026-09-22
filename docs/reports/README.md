# 版本与实验报告

报告记录被测提交或样本、日期、运行环境、输入、命令、实际结果、失败及结论。实现入口从[文档索引](../README.md)进入；尝试与未完成方案进入[过程目录](../process/README.md)。

## 运行时与交付

- [2026-09-21 运行时所有权回归](2026-09-21-runtime-ownership.md)：`f2f1781` 与基线的原始 CI 对照。
- [2026-09-21 FX3U 合同覆盖](2026-09-21-fx3u-contracts.md)：该批次审计分母、提升结果和未提升范围。
- [Web 迁移时期的验收](../process/web-migration-checklist.md)：2026-09-10 的包级结果、原生未验收项及后续反馈。
- [Qt 退役审查](../process/qt-retirement.md)：退役前后测试与依赖缺失记录。

## GXW 与原生实验

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
