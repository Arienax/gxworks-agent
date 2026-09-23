# nested `_hdb` CFB 元数据比较（2026-09-08）

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

本里程碑只读比较三个现有文件。结论：dense-repack 已复现连续、按目录顺序密排的 MiniStream 和 535B/9-sector 的 Program.pou 分配长度；没有复现 native51 的其他流大小、绝对 sector ID、root 大小和修改时间。未发现额外的目录树、CLSID 或 state bits 差异。**这些是结构相关性，不能据此认定导线不显示的原因。**

基线：`main` 的 `4b78691`。此前“生成的 535B Program.pou 放入 native51 原生 allocation 后导线显示 PASS”及 dense-repack 导线不显示，来自已有实验结论，该实验未重新打开 GX Works2 验证。

## 输入与复现

目录：`C:\Users\Arienax\Desktop\te`。以下为完整 GXW 的 SHA-256：

| 标签 | 文件 | SHA-256 |
|---|---|---|
| native48 | `48_STRUCT_X1_Y1.gxw` | `4d0b35230dd37eba1329988e74738922033d851e1f0d4e707473a5721b05093c` |
| native51 | `51_STRUCT_SERIES_X1_M1_Y1.gxw` | `d90769fd0c5c89a3030f062d4b60b0c7e3c0fec58ffbf148b2b586684c3201e6` |
| dense-repack | `48_X1_X2_Y1_DENSE_REPACK.gxw` | `3c7d668cc08ddcc699ac35b33a15e194652f5895fca29803147f3bc43ae09c19` |

在仓库根目录执行（PowerShell）：

```powershell
python tools/gxw_compare_cfb_metadata.py `
  C:\Users\Arienax\Desktop\te\48_STRUCT_X1_Y1.gxw `
  C:\Users\Arienax\Desktop\te\51_STRUCT_SERIES_X1_M1_Y1.gxw `
  C:\Users\Arienax\Desktop\te\48_X1_X2_Y1_DENSE_REPACK.gxw
