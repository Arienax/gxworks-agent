# 调用流程与示例

以下示例中的 `${...}` 均取自上一步工具的实际返回值；工具名称与参数字段以客户端当前连接的 `tools/list` schema 为准。

## 1. 只读分析外部 `.gxw` 文件

调用 `inspect_gxw_project`，传入服务端可读的 `path` 或文件内容的 `data_base64`，获取文件的 CPU 型号、程序清单与 `source_ref`（多程序工程可按清单指定 `program`）。

随后携带 `source_ref` 读取程序结构或静态诊断：

```json
{"tool":"get_current_program_info","arguments":{"source_ref":"${source_ref}"}}
```

- **Ladder**：根据返回的 `network_id` 调用 `read_network`，或调用 `get_diagnostics` / `validate_project(native=false)`。
- **ST / FBD / SFC**：调用对应的 `read_st_project`、`read_fbd_project` 或 `read_sfc_project`（在 schema 支持时传入 `source_ref`）。

## 2. 查看本机已打开的 GX Works2 程序

1. 调用 `list_gxworks2_sessions()` 获取当前打开的 GX Works2 窗口列表。
2. 按工程路径、CPU 或窗口标题匹配目标窗口，调用 `read_gxworks2_snapshot(session_id=..., program=...)` 取得 `source_ref`（存在多个窗口且无法从上下文区分时先向用户确认）。
3. 后续分析同流程 1，直接将 `source_ref` 传给读取与静态诊断工具。

## 3. 分析导出的程序 CSV 与注释 CSV

当用户提供 GX Works2 导出的程序 CSV 与软元件注释 CSV 时，连同明确的 `plc_model` 调用 `inspect_gxworks2_csv_files`（支持服务端路径或 `*_data_base64`）：

```json
{
  "tool":"inspect_gxworks2_csv_files",
  "arguments":{
    "program_path":"${server_program_csv}",
    "comment_path":"${server_comment_csv}",
    "plc_model":"${actual_cpu}"
  }
}
```

取得 `source_ref` 后即可按网络读取指令、查看绘图预览结果并运行静态分析。

## 4. 根据需求新建 Ladder 程序

1. **选择或创建工程**：若当前已选中匹配的工程则直接复用；在 `local` 模式下新建工程可调用 `create_project(name, plc_model, target_mode="ladder")`，切换已有工程用 `list_projects` → `select_project`。
2. **获取生成上下文**：

```json
{
  "tool":"get_generation_context",
  "arguments":{
    "user_requirement":"${完整原始工程需求}",
    "workflow":"direct_generation"
  }
}
```

3. **补充检索与提交候选**：优先使用上下文返回的 `generation_instructions`、规格与指令事实；仍需核对特定指令细节时调用 `search_plc_manual`。按合同生成 Ladder JSON（软元件注释遵守当前长度与格式要求），再调用 `create_program_candidate(ladder=..., generation_context_id=...)`。
4. **查看产物**：版本保存成功后，可调用 `get_project_artifacts()` 获取生成的 JSON、SVG、衍生 ST 与 CSV 路径。

## 5. 离线修改 `.gxw` 中的单个或多个 POU

1. **导入版本**：在 `local` 模式下，若文件尚未导入为工程版本，先调用 `import_gxw_project` 创建保存版本。
2. **读取编辑模型**：调用 `read_gxw_source(program=实际逻辑名称)` 获取目标 POU 的结构、指令记录（含 `record_index`）及同工程源码清单。
3. **提交修改**：
   - 单个 Ladder 网络或原始指令补丁：调用 `patch_program`（GXW 指令级修改需传入当前 `record_index` 与完整 `expected`）。
   - 多源码、接口声明或软元件注释联合修改：调用 `edit_gxw_project`，显式传入当前 `version_id` 与 `edits`（`save_policy` 默认为 `"draft"`，需要严格拦截新增已识别错误时设为 `"checked"`）。

## 6. FBD / ST / SFC 工程读写

### FBD 与项目定义

1. 调用 `get_fbd_catalog` 获取可用功能块、端口方向与 `parameter_schema`。
2. 按需调用 `read_fbd_project`（读取网络与连接）、`read_fbd_definition`（读取工程内结构体与 FB 定义）或 `read_fbd_libraries`（查看用户库）。
3. 调用 `create_fbd_candidate` 提交修改：
   - 生成或修改图形：使用 `generate` / `edit`，优先通过具名网络、`calls`、端口与 `IN_OUT` 配对提交。
   - 导入用户库定义：绑定已保存的目标版本，使用 `operation="import_library"`，传入来源 GXW 的 `data_base64` 与实际 `library_name`。只导入所选库的定义、接口和库内类型；保留目标项目的源码集合、任务绑定、调用者正文及声明，不复制来源工程的调用程序、任务或项目全局声明。新增库定义与目标调用程序分别校验。
   - 原生编译并保存 FBD 工程：对已保存版本调用 `operation="compile"`。

