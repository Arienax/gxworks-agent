# GXW 工程写回与串联 round-trip（2026-09-10）

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

## 结果与测量范围

**confirmed**：现有梯形图 JSON `X0 -- X1 -- Y0` 经 object model、Program.pou serializer、嵌套 CFB、history 同步生成工程；在本机 GX Works2 全部编译为 **Error 0 / Warning 0 / CheckWarning 0**，保存、关闭重开成功。重新读取后的器件、拓扑、wire 几何和 semantic graph 一致。证据：[编译截图](../../../research/evidence/series-compile.png)、[重开截图](../../../research/evidence/series-reopened.png)、[完整差异](../../../research/results/series_roundtrip.json)。

环境：Windows，FX3U/FX3UC 结构化工程；`GD2.exe` FileVersion / ProductVersion 均为 `1.635.0.1`。所有操作均针对离线副本，没有 PLC 写入、强制器件或在线 RUN 写入。GX Works2 原有未保存的空工程已另存为本地 `research/experiments/2026-09-10/prior.gxw`，不纳入研究样本。

**unknown**：其他 GX Works2 版本、CPU、任意用户库、未知 ABI、任意规模工程是否均接受生成文件。该实验没有将一组成功实验推广成通用编译保证。

## 已审查内容与复用决策

该实验修改前完整阅读了当时 `docs/research` 内全部九篇 GXW 文档，以及 `src/gxw` 的 parser、resolver、模型、connectivity、semantic、serializer、CFB writer、三个 allocator 实验模块和已有 GXW 测试；同时检查普通 patch 与分配策略实验工具。

| 原文档 | 已有成果，该实验直接复用 |
| --- | --- |
| [gxw_reverse_engineering.md](../../process/research/gxw_reverse_engineering.md) | 外层/嵌套 CFB、逻辑文件映射、普通指令研究 |
| [gxw_structured_ladder_reverse_engineering.md](../../process/research/gxw_structured_ladder_reverse_engineering.md) | record、端口、显式线、T 点、交叉不连接 |
| [gxw_semantic_model_v1.md](../../process/research/gxw_semantic_model_v1.md) | 已有对象与语义角色，不设计新 IR |
| [gxw_cfb_metadata_comparison.md](gxw_cfb_metadata_comparison.md) | CFB 分配与目录差异 |
| [gxw_structured_writeback_validation.md](gxw_structured_writeback_validation.md) | 已有串联插入与打开验证、保守分配边界 |
| [gxw_wire_rendering_history_isolation.md](gxw_wire_rendering_history_isolation.md) | iFileSize 恢复显示的因果实验；MD5 单独修改无效 |
| [gxw_structured_function_abi_59_66.md](gxw_structured_function_abi_59_66.md) | Function 参数与端口 ABI |
| [gxw_structured_function_abi_67_71.md](gxw_structured_function_abi_67_71.md) | AND/DIV/ABS 及 enable 变体 |
| [gxw_structured_fb_abi_72_75.md](gxw_structured_fb_abi_72_75.md) | FB 实例/类型分离、TON/CTU 系列及 76/77 样本 |

没有重复定位 binary field，没有修改普通梯形图指令编码，没有提前实现 FBD/ST backend。

## 工程写回契约

入口：[project_writer.py](../../../src/gxw/project_writer.py)，`build_gxw_project(baseline_bytes, programs)` 和 `write_gxw_project(source, new_output, programs)`；`programs` 接受一个 `StructuredProgram` 或逻辑 POU 名到模型的映射，多个 POU 在同一次内存构建中完成。

1. 校验两层 CFB 的链终止、长度、重复名称和重叠分配；从当前 `projectdatalist.xml` 解析 logical name → stream ID。
2. 验证模型的原始 POU 与 baseline 相同，拒绝过期或其他工程的模型。原始 POU 必须能够无损序列化。
3. 使用已有 `structured_pou_writer.py` 重建记录及已验证的 header 长度、计数、画布高度。
4. 更新嵌套 stream，按实际链容量选择现有分配、MiniStream 空闲空间或追加增长。
5. 定位 `history.xml` 中同一 `iProjectdataID` 和 `szProjectdataName` 的唯一当前行，更新已知 metadata。
6. 对两层所有 stream 做逐字节比较，确认非目标 payload 不变；重新解析 POU，并验证 metadata 更新幂等。
7. 校验完成后发布新文件，拒绝覆盖 baseline 或任何现存输出。报告保留 baseline/output SHA-256。

报告包含：修改对象、逻辑 stream、POU 记录前后清单及 offset/length/hash、可回放 binary diff、history 字段前后值及新旧 offset/length、分配模式和验证状态。POU offset 属于逻辑流，history offset 属于原 XML 字节流；不是外层文件绝对地址。完整 CFB header、directory slot、FAT/MiniFAT 差异由实验比较器输出。

未知字段、时间戳、编译器状态、声明和非目标 XML 保持原值。XML 使用原字节区间替换，不重排属性、不重新序列化整份文档；UTF-8、UTF-16LE/BE 均有测试。历史 `diffgr:before` 不作为当前写入目标。歧义、缺字段或不支持的编码表示直接失败。

### Metadata 结论

| 状态 | 结论 | 写入策略 |
| --- | --- | --- |
| **confirmed** | 原有隔离实验中 iFileSize 对导线恢复有因果作用 | 写入 `len(serialized_pou)`，不采用实验阈值或固定增量 |
| **confirmed** | 原始结构化样本 48–77 共 30 个工程均满足 iFileSize = POU 字节数 | [逐样本大小、摘要、工程 hash](../../../research/results/metadata_corpus.json) |
| **confirmed** | 同一 30 样本的 szMD5val = Base64(MD5(raw POU)) | 仅在 baseline 摘要符合该规则时重新计算 |
| **unknown** | 其他摘要格式、缺失摘要的含义 | 保留并在报告标明 unknown |
| **confirmed** | 该实验串联和显式 FB 写回无需猜改其他 history 字段即可打开、编译、保存 | 其他字段不改 |
| **unknown** | szMD5val 是否是所有版本编译/保存的必要条件 | 不从“MD5 单独不能修复导线”推导“摘要永远无用” |

