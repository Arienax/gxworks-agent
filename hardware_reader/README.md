# 真实 PLC 的独立只读接入

本适配器与 [Simulator Gateway](../simulator_gateway/README.md) 分开编译和运行。后者固定在
GX Simulator2，模拟测试写入能力不会进入本适配器。

工作台默认没有有效硬件授权。操作员先选择工程版本、MX 逻辑站号、现场设备说明、
1–64 个地址和 30–300 秒期限，再审查并明确确认。确认本身不连接设备；每次点击
“手动读取一次”才会启动一个独立进程，执行 `Open → GetDevice → Close`。
不提供写设备、强制、CPU 运行状态切换、下载、复位、后台轮询或自动重试。

每个会话最多读取 30 次；读取前后及被拒绝的请求均记录在工程之外的工作台私有状态中。
会话绑定工程版本、创建它的操作员登录及适配器内容哈希。到期、撤销和服务重启使授权失效。
读取超时会终止单次进程。历史读取结果保留时间戳，不作为实时值或程序下载证据。

## 安装

使用已安装、授权的 Mitsubishi MX Component，并在 Communication Setup Utility
配置逻辑站。操作员必须核对逻辑站实际对应的现场设备；适配器不会核验物理设备身份，也不锁定或修改 MX 工具中的配置。授权有效期内不要更改该逻辑站的外部配置。

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_hardware_reader.ps1
```

默认构建 x86，适用于 32 位 `ActUtlType.ActUtlType`。64 位 MX Component 可使用
`-Platform x64`，对应 `ActUtlType64.ActUtlType64`。输出默认在用户本地应用目录的
`PLC AI Studio\hardware-reader`。可通过 `GX_HARDWARE_READER_EXE` 指向管理员安装的
独立适配器。不会自动安装 MX Component，也不会更改已有 Simulator2 配置。

支持单独的 X、Y、M、D、S、T、C 地址；T/C 表示当前值，底层读取 TN/CN，
完成位应使用独立的状态定义。结果是各地址顺序读取的观测，不保证同一扫描周期快照。

`{"operation":"capabilities"}` 只检查本机 COM 类型注册，不创建对象或连接设备。
它可用于离线部署检查。测试只使用临时工程、模拟读取器和此能力查询；设备读取必须
由现场操作员通过上述流程另外授权。

## 接口依据

实现使用 MX 的 utility setting type 控制器：`ActLogicalStationNumber` 是
Communication Setup Utility 的逻辑站号，范围 0–1023；`GetDevice` 读取软元件。
参见 [Mitsubishi MX Component Version 4 Programming Manual，SH-081085ENG-T](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh081085eng/sh081085engt.pdf)
的控制属性和 GetDevice 章节，以及
[MX Component Version 5 Reference Manual，SH-082395ENG-H](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh082395eng/sh082395engh.pdf)
的 utility setting type 与示例程序章节。

## 实现与字段来源

会话、次数、期限和版本绑定由 [application.hardware](../src/application/hardware.py)维护；HTTP 输入字段及校验在 [schemas.py](../src/integrations/web/schemas.py)。地址及 T/C 当前值映射由 [plc.device_policy](../src/plc/device_policy.py)准备，Python 调用方为 [HardwareReader](../src/gxworks2/hardware_reader.py)。

[Program.cs](Program.cs)只执行供应商读请求，与 [NativeRequest.cs](../native_adapters/NativeRequest.cs)共同编译。升级时使用上述构建脚本重新构建读取器；协议及 COM 参数以这两个文件的定义为准。语言分工见[架构边界](../docs/architecture/language-boundaries.md)。
