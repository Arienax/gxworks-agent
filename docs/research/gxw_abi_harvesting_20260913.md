# GXW Structured ABI harvesting（2026-09-13）

目标是持续采集证据、自动提取结构和控制差异、保守地提升候选置信度。已有 MOV、CMP、ADD_E、AND_E、DIV_E、ABS/ABS_E、TON/TON_E、CTU/CTU_E 仅索引既有证据与模板，不重新采样或改写其历史验证范围。

## 首轮实验预注册

SET/RST 优先使用同一个离线 `X1→Y1` 原生基线。候选假设：H-kind，线圈种类保存在 node kind；H-flag，kind 不变、由 flag/子结构决定；H-other，对应信息在另一个 source/editor stream。逐个在 GX Works2 线圈属性界面改变普通/置位/复位选项，保持器件、bbox、声明和其他节点/连线不变。先保存未编译快照，避免编译缓存混入对象类型对照。

每个候选包含 type variant、direct/coincident-port variant、spaced/explicit-wire variant。后两者只改变目标与其驱动触点的间距和对应连接表达；不同时换器件或声明。逐一执行 native open→Compile All→save→close→reopen。原生界面截图用于对象名称；受控连接用于左侧执行输入角色。无界面或连接证据的右侧 graphic port 保持 unknown，不能因为 code=0 就命名 ENO/OUT。

所有实验离线；不连接或写入真实 PLC。新候选只有通过证据门槛后才注册。缺失原生闭环、formals 或关键控制时，保存原始结构并输出阻塞原因。

## 2026-09-19 成果固化检查点

既有 block/network 研究、双 block writer 和 C2034 消除证据已在提交 `56c8c37`，目标分支已经包含该提交。本次固化保存尚未入库的 ABI 实验材料，不表示 harvesting 总任务完成。

`research/evidence/gxw-abi-checkpoint-20260919.zip` 保存 5 个原先冻结的 GXW、11 张原生界面截图，以及恢复的 RST 工作文件。逐文件 SHA-256、大小和实际观察见 `research/results/abi-checkpoint-20260919/evidence-manifest.json`；全流 SHA/长度、record alignment、字段差分、声明和原始节点见同目录 `structural-controls.json`。

- SET：普通线圈→SET 的控制保持器件、其他节点、wire、声明和 block header 不变；目标 record 仅相对 offset 8 从 kind 5 变为 7。direct/spaced 控制保持目标布局、其他节点、声明及端口拓扑一致。原生两种连接均已观察到全部编译 Error=0、Warning=0、CheckWarning=0，以及保存、关闭、重开。保存前后仍需区分 POU 字节变化和语义等价，完整差分已归档。右侧 graphic port 的语义保持 unknown。
- RST：原生属性选择和编辑器 `(R)` 截图已保存；当前工作文件可解析为 kind 8，但它直到本次检查点才被恢复归档。不能当作当时已冻结的严格控制样本，原生闭环仍为 `native_validation_pending`。
- `harvest_gxw_abi.py` / `build_gxw_abi_catalog.py` 是流水线初稿：结构采集与 SET 控制检查已实际运行；尚缺完整 confidence manifest、负例测试、生产注册及生成工程原生闭环。未提交最终 `gxw_abi_catalog.json`，不得把候选或脚本存在视为 writer 已支持 SET/RST。

下一步先给 SET 建立 hash-bound confidence manifest 和回归负例，再完成 RST 的独立 type/geometry 控制与原生闭环；证据门槛通过后才注册模板，生成工程并重新执行原生闭环。其他目标继续按原优先级采样，不重新研究已有 ABI。

本次复核：两种 SET 样本编译保存后的 POU 都只有绝对 offset 50 的 `01→00`，canonical semantic graph 与连接拓扑均保持相同；不据此给该 header 字段命名。归档内 17 项内容 SHA-256 全部通过。目标分支上运行 `tests/test_gxw_network_blocks.py` 与 `tests/test_gxw_generation_roundtrip.py`，51 项通过；这些回归测试不替代候选 ABI 的原生验证。
