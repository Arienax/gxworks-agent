# Structured Ladder / FBD

## 生成与编辑

新建工程时选择 FBD。可从需求生成候选，也可在对象、连接和声明表中编辑。候选经 GXW 写入和回读校验后生成预览；修改草稿时旧预览会隐藏，避免显示与草稿不符的图。

可新增的节点、调用形式和声明类型从工作台 `/api/fbd/catalog` 查询；传入 `project_id`、`version_id` 可读取所选工程的目录。编辑器直接使用同一源码上下文返回的目录。实现入口是 [application.fbd](../../src/application/fbd.py)，基础模板位于 [gxw/templates](../../src/gxw/templates/)。新建工程仍使用 FX3U 模板。

[read_project_context](../../src/gxw/object_model.py)绑定当前 GXW 字节、所选 Program、声明和工程 CPU，供目录、v2 对象投影、预览及候选校验共用。自定义 FB 的形参取自该工程的库源码或项目 FB 声明；`IN_OUT` 两侧使用 `in.NAME`／`out.NAME`，端口名、方向和类型由 Core 返回。修改声明后重新解析形参，候选写入后从新 GXW 重建上下文。重命名现有实例会同时更新图形引用和声明；重命名形参会同步更新该端口已有的取反设置和命名连线。

源码与局部声明通过当前工程目录的原生文件夹和文件角色关联，支持 `POU_01.程序.pou`／`POU_01.标签.lh` 这类子对象名；缺失、重复或声明归属不符时返回 `project_source_owner_gap`。原生保存将子对象名规范化为 `Program`／`Labels` 时，[verify_native_save](../../src/gxw/native_write.py)核对流 ID、文件夹和角色，以及源码和声明内容；预览与候选回读使用已确认的保存后对象名。历史中的标签名称可以保留，写入时通过唯一流 ID 关联原记录。

来源缺失或接口不一致的调用保留原始记录并返回 `callable_source_gap`，不能凭名称克隆。未登记但有明确边界的记录按原始字节保留，已知对象仍可局部编辑。无法确定记录边界的输入会被拒绝。

保存的 FBD 版本提供 `program.gxw`、`fbd.json`、`fbd.svg` 和写入报告。版本、产物及校验结果的关系见[生成交付](../architecture/generation-delivery.md)。

带有原生保存状态的工程发生源码或声明变更时，预览和候选保存共用离线工作区适配器。它将原工程复制到隔离目录，核对实际 CPU、编码、所选 POU 和原始源码，再更新并保存；回读核对源码、声明和未编辑的源码对象。失败时不生成候选，也不改写原版本。原生资源占用时返回冲突，沿用共享资源锁。

Windows 源码环境先构建适配器：

```powershell
.\tools\build_workspace_adapter.ps1
```

默认输出到 `%LOCALAPPDATA%\PLC AI Studio\workspace-adapter`，可用 `GX_WORKSPACE_ADAPTER_EXE` 指定可执行文件。GX Works2 安装路径复用 `GXWORKS2_EXE` 和 [GXWorks2Finder](../../src/gxworks2/finder.py)。[WorkspaceSourceSave](../../native_adapters/WorkspaceSourceSave.cs)绑定 32 位原生组件版本 `1.635.0.1`，不自动兼容其他版本。此适配器独立构建，尚未随 Web/MCP 发布包分发。

当前调用计划由 [native_source_plan](../../src/gxw/native_write.py)生成：编辑已有 POU 图形、重命名已有局部声明，增删已验证的根局部声明，以及已验证的 BOOL 到 FB 类型变更。设备绑定、评论、任意初值修改、全局声明或库接口修改尚未接通，不能静默略过。新建 POU 和原生工程创建仍不在此保存入口的范围内。

## 导入现有 GXW

选择“导入 GXW”，上传工程并选择目标 POU。文件大小限制与请求字段由 [Web schemas](../../src/integrations/web/schemas.py)和 [application.fbd](../../src/application/fbd.py)定义。保留原工程副本；导入后核对所选 POU、声明和连接。

导入保留其他 POU、元数据及未知记录。v2 模型的 `cpu` 来自原工程，用于选择源码接口；它不代表运行行为或已通过原生编译，工作台机型选择仍不能替代原工程参数检查。导入校验通过后生成新本地版本，原版本保留。读取已有版本时也从其 GXW 重建投影，旧版 JSON 不用于推断当前接口。

已有 Ladder 可转换为 FBD；转换范围是已有 NO/NC/COIL 串并联结构，原注释作为源 Ladder 数据保留。复杂应用指令或未覆盖节点应使用已登记模板或原生 GX 工程，不应把转换按钮视为通用编译器。

## 原生编译与回读

发送到 GX 时打开版本 GXW 的独立副本。先处理 GX 中已有保存提示，再执行全部编译、保存和关闭。重新导入保存后的文件可以检查原生处理后的结果。

打开文件的结果记录为 `imported`；原生编译结果要在验证页单独记录。操作员填写的结论和附件绑定版本及文件 SHA-256，保留其人工报告来源。FBD 的仿真、诊断和 CSV 同步尚未接通。

离线候选保存不请求公开编译或检查。写入报告分别记录工作区加载、原生保存、保存文件回读；候选的 `gx_compile` 仍为 `not_run`。原生保存内部可能更新厂商缓存，这不构成一次完整编译检查的证据。实例名还受原工程编码等原生约束：例如已有 FX3G／CP1252 库工程的中文实例名保存后冷编译被拒绝；同工程 ASCII 实例名对照通过。不能因 CP936 工程通过中文名称就推广到其他编码。

模板的历史原生结果、失败样本和后续尝试见[研究记录索引](../../research/README.md)。具体样本的成功只适用于报告中登记的输入、模板和 GX 环境。
