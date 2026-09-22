# 语言与领域边界

## Python Core

PLC 领域规则由无 GUI 的 Python 模块持有。[plc](../../src/plc/)负责 IR、地址、型号、指令、规格和确定性检查；[gxw](../../src/gxw/)负责原生对象、声明及结构化工程语义；[simulator](../../src/simulator/)负责测试 DSL、断言及执行语义；[rendering](../../src/rendering/)把领域模型投影为图形。应用层组织任务、版本、审批和持久化，协议层转换请求与结果。

包级导航由[源码布局](source-layout.md)维护。Core 不导入模型 SDK 或界面组件。

## TypeScript

TypeScript 负责表单、组件状态、选择、缩放、布局、本地化和 HTTP 传输。它展示 Core 返回的可编辑性、权限和检查结果，不按地址前缀、模板名字或控件类型重新判断 PLC 规则。

FBD 编辑意图和仿真步骤交给 Python 应用服务处理；前端保存原始输入及请求状态。接口不可用时显示错误，不在浏览器生成替代程序或默认地址。自动生成的类型属于协议表示，字段来源见 [HTTP 参考](../integrations/http-api.md)。

## C#

C# 负责 COM、SDK、P/Invoke、厂商 ABI、资源释放及原始错误码。Python 准备原生调用计划和设备策略，C# 执行已准备的调用。

共同 DTO 位于 [NativeRequest.cs](../../native_adapters/NativeRequest.cs)；地址和访问规则由 [plc.device_policy](../../src/plc/device_policy.py)维护。Simulator Gateway 与真实 PLC 只读读取器各自保留鉴权、路由和接口限制，构建与协议版本从[网关](../../simulator_gateway/README.md)和[读取器](../../hardware_reader/README.md)查询。

## UI 维护范围

Web 是维护中的 UI，Qt 客户端及 Win7 专用包已退役。旧 SFC 文件的需求转换由 [plc.sfc](../../src/plc/sfc.py)提供；GXW 和原生 CSV 的保真读取保留在对应 Python 模块中。

退役决定及保留清单见[Qt 退役记录](../process/qt-retirement.md)。原生验收结果按版本报告单独登记。
