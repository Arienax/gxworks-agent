# 语言边界与 Qt 退役策略

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

## Qt：retired（2026-09-21）

Qt 客户端已删除，Web 为唯一继续维护的 UI。`src/ui/`、`src/main.py`、Qt/Win7 依赖、专用构建脚本及打包描述不再保留，不新增兼容壳或第二套界面。

退役前逐项检查独占行为：SFC 图语义已在 `plc/sfc.py`，旧 `.sfc` 文档转图/需求文本也由该模块接管；GXW 解析和原生 CSV 保真读取继续由 `gxw/` 与 `gxworks2/` 提供。会话默认目录不再查询 Qt，但保留原应用标识与显式工作区覆盖顺序，不迁移或删除用户文件。

主动撤销旧“等全部 Qt/Web parity 完成才删除”的发布策略，不把未完成的 Windows/GX/Simulator2 验收勾选为通过。旧 Qt 图形 SFC 画布和 Win7 产品包不再提供；Web 对未知原生指令的现有能力边界不放宽。详细保留/退役清单见 [qt-retirement-audit.md](qt-retirement-audit.md)。

## 本次范围

这是职责迁移，不引入新的校验规则、架构扫描器、自动门禁或新增 CI workflow。已有回归测试可随协议/文件归属更新；不隐藏已有失败，不把模拟后端或构建成功称为真实 GX 编译/设备验证。
