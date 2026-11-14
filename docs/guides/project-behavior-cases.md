# 工程行为用例

在工程目录下与 `plc-spec.json` 并列保存 `acceptance-cases.json`。其中 `spec_revision` 使用读取工程规格时返回的 `base_revision`。工程还没有确认规格时填 `null`。修改规格后，核对原用例是否仍适用，再更新用例和修订号。

```json
{
  "schema_version": 1,
  "project_id": "替换为工程 ID",
  "plc_model": "FX3U",
  "spec_revision": null,
  "cases": [{
    "case_id": "start_output",
    "scan_ms": 10,
    "steps": [
      {"at_ms": 0, "set": {"X0": 0}, "expect": {"Y0": 0}},
      {"at_ms": 10, "set": {"X0": 1}, "expect": {"Y0": 1}}
    ]
  }]
}
```

任务提交时读取这份文件，生成提示和本地执行检查使用同一份内容。没有工程文件时可使用 `GXWORKS_ACCEPTANCE_CASES_FILE`。文件中的工程、型号或规格修订不匹配时，检查结果会说明原因。

每个用例独立运行，步进时刻必须从 0 开始并与 `scan_ms` 对齐。`set` 的输入值会保持到下一次更新。`expect` 记录该时刻的观测值。用例可以通过 `execution_context` 指定 `cpu_version` 和有型号资料支持的 `clock_phases_ms`。这些值只描述本次执行检查。资料不足或超出检查时间时，该用例仍为未验证。

对照脚本的用例数据可提供 `acceptance_cases`，由脚本保存为当前工程的用例文件。`held_out_cases` 在候选保存后单独执行。
