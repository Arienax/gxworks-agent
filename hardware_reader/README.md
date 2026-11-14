# 真实 PLC 独立只读适配器

本适配器与 [GX Simulator2 网关](../simulator_gateway/README.md) 在源码、构建产物与运行时完全隔离。仿真网关固定面向 GX Simulator2，其写入能力绝对不会引入本适配器。

工作台默认不具备任何物理硬件访问权限。必须由现场操作员在明确知情的前提下，在前端界面手动配置工程版本、MX Component 逻辑站号、现场设备说明、读取地址集合（1–64 个软元件）及有效期限（30–300 秒），并显式提交确认。确认操作本身**不连接物理设备**；仅当操作员后续显式点击“手动读取一次”时，系统才会启动一个短生命周期的独立子进程，执行 `Open → GetDevice → Close` 的单次调用序列。

本适配器**不提供也不支持**写入软元件、强制 I/O、切换 CPU 运行状态（RUN/STOP）、程序下装、CPU 复位、后台自动轮询或静默自动重试。

每次授权会话硬性限制最多读取 30 次；读取前后的元数据及被拦截拒绝的非法请求，均记录在独立于工程之外的工作台私有审计存储中。授权会话与当前工程版本、授权操作员登录身份及适配器二进制哈希强绑定。到期、手动撤销或工作台服务重启均会立即销毁授权凭证。单次读取超时将直接强制终止子进程。历史读取结果附带精确时间戳，仅作为调试辅助观测，不作为实时状态或程序下装凭据。

## 安装与构建

本适配器依赖已在本机正确安装并取得授权的 Mitsubishi MX Component。需预先在 Communication Setup Utility 中配置目标逻辑站号（Logical Station Number）。现场操作员必须严格核实该逻辑站号对应的实际物理接线与设备目标；本适配器不自动校验物理 PLC 硬件身份，亦不会篡改 MX 工具内的任何配置。在授权有效期内，严禁更改该逻辑站的外部通信参数。

在 Windows 环境下通过 PowerShell 构建：

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_hardware_reader.ps1
```

- **架构适配：** 默认编译为 x86 目标，对应 32 位 COM 组件 `ActUtlType.ActUtlType`。若使用 64 位 MX Component，可指定 `-Platform x64` 构建参数，对应 `ActUtlType64.ActUtlType64`。
- **输出路径：** 产物默认生成在用户本地应用数据目录的 `gxworks-agent\hardware-reader`，不向源码仓库提交二进制产物。
- **环境变量：** 可通过设置 `GX_HARDWARE_READER_EXE` 指向由系统管理员统一部署的独立可执行文件。构建脚本不会自动安装 MX Component，亦不会影响现有的 GX Simulator2 配置。

### 支持软元件与并发语义

支持离散的 `X`、`Y`、`M`、`D`、`S`、`T`、`C` 软元件地址。其中 `T` 与 `C` 读取当前值（底层访问 `TN` / `CN`），不表示定时器与计数器的完成触点状态；该接口不接受 `TS`、`TC`、`CS` 或 `CC` 地址。

多次软元件读取在底层按地址顺序遍历观测，**不保证同一扫描周期的全局原子快照**。

### 离线部署检查

执行命令参数 `{"operation":"capabilities"}` 仅检查本机 MX Component COM 类型的注册可用性，不会实例化通信对象或发起任何物理网络连接。该接口可用于自动化离线环境验证。所有物理设备的数据读取，必须由现场操作人员通过严格授权流程逐次手动触发。

## 规范依据

适配器实现遵循三菱电机官方 MX Component 规范的 Utility Setting 控制器标准：
- `ActLogicalStationNumber`：Communication Setup Utility 定义的逻辑站号（范围 0–1023）；
- `GetDevice`：单软元件读取方法。

参考文档：
- [Mitsubishi MX Component Version 4 Programming Manual (SH-081085ENG-T)](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh081085eng/sh081085engt.pdf) 第 3 章（控制属性与方法）与 GetDevice 章节；
- [Mitsubishi MX Component Version 5 Reference Manual (SH-082395ENG-H)](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh082395eng/sh082395engh.pdf) 的 Utility Setting Type 示例与参考规范。

## 架构职责划分与源码映射

- **会话与授权生命周期：** 由 [src/application/hardware.py](../src/application/hardware.py) 统一维护会话计数、期限校验与版本绑定；
- **接口契约与请求校验：** HTTP API 输入模式及字段约束由 [src/integrations/web/schemas.py](../src/integrations/web/schemas.py) 负责；
- **地址策略与软元件转换：** 目标软元件范围校验与当前值转换由 [src/plc/device_policy.py](../src/plc/device_policy.py) 集中处理；
- **Python 进程驱动：** [src/gxworks2/hardware_reader.py](../src/gxworks2/hardware_reader.py) 负责子进程的隔离启动、超时监控与结果解析；
- **原生适配器实现：** [Program.cs](Program.cs) 与 [native_adapters/NativeRequest.cs](../native_adapters/NativeRequest.cs) 联合编译，专职处理与 MX Component 的 COM 交互及错误码转换。

职责边界上，PLC 领域语义、软元件安全策略与会话授权完全由 Python Core 持有；C# 原生适配器仅作为轻量级安全执行端，不维护业务状态，不保留私有缓存。