**strongly inferred**：iFileSize 参与编辑器读取/呈现边界。内部实现尚未观察，不能声称已证明具体截断算法。以前实验中的某个 offset+常量、密集布局或物理扇区位置不作为生产规则。

### 当前支持边界

语义入口 [structured_writer.py](../../../src/gxw/structured_writer.py) 复用现有 ladder JSON / PLC IR 和校验器。当前自动 lowering 支持 FX3U 单网络、单支路、普通 NO 串联触点与普通 COIL，直接 X/Y/M 器件；会核对现有 IR lowering 得出的 LD/AND/OUT 序列。注释、标签注解、其他算子或分支不能被静默丢弃。更复杂的已支持对象可直接使用现有 StructuredProgram serializer。

当前是**模板式工程生成**：PLC 设置和库等来自 baseline；不从空字节创建任意工程。CFB 增长仍受现有 FAT/MiniFAT 表容量限制；跨 MiniStream cutoff 或需新增分配表时明确失败，不输出残缺工程。这是实际未实现边界，不是“足够大就可能显示”的实验规则。

普通 `gxw_patch_symbol.py`、`gxw_patch_symbol_same_size.py`、`gxw_insert_series_contact.py` 已接入同一工程 pipeline，默认在输出旁写 `.write.json`。历史 dense/contiguous/appended 隔离实验工具保留原实验行为，不作为工程生产入口。

## A–F 自动实验

入口：[tools/gxw_project.py](../../../tools/gxw_project.py)，采用一个已知单触点→线圈 baseline。对其他拓扑显式拒绝，防止自动选择了错误网络。

```powershell
.venv\Scripts\python.exe tools/gxw_project.py regression BASELINE.gxw --output-dir research/experiments/new-run --results-dir research/results/new-run
```

| 组 | 操作 | record 增量 | 目的 |
| --- | --- | --- | --- |
| A | 原文件逐字节复制 | 0 | baseline hash 应完全相同 |
| B | 单个 symbol，默认 X1→X100 | 0 | 变长记录及 history 同步 |
| C | 新增一个未连接 contact | +1 | 纯对象计数对照，不承诺可编译 |
| D | 新增一个未连接 coil | +1 | 纯对象计数对照，不承诺可编译 |
| E | 插入串联 contact | +2 | 新 contact 与新增 wire |
| F | 插入并联支路 | +3 | 新 contact 与两条支路线 |

自动比较 Program.pou binary/record、对象数量、所有逻辑 payload、history/XML 文本与字节、两层 CFB metadata。输出独立 A.json…F.json 与轻量 regression.json 索引。该实验结果：[regression.json](../../../research/results/regression.json)。A–F 的 GUI 状态保持 `not_run`；串联专门工程和 FB 专门工程的实机软件验证在独立报告中记录，不能挪用成 A–F 全部编译通过。

## 串联 round-trip

```powershell
.venv\Scripts\python.exe tools/gxw_project.py generate BASELINE.gxw research/models/series.json -o series.gxw --report series.write.json
```

实际基线：原始 `48_STRUCT_X1_Y1.gxw`，SHA-256 `4d0b35230dd37eba1329988e74738922033d851e1f0d4e707473a5721b05093c`。语义输入：[series.json](../../../research/models/series.json)。生成 `s.gxw`（保留原样），复制为 `sc.gxw` 后在 GX Works2 执行“转换+全部编译”，保存并关闭重开。

**confirmed**：POU 由 411 变为 535 字节，2 个节点/3 条 wire 变为 3 个节点/4 条 wire；history 使用计算所得大小。读取回来的拓扑、X0/X1/Y0、wire 几何及语义关系一致；`MAIN.res` 从 142 变为 156 字节。编译产生的其他派生文件和 XML 变化详见差异报告，不由 writer 伪造。

比较器不用可变化的 record offset 或 net 编号作为跨保存身份；器件标识和同标识的空间顺序用于对照，同时单独比较 wire 几何。**unknown**：完全重新排版且重复器件重排时的通用图同构；当前比较器不会把无法解释的差异自动视为相等。

## 验证与后续入口

全部原测试保留。新增 metadata 编码/歧义/历史行、变长和多 POU、过期模型、未知 payload 保留、CFB 重叠分配、旧 CLI、防覆盖、未建模输入拒绝、wire 丢失及真实编译提取夹具测试。单元测试不启动 GX Works2；GUI 截图和编译后的实际文件提供另一层证据。

最终在项目 `.venv` 完整测试得到 **1310 passed / 3 skipped / 1 warning**（50.70 秒），MCP smoke 返回 `ok: true`。警告来自已有 Starlette/AnyIO 的弃用提示。系统 Python 初次全测有 3 项缺 `fastapi` 的环境失败，改用已有 `.venv` 后消失；没有为此修改无关 Web 代码或安装全局依赖。23 个工程快照、11 张截图的 hash、新文档链接、结果 JSON 和 `git diff --check` 均已校验。

FB 声明和显式连接研究见 [gxw_declaration_binding_and_fb_wire_20260910.md](gxw_declaration_binding_and_fb_wire_20260910.md)。下一步工程化边界是声明 writer 与更大 CFB 分配，而非重复已解析指令/端口字段。
