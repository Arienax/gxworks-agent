# Structured Ladder/FBD block/network 边界研究（2026-09-13）：后续实验

来源版本：`f2f1781`。已完成的结果见[Structured Ladder/FBD block/network 边界研究（2026-09-13）](../../reports/gxw/gxw_network_boundaries_20260913.md)。

## 采样前的可证伪假设

| ID | 假设 | 排除方法 |
| --- | --- | --- |
| H-record | block 由 node/wire 列表内新的 record class、node kind 或 separator 表示 | 对 B/C 做完整 record alignment，检查所有插入/删除字节是否属于其他层级 |
| H-envelope | Program.pou 在 record 列表外有每 block 的 envelope、长度和计数 | C/D 完整重排 envelope，且 block 内记录独立保持；受控 B→C mutation |
| H-other | block 边界只在其他 source/editor logical stream 中 | Program.pou 单流移植能改变 block 数并消除 C2034 时排除“只在其他流” |
| H-geometry | block 仅由现有 wire/rail/节点几何推导 | 保持逻辑图、对齐 block 局部坐标，观察非几何结构及移植结果 |
| H-identity | 候选 block DWORD 是稳定 identity | 交换 block 顺序，比较数值跟随内容还是位置；不能只由两个相等常量下结论 |

## 最小原生矩阵（预注册）

用已知直接器件与空声明基线，首条 `X1→Y1`，第二条 `X2→Y2`。B/C/D 中器件、逻辑和声明相同。

| 样本 | 组织 | 与对照的唯一编辑语义变化 |
| --- | --- | --- |
| A | 1 block / 1 简单梯形图 | 原生基线 |
| B | 1 block / 2 独立梯形图 | A 加第二条逻辑；这是计数基线，不作为纯组织因果对照 |
| C | 2 blocks / 各 1 梯形图 | B 将第二条移动到独立 block；局部坐标重定位是编辑器附带变化，单独列出 |
| D | 与 C 相同，交换 block 顺序 | 仅顺序；区分 identity、ordering 与 count |
| E | 在 A 上增加空 block（如编辑器允许） | 空 block 的结构控制；与 C 删除第二条逻辑不同，不冒充语义等价 |

每个样本分别保存编辑后/编译后快照，记录 open→compile all→save→close→reopen；全流 SHA-256、长度、XML 字段 diff、record alignment 与二进制 diff 都进入结果 JSON。重复无编辑保存用于发现 save noise；未经隔离的字段不丢弃、不命名为格式规则。
