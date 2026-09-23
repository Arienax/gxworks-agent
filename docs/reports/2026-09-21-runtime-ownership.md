# 运行时所有权与配置迁移验证 · 2026-09-21

代码版本：`f2f1781a846c9f7073724b4cca485082d57f414c`。比较基线：`5fd2f7108a628da43c2b5bb06adb26b648bec7a1`。日期按 CI 的 UTC 时间记录。

## 条件与结果

验证使用离线 Provider、临时配置/工作区、真实 Pydantic/Haystack 依赖及固定回放案例。原始全量结果与失败集合保存在[冻结验证记录](evidence/runtime-ownership-20260921.zip)。实现规则见[运行时所有权](../architecture/runtime-ownership.md)。

| 项目 | 基线 | 对应版本 |
| --- | ---: | ---: |
| 全量通过 | 3537 | 3619 |
| 全量失败 | 12 | 12 |
| 跳过 | 5 | 5 |

失败节点集合相同，新增通过 82 项。独立上下文测试通过 154 项；Python 回放和 promptfoo 回放均为 6/6。定向测试与全量测试存在重叠，不累加为独立测试总数。

[Model API Compatibility](https://github.com/Arienax/gxworks-agent/actions/runs/35641077875)、[Confirmed Generation Compatibility](https://github.com/Arienax/gxworks-agent/actions/runs/35641077820)与[Generation Fast Path Validation](https://github.com/Arienax/gxworks-agent/actions/runs/35641077792)成功。[Source Layout Validation](https://github.com/Arienax/gxworks-agent/actions/runs/35641077913)失败：全量无界面测试保留上述 12 项失败，后续 MCP stdio 步骤跳过；其余任务成功。

## 保留的失败节点

- `tests.test_candidate_diff::test_execution_preview_fails_when_its_registered_artifact_is_missing`
- `tests.test_candidate_diff::test_execution_preview_http_contract_is_bound_to_proposal_not_selected_version`
- `tests.test_candidate_diff::test_execution_preview_uses_its_bound_program_artifacts_and_plan[gx_import]`
- `tests.test_change_scope::test_explicit_structural_repair_inherits_original_scope[False]`
- `tests.test_change_scope::test_explicit_structural_repair_inherits_original_scope[True]`
- `tests.test_fx3u_rag::test_st_prompt_uses_only_the_selected_model_special_device_prefixes`
- `tests.test_gxw_lossless::test_all_evidence_manifests_include_nested_archives_and_external_screenshots`
- `tests.test_gxw_lossless::test_recorded_native_failures_are_kept_even_when_parsing_succeeds`
- `tests.test_gxw_network_blocks::test_archived_native_evidence_hashes_are_complete[evidence-manifest.json]`
- `tests.test_gxw_network_blocks::test_archived_native_evidence_hashes_are_complete[generation-evidence-manifest.json]`
- `tests.test_i18n::test_all_marked_ui_templates_have_english_coverage`
- `tests.test_web_fbd::test_approved_gx_import_uses_own_copy_on_com_queue`

## 结论与范围

这些结果支持该版本的模型配置所有权、检索入口约束和可恢复配置迁移回归结论。全量测试尚未全部通过。该组实验未请求真实模型、远程评分器，也未执行 GX 原生编译或 PLC 操作；没有采集真实模型延迟改善数据。

配置迁移验证使用临时数据，包含 SQLite WAL、文件冲突和中断恢复。文件逐个原子发布，通过日志恢复配置/数据库组合；不将其描述为多文件原子重命名。实际配置位置和操作步骤见[模型设置](../guides/model-settings.md#storage)。发布状态按对应提交的 Release 查询。
