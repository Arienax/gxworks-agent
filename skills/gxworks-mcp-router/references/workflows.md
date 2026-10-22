# 调用流程与示例

这些是路由模板，不是已经执行的结果。示例中的 `${...}` 必须替换为实际工具返回值；工具名使用宿主当前暴露的命名空间。未列出的必填参数从实时 schema 取得，不用空对象冒充完整工程命令。

## 1. “解释这个 GXW”，不修改它

`inspect_gxw_project(path=实际服务器侧文件路径)`，或传文件内容的 `data_base64`。两者不能同时传。先读实际 CPU/语言与程序清单；多程序按用户目标选择返回的逻辑程序名。

随后：

```json
{"tool":"get_current_program_info","arguments":{"source_ref":"${source_ref}"}}
```

Ladder 需要一段细节时再用返回的 `network_id` 调 `read_network`；静态问题用 `get_diagnostics` 或 `validate_project(native=false)`。结构化程序使用对应 `read_st_project/read_fbd_project/read_sfc_project`，且只在 schema 接受时传 `source_ref`。

结束条件：解释实际读取内容与覆盖限制。此任务不需要创建 Agent 工程、导入 IDE或原生编译。宿主文件路径不一定是 MCP 服务器文件路径，不可把客户端路径当成服务器必然可读的路径。

## 2. “看看我已打开的 GX Works2 程序”

`list_gxworks2_sessions()` → 按工程名、CPU、会话标识匹配用户目标 → `read_gxworks2_snapshot(session_id=发现结果, program=所需程序)`。

无匹配时报告发现结果；多个匹配且上下文不能消除歧义时才询问。不要因为 `get_current_project` 返回 none 就说“GX 没打开”，也不要随便选一个保存工程继续。

后续只读分析同流程 1。快照产生临时导出，但不保存原工程或创建保存版本。

## 3. “没有打开 IDE，只有程序 CSV 和注释 CSV”

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

文件内容来自客户端时改用各自的 `*_data_base64`。取得 `source_ref` 后直接做只读/静态分析。不先导入 GX Works2，不把 CSV 转述成已经有原生 GXW 保存版本。指令解析、图形预览、语义覆盖分别保留；绘图缺失不自动否定指令已读出。

## 4. “从完整要求生成 Ladder”

当前已有正确工程就复用；独立 local 模式的新项目使用 `create_project(name,plc_model,target_mode="ladder")`。工程选择不明时 `list_projects` → `select_project`，不换掉用户明确的 CPU。

```json
{
  "tool":"get_generation_context",
  "arguments":{
    "user_requirement":"${完整原始工程需求}",
    "workflow":"direct_generation"
  }
}
```

使用返回的 `generation_instructions`、`generation_request`、规格、CPU 和当前程序。工艺事实缺失才澄清；指令语义不确定时 `search_plc_manual`。生成与合同一致的 ladder JSON，然后 `create_program_candidate(ladder=..., generation_context_id=实际ID)`。用途注释遵守工具当前长度/内容规则，不把动作说明塞进设备短名称。

检查实际返回的版本/候选/提案状态。只在有保存回执后取得 `get_project_artifacts()`；不再重复导出已有产物。用户要求原生编译时，确认有绑定的 GXW 再走流程 7；没有 GXW 时说明缺少原生输入，不把 CSV 检查说成原生通过。

## 5. “修改这个 GXW 的一个程序或多个 POU”

独立 local 模式：已有正确保存版本则直接复用；否则 `import_gxw_project` 导入用户文件。该操作会创建保存版本，不与 `inspect_gxw_project` 混用。

`read_gxw_source(program=实际逻辑名称)` 取得当前模型、同工程源码清单和声明。局部 Ladder 可 `patch_program`；多个程序/接口/注释联合修改可 `edit_gxw_project`，显式绑定实际 `version_id`。

已有 GXW 的指令编辑使用当前 `record_index` 与完整 `expected`，而不是按显示步号猜索引。具名 P/I 标签与原始记录身份照实际模型提交。普通文件修改保持离线；只有当前请求明确需要原生保存时使用相应原生操作。

保存策略的 draft/checked 不等于编译器通过。原生验证及仿真独立执行。

## 6. FBD / ST / SFC 的语言路由

### FBD 与项目定义

`get_fbd_catalog` → 按任务读取 `read_fbd_project`、`read_fbd_definition`、`read_fbd_libraries` → `create_fbd_candidate`。

