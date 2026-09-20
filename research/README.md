# GXW 工程生成实验

2026-09-19 新入口：`gxw_corpus.py` 统一扫描、失败保留、分层比较、原始字节保留和局部补丁；`align_gxw_tokens.py` 将整份原生 CSV 与 token 顺序、操作数、文本位置、步号核对。普通 Ladder 的只读指令投影与常量补丁位于 `src/gxw/token_listing.py`、`src/gxw/lossless.py`。三个本地工程的 385 条指令和 185 条文本全部对齐；等长和变长常量修改已有独立原生观察，见 `results/token-20260919/`。本地完整工程及 CSV 留在忽略入库的实验目录，结果清单绑定其 hash。

`native_gxw_tokens.py` 可调用本机 GX Works2 附带的 15.31 版转换 DLL，在独立 32 位进程中批量交叉核对 token。`harvest_native_gxw_tokens.py` 读取厂商指令数据库，批量产生候选；791 个候选中 782 个通过原生内存编码和解码，形成 474 种精确指令头观测。剩余 9 个操作数拒绝案例保留在结果中。`native-conversion-corpus.json` 还保留了初始模式参数错误导致的 72 个比较指令失败；反汇编定位模式参数后得到 `native-conversion-ladder-mode.json`。这些是格式转换证据，完整工程的编译、保存、重开证据单独记录。

```text
python research/gxw_corpus.py scan research/corpus/gxw.json -o LOCAL_NEW_REPORT.json
python research/gxw_corpus.py decode-tokens PROJECT.gxw --text-encoding cp936 -o LOCAL_LISTING.json --csv LOCAL_LISTING.csv
python research/native_gxw_tokens.py --fixture tests/fixtures/gxw_token_corpus.json PROJECT.gxw -o LOCAL_NEW_DIRECTORY
python research/harvest_native_gxw_tokens.py -o LOCAL_NEW_DIRECTORY
```

原生辅助程序只提供离线字节转换，不连接 GX UI、仿真器或 PLC。它要求 Windows、现有 .NET Framework 编译器及 hash 匹配的本机 DLL；批量 harvesting 另需可读取本机 MDB 的 ACE/ADO。源码、请求、原生返回码及崩溃前输出都留存在新实验目录，厂商二进制不随仓库分发。纯 Python 解码不依赖安装 GX Works2。

`generate_native_token_project.py` 从 hash 绑定的空原生种子和指令文本生成实验工程，拒绝覆盖非空程序及已有输出。`evidence/gxw-token-native-20260919.zip` 保存 24 条指令、112 步的完整例子及原生转换、保存、重开、CSV 全量核对：POU 从 106 增至 384 字节，触发内层 MiniStream/分配表扩容与外层追加分配；生成与原生保存的 POU 字节一致，`.res` 由 GX Works2 更新。精确清单见 `results/token-20260919/native-manifest.json`。

`probe_native_gxw_operands.py` 批量探测复合操作数。2025 个输入保留了 879 个原生成功结果和 1146 个拒绝；Python 已逐项匹配成功结果，操作数按多 token 分组并保留原始字节。816 个不同原生片段的“原生解码→文本→原生编码”中，11 个因浮点舍入或记数法标记发生字节变化，不能把文本往返当作无损。包含变址、位组合、位选择、中文字符串和浮点数的 16 指令/90 步工程完成了原生转换、保存、关闭、重开和 CSV 核对，见 `results/token-20260919/operand-native-manifest.json`。两批原生观察累计覆盖 499 个精确指令头。

截图勘误：`native-manifest.json` 和 `native-mutations.json` 中 hash 为 `35f6c09b…` 的 PNG 来自错误的旧状态引用，已标为无效观察；原始归档保留用于追溯。工程及 CSV 字节证据有效，补取的 112 步/90 步重开和关闭画面位于 `gxw-token-operands-20260920.zip`，不冒充历史转换瞬间的截图。

`read_gxw_resources.py` / `src/gxw/token_resource.py` 读取 `.res` 的有界编译指令区，Structured 的 TON 展开结果也能通过同一解码路径读取。扩展 corpus 的 207 份引用对应 65 个不同 `.res`，79 个非空代码区全部与原生解码相符；公开归档的 17 个资源及原生返回字节可离线回放。尾部已观察的单程序名与 207 份映射一致，保留其他未知尾部。8 个资源跨不同源程序版本复用，因此编译结果不代表当前源码；原生 X1→X2 修改对照明确展示 `.res` 在转换前仍是 X1。见 `compiled-resource-corpus-20260920.json`。标签保持独立记录，禁止静默丢弃标签导出扁平 IR。