```

加 `--json` 可输出完整可机读报告。工具只向 stdout 输出，不写输入或实验文件。报告包含全部 header 字节、DIFAT/FAT/MiniFAT（含未用表项）、物理链、48 个完整 128-byte 目录槽位（含空槽 47）、每个流的分配链和三组逐字段差异。原始 hex 的差异附相对字节偏移；目录按 index 对齐，同时显示名称。`stream 16 (1.Program.pou)` 和 root 单独标记。本组三个文件的 stream 名称与目录 index 恰好相同。

## 三组比较

| 指标 | native48 | native51 | dense-repack |
|---|---:|---:|---:|
| `_hdb` bytes | 453632 | 455168 | 453632 |
| root stream size | 11136 | 11904 | 11264 |
| root FAT sector 数 / capacity | 22 / 11264 | 24 / 12288 | 22 / 11264 |
| root trailing slack（bytes） | 128 | 384 | 0 |
| 已分配 mini-sector 数 | 174 | 186 | 176 |
| Program.pou size | 411 | 535 | 535 |
| Program.pou MiniFAT chain | 27–33 | 31–39 | 27–35 |
| MiniFAT 所在 FAT sector chain | `[2,839]` | `[2,813]` | `[2,839]` |

三个文件均为 CFB v3，minor=62，sector=512B，mini-sector=64B，cutoff=4096，7 个 FAT sector、2 个 MiniFAT sector、无外部 DIFAT sector。header 的前 76 bytes 完全相同，包括 CLSID、保留区、transaction signature=0；仅内嵌 DIFAT 中的 6 个 FAT sector ID 不同。

| 物理位置 | native48 / dense-repack | native51 |
|---|---|---|
| FAT sector IDs | `[0,17,18,301,455,608,609]` | `[0,18,19,302,456,609,610]` |
| directory FAT chain | `[1,5,6,8,10,14,16,300,454,813,878,879]` | `[1,5,6,8,10,15,17,300,455,815,880,882]` |
| root FAT chain | `[3,4,7,9,11,12,13,15,295,296,297,298,299,810,811,812,840,880,881,882,883,884]` | `[3,4,7,9,11,12,13,14,16,296,297,298,299,301,811,812,814,816,881,883,884,885,886,887]` |

### native48 vs native51

FAT 有 41 个表项不同，MiniFAT 有 53 个表项不同（统计完整表，包括未用尾部）。目录原始字节变化仅落在各项 start sector / stream size，以及 root modified time。除 Program.pou 外，stream `14`、`34`、`35`、`39` 的 size 也增长；其余流的 size 未变。

### native48 vs dense-repack

header、完整 FAT、FAT/MiniFAT 所在 sector、directory/root 物理链完全相同。MiniFAT 有 24 个表项改变。Program.pou 从 411B/7 mini-sectors 增为 535B/9 mini-sectors；之后所有非空小流的起点增加 2。root size 增加 128B，正好用尽既有物理容量。目录原始字节没有 size / start sector 之外的变化，root 修改时间保持 native48 的值。

### native51 vs dense-repack

FAT 有 41 个表项不同，MiniFAT 有 47 个表项不同。两者都有 37 个非空小流，均按目录顺序从 mini-sector 0 连续密排、每条链以 ENDOFCHAIN 结束；已用区域后的 MiniFAT 尾部全部为 FREESECT。另有 2 个零长度流，起点均为 ENDOFCHAIN。dense-repack 模拟了这种排列形态和 Program.pou 长度，但没有模拟完整布局：

| stream | native48 / dense-repack size | native51 size | native51 相对 dense-repack 多占 sector |
|---|---:|---:|---:|
| `14` | 58 | 296 | 4 mini-sectors |
| `34` | 838 | 987 | 2 mini-sectors |
| `35` | 531 | 771 | 4 mini-sectors |
| `39` | 18814 | 19092 | 1 regular sector |

前面 stream `14` 的分配多出 4 个 mini-sector，恰好对应 native51 的 Program.pou 起点 31 与 dense-repack 起点 27 的差值。三个小流的额外占用合计 10 mini-sectors，恰好对应 root size 差额 640B。这里是分配数量的算术关系，不表示这些流或地址导致导线显示变化；该实验不分析它们的内容。

## 完整目录项排查结果

三份文件的全部 48 个目录槽位具有相同的完整名称缓冲区（含尾部）、名称长度、object type、color、left/right/child、CLSID、state bits 和 creation time。CLSID、state bits、creation time 均为零；所有非 root 项 modified time 也均为零。root 的 left/right 为 NO_STREAM，child=16，color=0，start sector=3。

root 唯一未被 dense-repack 模拟的非分配字段是 modified time（槽位相对偏移 `0x6C..0x73`）：

| 文件 | 原始 FILETIME ticks | UTC |
|---|---:|---|
| native48 / dense-repack | 134330968486340000 | `2026-09-05T15:47:28.6340000Z` |
| native51 | 134330980286810000 | `2026-09-05T16:07:08.6810000Z` |

屏蔽各项的 start/size 字节和 root modified time 后，三份 directory stream 的全部原始槽位逐字节一致。因此本组证据中，没有另一项遗漏解析的 directory metadata 差异可供解释；这一结论不覆盖流内容、外层 GXW 或 GX Works2 运行时状态。

## 验证与边界

`python -m pytest -q tests/test_gxw_container_writer.py tests/test_gxw_container_appended_growth.py tests/test_gxw_structured_reader.py tests/test_gxw_structured_writer.py tests/test_gxw_connectivity.py`：**32 passed**。

额外执行内存校验：非零 CLSID 的 little-endian 解码、state bits、100ns FILETIME 精度及零值/越界值、v3 size 高位保留与 v4 64-bit size、字节偏移 diff、列表长度变化、144 个真实目录槽位、文本/JSON 的三组比较和 Program/root 标记均通过。重复读取的三个输入 SHA-256 与首次读取一致。未跑全仓库测试或真实 GX/PLC 集成；未修改 production 代码、MCP API、writer 或任何 GXW。

## 后续研究

未执行方案及下一步实验见[过程记录](../../process/research/gxw_cfb_metadata_comparison-follow-up.md)。