生成与图形编辑优先用目录提供的具名网络、calls、端口、实例和参数 schema。`IN_OUT` 两侧方向分开绑定。不自造“所有 CPU 通用”的函数签名、设备分配或坐标。

只读库用 `read_fbd_libraries`；复制库定义用候选操作 `import_library` 并提供实际 donor 与 `library_name`；不要混成修改调用者源码。已有定义编辑时，省略声明与传空声明列表含义不同，以当前 schema/handler 为准。

FBD 的 `operation=compile` 是当前保存版本的原生编译/检查/保存路线，不同时传源码修改参数；不是任意语言的通用保存捷径。

### 普通 ST

`read_st_project` 读取既有 Program、FB、FUNCTION 的身份、正文、返回类型、局部声明和任务。编辑同一对象用 `create_st_candidate` 对应 edit 操作，或统一多源码编辑工具。

新 Program 使用 `from_candidate`/创建操作的当前合同，`task_binding` 来自实际资源与任务。不能把读取 ST 的工具当作“凭一段文本构造完整 GXW”。需要前台连续写 ST 时改走流程 8，而不是同时在离线版本和活动窗口写同一对象。

### 既有 SFC

`read_sfc_project` → 针对当前 child/graph/declarations 支持范围调用 `create_sfc_candidate(operation="edit")`。确需原生格点才显式 `native=true` 读取；普通读取不额外触发原生组件。现有 SFC 内部对象编辑不代表支持新建工程级 SFC POU/任务。

## 7. “编译一下，不要打开 IDE”

确认当前选中保存版本且包含 GXW。然后：

```json
{"tool":"validate_project","arguments":{"native":true}}
```

需要指定 POU 上下文时可传实际 `program`；编译/检查的实际范围看返回的 `selection`、`targets`，不能仅因传了 program 就宣称只编译该 POU。

不要调用 MCP `compile_project` 代替，也不要自动打开 IDE。读取 `compilation` 的完成与接受状态、诊断、`public_check`、`current_source_check`；原生检查不会保存一个新应用版本。

出现原生组件不可用/协议失配时，记录实际错误与阶段，进入结果恢复指南。未安装的修复不能靠 Skill 生效；修改安装、构建组件或切换服务需要相应开发/维护任务，而不是 PLC 工程工具的隐式动作。

## 8. “在 GX Works2 中展示整个编辑过程”

正确工程 → `execute_native_engineering` 的显式 live start 方案 → 保留返回的持续工程 `session_id`。

后续 `get_native_engineering_session` 取得当前修订/待完成动作，再按当前 schema 提交 continue 与 `controls.expected_revision`。持续会话内读取生成上下文时使用 live；刻意操作保存版本才指定 background。

需要暂停时 `pause_native_engineering_session`，等待当前操作完成。继续待执行动作使用当前协议的 resume 字段，不重放不确定的已发出修改。`rebind` 是核对现有会话，不是重新执行编辑。

同一会话中先编译当前修订，才展示该修订的编译分配地址。后续编辑会使旧编译/地址结果失效。保存、重开是任务中的明确阶段，不为每个图形动作自动重复。

## 9. “变量实际分配到哪里 / 保存代码一致吗”

从当前工程读取准确声明表、POU、成员名或资源名，再选择：

```json
{"tool":"read_native_device_assignments","arguments":{"queries":[{"source":"${实际全局声明表}","name":"${实际变量名}","scope":"global"}]}}
```

```json
{"tool":"compare_native_compiled_code","arguments":{}}
```

```json
{"tool":"locate_native_code_steps","arguments":{"queries":[{"resource":"${实际资源名}","step":12}]}}
```

示例步号 12 仅说明参数类型，真实任务使用实际诊断中的步号。分配、代码比较和定位分别解释；非唯一结果不能补造唯一源码位置。

## 10. “看模拟器 Y0 / 给我一张时序图”

有已连接目标：`list_simulator_targets` → 使用匹配 `target_ref`。没有目标且用户请求包含连接时使用 `connect_simulator`，它连接现有 Simulator2，并非替用户启动/下载程序。

```json
{"tool":"read_simulator_devices","arguments":{"target_ref":"${target_ref}","addresses":["Y0"]}}
```

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

展示工具返回的视图，随后按需 `get_simulator_trace(view_ref)`；结束用 `stop_simulator_trace`。用户要输入刺激方案可用 `prepare_simulator_scenario`，但这不执行写入。运行观察与保存工程的对应关系仍须分别说明。