`patch_native_token_instructions.py` 仅编码指定的新指令，交由 `src/gxw/token_patch.py` 按源 hash、原始偏移和预期字节批量替换；未编辑的浮点数、字符串、文本和未知记录均保留原始字节。16 指令样本两处修改后为 94 步，完整工程两处修改后为 234 指令/142 文本/563 步，两者均完成原生转换、保存、关闭、重开与完整 CSV 核对。保存后的 POU 与补丁一致，更新的 `.res` 代码区与当前源码 body 一致（包括完整工程的文本记录）。见 `instruction-patch-manifest.json`；公开归档含合成样本，完整工程证据留在忽略入库的本地实验目录。

`control-flow-manifest.json` / `evidence/gxw-control-flow-20260920.zip` 保存含 `CJ`、`CALL/SRET`、`EI/IRET` 的生成工程及原生闭环：17 条指令、3 个标签、25 步。原生 CSV 确认已观察的标签头占 1 或 2 步（样本 `P0`、`I1` 为 1，`P256` 为 2）；读取器可连续计算后续步号，CSV 保留标签行。两个编译资源副本仍各自保留，格式验证不等于设备执行验证。

`splice_token_records` / `patch_native_token_instructions.py` 的 `splice_records` 计划支持按原始坐标批量插入、删除和替换完整记录段，保留原始 END、未编辑记录及其他流。新增调用和子程序、删除输出梯级的 20 指令/4 标签/31 步样本，以及在当时未识别的字符串片段旁编辑的 17 指令/93 步样本，均完成原生转换、保存、关闭、重开和完整 CSV 核对，见 `record-splice-manifest.json`。参考 gx3-cli-mcp 的局部编辑与往返核对方法，但核对完整有序记录和步号，不以设备出现集合相同代替语义一致。

读取器现在区分指令头中的身份、步数和头部形状：DLL 解码分支及 88 个字符串变长对照支持按已观察的指令族读取，无需再加入 30 个精确头枚举。三/四字节 T/C 输出头的操作数数量仍分开处理；零或负的有符号步数保持 opaque。`step-width-manifest.json` 保留另一条原生内部重算路径的原始证据：1685 个片段中 1681 个字节不变，35 个故意改错步数的片段全部恢复，4 个缺少设定值的变址 T/C 输出返回 -13。然而最小无设定值工程在 GUI 转换、保存、重开及离线程序检查中被接受（Error: 0），所以内部返回码没有成为生产校验或自动修复规则。后续原生输入阶段对照见 `machine-roundtrip-manifest.json`；`stored-steps` / `recompute-width` 仍仅是 hash 绑定的离线研究调用。

继续追到正式 `ChangePToMcode` 导出后，6 份已有原生工程全部生成完整机器码，机器码字数与 112/90/25/31/93/6 步分别一致；35 个步数被改错的片段仍生成各自相同的机器码，提供了独立于存储步数字段的核对方式。上述无设定值工程在此正式入口也返回 `0x04010004`，而带设定值对照成功，见 `machinecode-manifest.json`。首次 ABI 调用遗漏原生必需的 128 字节工作区导致的隔离进程崩溃，以及错误附加解码专用零哨兵导致的拒绝，都保留了原始请求、源码和结果。研究适配器的 `machinecode` 只做内存转换，不写入设备；转换接受也不等同于控制流或执行安全验证。

`native_gxw_roundtrip.py` 使用正式 `ChangeMToPcode` 形成 token→机器码→token→机器码核对，比较全部字节、输入消耗和完整有序读数。1681 个被接受的指令片段及 6 份原生工程均逐字节还原；公开 corpus 的 110 个引用（53 个工程镜像、16 种不同 body）也逐字节还原。三个本地完整工程的 385 条指令保留，185 条源码文本在机器码反解中被省略，因此反解结果不用于重写源码。原生文本重编码曾改变的 11 个浮点样本，通过机器码往返均保留原始 token；11 对文本重编码前后的机器码也均不同，不据此推断运行语义必然不同。`GetErrorOffset` 将缺省 T/C 指令的拒绝位置定位到指令末尾；GUI 直接输入同一缺省形式停留于指令帮助，补入 K11 才接受。实验生成器和指令补丁现在核对新编码片段，拒绝后保留失败输入；已有工程的解析和未编辑区域仍原样保留。公开原始证据与重放入口见 `machine-roundtrip-manifest.json`，本地完整工程数据未入包。

```text
python research/native_gxw_roundtrip.py PROJECT.gxw --compiled-resources -o LOCAL_NEW_DIRECTORY
```

`inspect_gxw_compiler.py` 从 `ESCompiler.stg` 的索引、链接片段、`CGTable.dat` 实例引用和 `DebugInformation2.dat` 读取存储的编译结构，保留原始数据及未知尾部。现有 corpus 的 33 份链接表可完整拼回程序，109 个有效片段的步数与原生转换一致。重命名后的旧 FB 缓存可能与新实例字节完全相同，必须同时检查原生链接标志；该反例保存在 `structured-compiler-manifest.json`。这条路径能读取源对象尚未完全解码的编译结果，但不推断缓存与当前源码同步。

