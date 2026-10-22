# 模式、结果判读与失败后的选择

## 接入模式决定“提交后发生什么”

| 模式 | 识别依据 | 对生成/编辑结果的处理 |
| --- | --- | --- |
| 独立 local | 服务 instructions 标识独立 MCP；通常暴露 list_projects/create_project/select_project/import_gxw_project/get_project_artifacts | 共享应用事务可保存版本；必须看实际 version_id/save_result；保存草稿可能不激活。 |
| Web service | 服务声明使用本地工程服务的共享运行时 | 可能返回提案；待审批仍是 pending，不能通过操作 HTTP 路由自行批准。 |
| 固定 context | 服务绑定指定工程/版本；instructions 说明只生成候选 | confirmation_required 不等于保存完成；客户端不能绕过应用事务落盘版本。 |

不要仅因看见一个工具名就推断模式；以当前服务说明和结果为准。工程 ID、版本 ID、提案 ID、任务 ID分别保存。

## 按输出层次判读

| 事实 | 何时能报告 | 不能顺带声称 |
| --- | --- | --- |
| 已读取源码 | 读取工具确实返回当前来源与范围 | 原文件可编辑、所有未知指令已理解。 |
| 生成了候选 | 候选工具返回成功及候选内容 | 已保存/激活版本。 |
| 保存了版本 | 真实保存回执包含 version_id/save_result | 已导入 IDE、已原生编译。 |
| 本地检查通过 | 相应本地检查完成且返回通过 | GX Works2 接受、PLC 行为正确。 |
| 原生编译接受 | 本次绑定来源的编译实际完成、接受结果明确 | 公开检查必然完整，模拟器正在运行这一版本。 |
| 保存代码相同 | 返回的资源及代码通道完成比较 | 实物执行对应关系成立。 |
| 运行观察已读到 | 目标身份及实际读取结果存在 | 物理 PLC 状态或程序功能验证。 |

原生 API 的结果字段通常包括 compilation/public_check/diagnostic_projection/current_source_check/inspection。字段缺失、not_run、unverified、incomplete 不是 passed。依据当前 schema 与回执解释，不因为函数正常返回就给出整个任务通过的结论。

## 最短恢复路径

| 情形 | 下一步 | 不要做 |
| --- | --- | --- |
| selection_status=none | 只读任务直接发现来源；需版本时正确 list/select/create/import | 宣称没有打开 IDE，或选择无关工程。 |
| NO_PROJECT_SELECTED / NO_PROGRAM_SOURCE | 使用已暴露的 next_actions 补真实工程/保存 GXW；需要临时分析则走 source_ref | 虚造 ID，把 CSV 或只读快照当保存版本。 |
| 参数错误 | 对照当前 schema 与读取对象修正该次输入；确实缺事实再澄清 | 扩大到无关重构、连续盲目重试。 |
| UNKNOWN_TOOL / 宿主未暴露工具 | 重新查看当前工具目录和模式，找已有等价能力 | 按文档中的名称调用不存在的函数。 |
| 组件启动、安装或协议失败 | 保留实际 code/details/observation/阶段；维护问题与 PLC 源码问题分开 | 把“组件未执行”改写成“工程编译被拒绝”，或假定无 IDE 编译永久不可用。 |
| 编译明确拒绝 | 读取本次真实诊断；按用户授权修正对应源码，再提交一次有理由的修订 | 靠改描述文字、忽略失败步骤或换空工程宣称通过。 |
| 写入结果未知 / 超时 | 查 get_operation_status 或持续工程会话，辨别已经完成的阶段 | 自动重放写入、重复建对象或盲目重复导入。 |
| pending / confirmation_required / execution_consent_required | 报告待处理状态与当前模式要求 | 把 MCP 调用许可当工程审批，修改项目记录绕过流程。 |
| source_ref / session_id / target_ref 失效 | 从所属发现/会话服务重新取得真实标识 | 复用不同类型同名 ID，或猜测新的 ID。 |

新版本可能给出更细的 WORKSPACE_* 错误码与 retryable/recovery 字段。仅当实际返回时使用；本 Skill 的源码快照不能保证这些修复已经进入用户安装的服务。不得用本地化 message 中的某个单词判断整个工具的能力。

## 关键反例

- 用户要“原生编译”时，`compile_project` 名称最像，但实际选择应是 `validate_project(native=true)`。
- 用户要“看当前程序”时，未选择 Agent 工程不妨碍 `list_gxworks2_sessions` 和快照读取。
- 用户要“改现成 GXW”时，不能为了可视化而默认启动 IDE；已保存文件有离线路径。
- 用户要“看运行问题”时，`get_diagnostics` 只是静态分析，不能替代目标状态和实时值。
- 用户要“执行测试输入”时，`prepare_simulator_scenario` 和 trace 的 steps 只是方案/标记，没有执行刺激。
- 用户只要取得已有 CSV/GXW 时，`get_project_artifacts` 不应被替换成重复原生导出。

## 任务完成时

给出本次实际操作、保存版本/产物或视图、编译与检查结果、未执行范围。只读任务无需附带未请求的编译、仿真或资料搜索；写入任务不能在候选阶段就结束并声称已完成交付。