`import`、`edit`、`generate` 的 `native_save` 必须是布尔值，只表示本次调用的选择：显式 `true` 请求原生保存，省略或 `false` 使用离线写入。历史版本曾原生保存不会授权本次保存；源码和声明未变时不会重复原生保存。`create_fb`、`create_program` 和 `import_library` 不接受该选项，继续使用各自的原生创建或导入路径。保存及回读成功仍不表示候选已确认、原生编译通过或运行通过。

### ST 程序与块定义

1. 调用 `read_st_project` 读取既有 Program、FB、FUNCTION 的正文、返回类型、声明表和任务归属。
2. 编辑既有对象时，以读取结果中的准确逻辑身份作为 `source_name`，调用 `create_st_candidate` 的 `edit_st_program`、`edit_st_fb`、`edit_st_function` 或通用 `edit_st_source`；多源码联合修改也可使用 `edit_gxw_project`。
3. 只改正文时省略 `declarations`，保留原声明。需要改声明时传入完整目标列表：给出的列表整体替换原表，未列出的旧声明将删除，列表本身决定名称和顺序；`[]` 表示请求清空，仍须通过现有引用及类型检查。整表替换不与 `remove_declarations`、`renames` 或 `order` 混用。
4. 新建普通 ST Program 时，结合 `get_generation_context` 与工程内实际存在的资源、任务名称构造 `task_binding`，通过 `create_st_candidate` 提交。

例如，只修改已读取对象的正文并保留声明：

```json
{"tool":"create_st_candidate","arguments":{"operation":"edit_st_source","source_name":"${实际逻辑源码身份}","st_text":"${完整目标ST正文}"}}
```

上述 `edit_*` 为后台候选编辑；可见批次和 Live 会话继续按 `execute_native_engineering` 自己的操作集合调用。候选确认、保存回读、原生编译与运行结果分别报告。

### 已有 SFC 工程

调用 `read_sfc_project` 读取既有 SFC 的步骤、转换、动作、分支及子程序（需要原生格点坐标时传 `native=true`），再通过 `create_sfc_candidate(operation="edit")` 或 `edit_gxw_project` 在既有 POU 范围内提交修改。

## 7. 无界面后台原生编译检查

确认当前已选中包含 `.gxw` 的保存版本后，调用：

```json
{"tool":"validate_project","arguments":{"native":true}}
```

如需指定目标 POU 上下文可传入 `program`。从返回结果中查看 `compilation`（编译完成与通过状态）、`public_check`、`current_source_check` 及具体诊断列表。

## 8. 在 GX Works2 中连续可见编辑（Live 会话）

1. **启动会话**：选中目标工程后，调用 `execute_native_engineering` 并传入 `plan.mode="live"`、`session_action="start"`，记录返回的工程会话 `session_id`。
2. **分步推进**：每次继续操作前，先调用 `get_native_engineering_session` 获取当前 `controls.expected_revision` 与待处理状态，再提交 `session_action="continue"`。在持续会话内获取生成上下文时传入 `execution_mode="live"`。
3. **暂停与恢复**：需要暂停时调用 `pause_native_engineering_session(session_id=...)`；步骤中断或超时后，通过 `rebind` 先核对实际界面状态再继续。
4. **会话内编译与地址查询**：在当前修订上执行 `compile` 步骤后，可通过 `show_assignments` 查询编译器分配地址；后续继续修改源码会使此前的编译与地址结果变为 `stale`。

## 9. 查询编译器分配地址、代码一致性与步号定位

基于当前已保存的 `.gxw` 版本，传入工程内实际的声明表名、POU 名或资源名：

```json
{"tool":"read_native_device_assignments","arguments":{"queries":[{"source":"${实际全局声明表}","name":"${实际变量名}","scope":"global"}]}}
```

```json
{"tool":"compare_native_compiled_code","arguments":{}}
```

```json
{"tool":"locate_native_code_steps","arguments":{"queries":[{"resource":"${实际资源名}","step":12}]}}
```

## 10. 读取 Simulator2 软元件与波形时间轴

1. **获取目标**：调用 `list_simulator_targets` 查看已连接目标；若尚未连接且任务需要连接本机 Simulator2，调用 `connect_simulator` 获取 `target_ref`。
2. **读取状态或软元件值**：

```json
{"tool":"read_simulator_devices","arguments":{"target_ref":"${target_ref}","addresses":["Y0"]}}
```

3. **启动波形采样**：

```json
{
  "tool":"start_simulator_trace",
  "arguments":{
    "target_ref":"${target_ref}",
    "config":{
      "signals":[{"id":"output_y0","label":"Y0","address":"Y0","data_type":"BOOL"}],
      "sample_ms":100,
      "duration_ms":10000
    }
  }
}
```

采样期间或结束后可通过 `get_simulator_trace(view_ref)` 查看采样进度、波形数据与时间轴页面地址，需要提前结束采样时调用 `stop_simulator_trace(view_ref)`。
