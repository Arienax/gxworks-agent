# Structured Ladder / FBD

## 生成与编辑

新建工程时选择 FBD。可从需求生成候选，也可在对象、连接和声明表中编辑。候选经 GXW 写入和回读校验后生成预览；修改草稿时旧预览会隐藏，避免显示与草稿不符的图。

可新增的节点、调用形式和声明类型从工作台 `/api/fbd/catalog` 查询。实现入口是 [application.fbd](../../src/application/fbd.py)，模板位于 [gxw/templates](../../src/gxw/templates/)。未知调用可随源记录保留，但不能凭名称新增未登记的 ABI。

保存的 FBD 版本提供 `program.gxw`、`fbd.json`、`fbd.svg` 和写入报告。版本、产物及校验结果的关系见[生成交付](../architecture/generation-delivery.md)。

## 导入现有 GXW

选择“导入 GXW”，上传工程并选择目标 POU。文件大小限制与请求字段由 [Web schemas](../../src/integrations/web/schemas.py)和 [application.fbd](../../src/application/fbd.py)定义。保留原工程副本；导入后核对所选 POU、声明和连接。

导入保留其他 POU、元数据及未知记录。工作台没有完整的导入 CPU 识别，项目下拉框中的型号不能替代原工程 CPU 参数检查。导入校验通过后生成新本地版本，原版本保留。

已有 Ladder 可转换为 FBD；转换范围是已有 NO/NC/COIL 串并联结构，原注释作为源 Ladder 数据保留。复杂应用指令或未覆盖节点应使用已登记模板或原生 GX 工程，不应把转换按钮视为通用编译器。

## 原生编译与回读

发送到 GX 时打开版本 GXW 的独立副本。先处理 GX 中已有保存提示，再执行全部编译、保存和关闭。重新导入保存后的文件可以检查原生处理后的结果。

打开文件的结果记录为 `imported`；原生编译结果要在验证页单独记录。操作员填写的结论和附件绑定版本及文件 SHA-256，保留其人工报告来源。FBD 的仿真、诊断和 CSV 同步尚未接通。

模板的历史原生结果、失败样本和后续尝试见[研究记录索引](../../research/README.md)。具体样本的成功只适用于报告中登记的输入、模板和 GX 环境。