`native_gxw_compiler.py` 使用独立进程调用厂商编译表读取、导出和回存接口。12 份不同符号表原生回存字节一致，1,345 个组件的名称、作用域、解析后的类型、下一记录位置和原始分配数据与 Python 读取一致，38 个实例引用也一致。外部变量类型通过全局表引用解析；仅修改全局类型的原生对照见 `compiler-components-manifest.json`。原生结构中的指针和未初始化联合字段不作为可移植数据解释。

```text
python research/inspect_gxw_compiler.py PROJECT.gxw -o LOCAL_NEW_DIRECTORY
python research/native_gxw_compiler.py CGTable.dat --components -o LOCAL_NEW_DIRECTORY
```

`trace_gxw_compiler.py` / `replay_gxw_compile_trace.py` 已捕获并离线重放公开 t2 工程的 IEC 编译链：11 次调用、13 个源码描述符，空工作目录生成的 420 字节程序与 GUI 完全一致。X1→X3 的局部 GXW 补丁完成原生编译、保存、重开；全部编译输入和输出均符合事先离线预测。`probe_compiler_minimal.py` 将上下文缩至 150 字节后，34 个表达式案例中 29 个产生程序，独立厂商指令解码全部一致。缺少上下文时成功返回却省略程序、重置符号表丢失分配信息、资源列表不决定本次链接顺序等反例一并保留，见 `results/token-20260919/compiler-replay-manifest.json`。这是编译器中间输入的实验接口，不是通用 GXW/ST 写入器；原始进程堆快照未入包，重放仅需归档中的无指针计划。

```text
python research/replay_gxw_compile_trace.py EXTRACTED/offline-replay-5/plan.json LOCAL_NEW_DIRECTORY --plan
python research/probe_compiler_minimal.py EXTRACTED/offline-replay-5/plan.json LOCAL_NEW_DIRECTORY
python research/probe_compiler_programs.py EXTRACTED/named-variables-1/bool-chained/plan.json LOCAL_NEW_DIRECTORY
```

研究说明与置信度标注以以下文档为准：

- [工程写回、A–F 与串联闭环](../docs/research/gxw_project_write_pipeline_20260910.md)
- [声明绑定与 FB 显式连线](../docs/research/gxw_declaration_binding_and_fb_wire_20260910.md)
- [通用声明、跨表扩容与 Web FBD](../docs/research/gxw_declarations_allocation_web_fbd_20260910.md)
- [Structured block 边界与 C2034 消除](../docs/research/gxw_network_boundaries_20260913.md)
- [ABI harvesting 与 2026-09-19 阶段固化](../docs/research/gxw_abi_harvesting_20260913.md)：SET 控制、原生截图和待完成项；证据为 `evidence/gxw-abi-checkpoint-20260919.zip`，清单与差分在 `results/abi-checkpoint-20260919/`。流水线尚未完成新 ABI 生产注册。

`results/regression.json` 索引六组自动比较；`results/gxworks_validation.json` 单独记录实际 GUI 观察，避免将 parser 通过当作编译通过。JSON 中 offset 属于相应逻辑流，CFB 字段变化另行列出。

`evidence/gxw-20260910.zip` 保存 23 个不可变工程快照，`results/evidence_manifest.json` 包含逐文件 SHA-256 和截图 hash。解压到新目录后可以用 `tools/gxw_project.py compare` 重算比较；通过 `reproduce_fb_controls.py` 重建名称、类型和显式连接三个 POU 控制。回归测试校验这三种重建结果与保存工程逐字节相同。

`models/series.json` 是现有 ladder JSON 输入示例。`regression/A.gxw` 是单触点基线；`s.gxw` 是生成结果，`sc.gxw` 是编译保存并重开后的结果。`f2.gxw` / `f2c.gxw` 是 FB 显式连接的对应一对。其余短文件名及失败对照见声明研究文档。

`evidence/gxw-generation-20260910.zip` 是后续声明/生成闭环的独立证据包，清单为 `results/generation-evidence-manifest.json`。`models/two-ton.json` 可直接生成两个显式连接的 TON；`models/declarations.json` 演示局部变量写入。对应 `*-roundtrip.json` 分开记录原生编译观察与 parser 比较结果。

`experiments/` 是忽略入库的本地工作副本目录。原有未保存工程备份 `prior.gxw` 仅留本机，没有加入证据包。writer 使用基线工程或已验证 FX3U 种子及已知对象布局；不自动生成未知声明布局、库或编译产物。

`evidence/gxw-block-boundary-20260913.zip` 保存原生 A/B/C/D/E 及单流 M1 对照；`gxw-block-generation-20260913.zip` 保存双 block relay 生成与原生闭环。`results/block-boundary-20260913/` 包含 knowledge ledger、全流 SHA/size、XML/record/field diff、真值表及原生观察 manifest。`models/relay-parallel-blocks.json` 可由 `tools/gxw_project.py fbd` 生成无 C2034 的已验证逻辑；原始 ladder 输入为 `models/relay-parallel.json`。
