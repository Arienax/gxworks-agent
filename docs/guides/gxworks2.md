# GX Works2 操作

## 环境与工程准备

本机 GX 操作需要已登录、可交互的 Windows 桌面及相应 GX 软件。仿真还需要 [Simulator Gateway](../../simulator_gateway/README.md)所列的运行依赖。先完成本地程序和规格检查，再对选定版本执行发送、回读或仿真。

GX 操作由 [GXExecutionCoordinator](../../src/application/execution.py)串行协调，避免多个任务同时操作同一桌面资源。运行期间不要用另一进程并发操作同一 GX 工程。工作区审批方式见[审批策略](../architecture/approval-modes.md)。

## 发送 Ladder CSV

在活动版本选择“发送到 GX”，检查目标 GX 工程和本地版本，先自行备份目标工程，再确认备份提醒。取消提醒不会创建发送提案。发送过程校验 CSV 和目标，导入 MAIN、导入软元件注释并保存工程。

顶部普通 CSV 发送使用手动备份流程：不会先自动导出旧 MAIN 或注释，也不会回读旧工程建立保护性基线。高级同步、MCP 请求、仿真和 FBD 走各自的执行路径。策略字段及默认值由 [gxworks2.import_service](../../src/gxworks2/import_service.py)和[执行服务](../../src/application/execution.py)定义，不要为其他路径套用手动备份标记。

程序导入成功而注释失败时，结果会明确保留这个部分成功状态；先检查 GX 中实际内容再决定下一步。保存失败时按提示手动保存。不要重复点击发送来掩盖前一次结果。

完成导入后，在 GX Works2 中执行编译并检查错误和警告。导入记录、原生编译记录和仿真结果分别保存。空白步号或未知指令形式应按[步宽参考](../architecture/instruction-step-widths.md)检查，不能通过补猜步号消除问题。

## 回读与同步检查

显式“读取 GX”使用原生 CSV 解码和往返校验，接受后保存本地快照。同步检查比较已有基线和两侧程序，检查操作自身不保存 GX 工程或覆盖活动版本。

遇到本地语义目录未覆盖的厂商指令，工作台返回 `unsupported` 并保留现有版本。低层保真读取工具与可编辑程序的校验范围不同；支持范围从对应结果与[指令合同](../architecture/instruction-contract-coverage.md)查询。

## Structured Ladder / FBD

FBD 发送会打开已确认 GXW 的独立副本。随后在 GX Works2 编译、保存，需要回读时重新导入保存后的副本。完整步骤和支持范围见 [FBD 指南](fbd.md)。

## 真实 PLC 只读检查

“工程交付摘要 → 高级维护”提供默认关闭的真实 PLC 只读入口。先配置[独立读取器](../../hardware_reader/README.md)。每次人工授权绑定逻辑站、项目版本、地址白名单和期限，读取结束后关闭连接并记录审计。

这个入口只读取已授权地址，不提供写入、强制、下载或 RUN/STOP 切换。现场操作前确认逻辑站确实指向预期设备；不要把 Simulator Gateway 当作真实 PLC 读取器。
