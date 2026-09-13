# GXW 工程生成实验

研究说明与置信度标注以以下文档为准：

- [工程写回、A–F 与串联闭环](../docs/research/gxw_project_write_pipeline_20260910.md)
- [声明绑定与 FB 显式连线](../docs/research/gxw_declaration_binding_and_fb_wire_20260910.md)
- [通用声明、跨表扩容与 Web FBD](../docs/research/gxw_declarations_allocation_web_fbd_20260910.md)
- [Structured block 边界与 C2034 消除](../docs/research/gxw_network_boundaries_20260913.md)

`results/regression.json` 索引六组自动比较；`results/gxworks_validation.json` 单独记录实际 GUI 观察，避免将 parser 通过当作编译通过。JSON 中 offset 属于相应逻辑流，CFB 字段变化另行列出。

`evidence/gxw-20260910.zip` 保存 23 个不可变工程快照，`results/evidence_manifest.json` 包含逐文件 SHA-256 和截图 hash。解压到新目录后可以用 `tools/gxw_project.py compare` 重算比较；通过 `reproduce_fb_controls.py` 重建名称、类型和显式连接三个 POU 控制。回归测试校验这三种重建结果与保存工程逐字节相同。

`models/series.json` 是现有 ladder JSON 输入示例。`regression/A.gxw` 是单触点基线；`s.gxw` 是生成结果，`sc.gxw` 是编译保存并重开后的结果。`f2.gxw` / `f2c.gxw` 是 FB 显式连接的对应一对。其余短文件名及失败对照见声明研究文档。

`evidence/gxw-generation-20260910.zip` 是后续声明/生成闭环的独立证据包，清单为 `results/generation-evidence-manifest.json`。`models/two-ton.json` 可直接生成两个显式连接的 TON；`models/declarations.json` 演示局部变量写入。对应 `*-roundtrip.json` 分开记录原生编译观察与 parser 比较结果。

`experiments/` 是忽略入库的本地工作副本目录。原有未保存工程备份 `prior.gxw` 仅留本机，没有加入证据包。writer 使用基线工程或已验证 FX3U 种子及已知对象布局；不自动生成未知声明布局、库或编译产物。

`evidence/gxw-block-boundary-20260913.zip` 保存原生 A/B/C/D/E 及单流 M1 对照；`gxw-block-generation-20260913.zip` 保存双 block relay 生成与原生闭环。`results/block-boundary-20260913/` 包含 knowledge ledger、全流 SHA/size、XML/record/field diff、真值表及原生观察 manifest。`models/relay-parallel-blocks.json` 可由 `tools/gxw_project.py fbd` 生成无 C2034 的已验证逻辑；原始 ladder 输入为 `models/relay-parallel.json`。
