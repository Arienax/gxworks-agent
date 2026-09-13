# GXW Structured ABI harvesting（2026-09-13）

目标是持续采集证据、自动提取结构和控制差异、保守地提升候选置信度。已有 MOV、CMP、ADD_E、AND_E、DIV_E、ABS/ABS_E、TON/TON_E、CTU/CTU_E 仅索引既有证据与模板，不重新采样或改写其历史验证范围。

## 首轮实验预注册

SET/RST 优先使用同一个离线 `X1→Y1` 原生基线。候选假设：H-kind，线圈种类保存在 node kind；H-flag，kind 不变、由 flag/子结构决定；H-other，对应信息在另一个 source/editor stream。逐个在 GX Works2 线圈属性界面改变普通/置位/复位选项，保持器件、bbox、声明和其他节点/连线不变。先保存未编译快照，避免编译缓存混入对象类型对照。

每个候选包含 type variant、direct/coincident-port variant、spaced/explicit-wire variant。后两者只改变目标与其驱动触点的间距和对应连接表达；不同时换器件或声明。逐一执行 native open→Compile All→save→close→reopen。原生界面截图用于对象名称；受控连接用于左侧执行输入角色。无界面或连接证据的右侧 graphic port 保持 unknown，不能因为 code=0 就命名 ENO/OUT。

所有实验离线；不连接或写入真实 PLC。新候选只有通过证据门槛后才注册。缺失原生闭环、formals 或关键控制时，保存原始结构并输出阻塞原因。
