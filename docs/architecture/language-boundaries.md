# 语言边界与 Qt 冻结策略

状态：已采纳。日期：2026-09-19。

## 单一领域实现

所有 PLC 领域规则由 Python Core 持有。Core 是无 GUI 的领域模块集合，不要求把所有逻辑搬入一个 `core.py`，也不把 HTTP 服务、模型 SDK 或存储类重新命名为 Core。

`plc/` 持有 IR、地址/型号/指令、确定性校验、规格与 SFC 需求解释；`gxw/` 持有 GXW 对象模型、声明语义、FBD 编辑与生成契约；`simulator/` 的领域模块持有测试 DSL、测试默认值、断言解释与执行语义；`rendering/` 把 Core 模型投影为图形。应用层只组织任务、版本、审批与持久化；驱动模块只连接外部软件。

对外的数据类型可以出现在生成的 TypeScript 契约或 C# DTO 中。这是表示数据，不是第二套领域实现。UI 文案、图标、颜色和格式化也不需要强行移到 Python。区别在于：前端可以展示 Core 返回的 `writable`，不能根据地址前缀自行判断可写；可以展示 `symbol_editable`，不能根据模板名字自行判定；可以提交用户输入，不能独立生成指令、声明、PLC 默认地址或断言值。

## TypeScript：presentation

React 持有表单、选择、缩放、分页、请求状态和过期响应处理。FBD 表单向 `/api/fbd/editor` 提交编辑意图，Python 返回模型及声明行/端口投影；原有 `/fbd/preview` 和保存流程仍负责接受草稿。编辑端点不会保存版本、操作 GX 或增加一套程序校验规则。

仿真编辑器从版本接口取得默认方案、采样/等待默认值和断言选项；新增测试/步骤交给 Python；断言输入的字符串转数值及范围拆分从 TypeScript 迁到 Python。原测试 DSL 的保存/执行规则不变。新建工程的默认型号及程序形式由 Core 能力响应提供。

禁止以离线 fallback、界面方便或性能优化为由，在 TS 重写 PLC 规则。HTTP 不可用时报告错误，不在浏览器拼一个“差不多”的程序。

## C#：vendor/native adapter

Python 在调用原生进程前准备 `key/device/value` 调用计划。`key` 用于关联用户可见地址；`device` 是传给 SDK 的名字。例如 T/C 当前值的 TN/CN 转换只在 Core 决定。

Simulator2 网关的原生计划协议为 v3，物理只读 helper 的计划协议为 v2。旧二进制不会理解新载荷，发布时必须同步重建两个 helper；不得静默回退到旧地址语义协议。构建脚本会共同编译 `native_adapters/NativeRequest.cs`。

C# 继续负责 COM 生命周期、MX 原生常量、SDK 调用顺序和原始错误码。Simulator2 路由固定、token、loopback、请求大小限制、原生整数类型及物理 helper 不含写接口等已有边界保留。原有地址/写值/重置范围约束迁入 `plc/device_policy.py`，不是取消。RUN 是否已恢复由 Python 对既有监视地址作出判断。

## Qt：legacy/frozen

`src/ui/desktop/`、`src/main.py`、Qt/Win7 依赖与打包入口保留，但不再承担新功能路线。Web 是主动开发的 UI；无需为 Web 新能力补 Qt parity。

仅接受严重 bug 修复：安全风险、工程/会话数据损坏、崩溃、启动失败或现有关键工作流不可用。共享 Core 接口变更时可做最小适配，不借此新增 Qt 功能。已有 Qt 测试、启动器和包描述不因冻结而删除。

Qt 的 SFC 场景仍由 Qt 绘制；场景到需求文本的解释及地址分配放到 Python Core。UI 提交普通节点/连线数据，不把 Qt 对象传入 Core。

删除 Qt 的前提是 Web 对冻结时需保留的工作流完成覆盖，包括历史工程/版本、设置/语言/附件、需求输入、生成编辑、GX 导入读取同步、仿真回放与报告，并完成 Windows 现场验收。存在未覆盖项时保留冻结版，不宣称已全覆盖。通过后单独删除 Qt 源码、Qt 专用依赖和对应打包入口。

## 本次范围

这是职责迁移，不引入新的校验规则、架构扫描器、自动门禁或新增 CI workflow。已有回归测试可随协议/文件归属更新；不隐藏已有失败，不把模拟后端或构建成功称为真实 GX 编译/设备验证。
