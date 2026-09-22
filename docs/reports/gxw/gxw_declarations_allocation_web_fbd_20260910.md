# 通用声明写入、分配表扩容与 Web FBD（2026-09-10）

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

该实验接续[工程写回实验](gxw_project_write_pipeline_20260910.md)和[声明绑定/FB 显式连线](gxw_declaration_binding_and_fb_wire_20260910.md)。不重复容器、指令、连接模型和 Function/FB 既有 ABI 研究；沿用现有 `StructuredProgram`、PLC IR、解析器及审批服务。这里的 FBD 是 GX Works2 的 **结构化梯形图/FBD 对象模型**，不宣称实现完整 IEC FBD 或 ST backend。

## 结论与证据等级

| 等级 | 结论 | 证据与适用范围 |
| --- | --- | --- |
| confirmed | 可写局部与全局声明的已知标量、常量、数组及 FB 实例记录 | GX Works2 1.635 原生 CSV 导入并保存的 `d0/dtypes/dconst/dglobal`；逐 record 字节重建测试 |
| confirmed | FB 调用的实例名和类型需与声明匹配；writer 可以在同一事务中补齐声明 | `auto_decl` 增加 1000 个 BOOL、重命名两个 TON 后，原生编译 0 错误/0 警告/0 检查警告，保存重开图与声明均保持 |
| confirmed | MiniFAT、FAT、DIFAT 可按 CFB 格式容量跨表扩容 | 合成 v3/v4 CFB 测试、独立 olefile 读取；真实 GXW `auto_decl` 的 mini→regular 及外层 FAT 扩容经过原生闭环 |
| confirmed | 默认 FX3U 工程种子可生成两个 TON 和非重合端口的显式连接 | `fresh_fbd` → `fresh_fbd_compile`，原生编译 0/0/0，保存重开后所有 parser 比较项一致 |
| confirmed | 现有继电器 ladder 可转换为包含串并联、常闭触点及多个线圈的 GXW 对象 | `relay_parallel` 原生编译 0 错误、1 条 C2034、0 检查警告；保存重开所有比较项一致 |
| confirmed | Web 可直接生成、编辑、接受、下载和导入 GXW；原生 GX 导入操作使用版本副本 | 隔离工作区的 v0001→v0002→v0003 与原生导入审批记录，见 `web-fbd-acceptance.json` |
| strongly inferred | 新声明的零/空未知字段默认值可用于同布局的新记录 | 16 种原生类型对照及 1000 条新增 BOOL 闭环支持；字段含义未知，不能推广到其他声明格式 |
| unknown | 任意自定义库、结构体声明及未知 FB 调用 ABI 都能自动生成/编译 | 没有此结论；未知记录保留，新的调用只能选已保存的 ABI 模板 |
| unknown | 多个独立梯形图块的通用写入规则，以及 C2034 的消除方式 | 当前 lowering 保留一个已知块，因此多梯形图样例存在该警告；不试写未知块字段 |

`confirmed` 均限于列出的实验或标准测试，不代表所有 GX 版本/CPU 已验证。全部工程来自本地最小控制，不操作真实 PLC。

## 声明最小控制

先在原生局部标签表导入 16 行 UTF-16 BOM 制表符分隔 CSV，再分别改变一个常量类别和全局表。CSV 和保存工程存入 `research/evidence/gxw-generation-20260910.zip`，清单含 SHA-256。`tests/fixtures/gxw_declarations_20260910.json` 保存对应声明流，测试不需要 GUI。

已确认的流布局：54 字节不透明前缀；局部表随后为 counted UTF-16 owner、20 字节不透明区、行数；全局表直接为行数。每行由 8 个 counted UTF-16 NUL 字符串及 5 个 u32 组成，没有独立行长前缀。局部样例尾部 24 字节、全局样例无尾部；实现保留整个实际尾部，而非按此样例长度补齐。

| 字段 | 已确认的用途或当前处理 |
| --- | --- |
| name / data_type | 实例/变量名与类型文本 |
| class_code | VAR=1、VAR_CONSTANT=2、VAR_GLOBAL=8、VAR_GLOBAL_CONSTANT=9 |
| device / iec_address / initial_value / comment | 原生对应列；缺省编辑保持原值 |
| record_id | 表内唯一记录标识；新增取最大已有值加一 |
| array_marker | 原生一维/二维数组为 1；维度和范围存于类型文本 |
| type_code | BOOL=1、INT=2、DINT=3、WORD=4、DWORD=5、REAL=6、TIME=7、STRING=8、FB=15 |
| type_reference | FB 再存类型名；基本类型为空 |
| unknown_u32 / unknown_text | 含义 unknown；原记录原样保持，新记录使用上述受控样例共同的零/空值 |

`TON/TOF/TP/CTU/CTD/CTUD` 的**声明记录**均有原生证据，不能由此推导它们的 Program.pou 调用布局相同。新增调用模板当前为 TON、TON_E、CTU、CTU_E、MOV，以及触点、线圈、输入/输出终端；导入记录可保留其原布局。

`edit_declarations` 支持按名 `upserts`、`renames`、`remove`，拒绝重名、错作用域和不支持的新标量类型。修改备注/名称保留未知类型和未知字段。现有局部表优先绑定 FB，再查全局表；缺失实例自动加入局部表，已有变量冲突明确失败，闲置声明保留。不会合成新声明表、库、结构体或 MAIN.res 编译产物。

## 分配器与事务

`cfb_allocator.py` 根据真实扇区容量求解 MiniFAT/root/FAT/DIFAT 的大小，包含分配表本身占用的扇区，支持空流、mini/regular 转换和缩短后的链释放。目录项 ID、CLSID、时间戳、v3 高位 size DWORD 等不相关内容保持。输出前后验证全流可达性和数据。

4096 字节 mini cutoff、64 字节 mini sector、v3/v4 几何及 DIFAT 链结构来自 CFB 标准，**不是实验阈值**。依据：[Microsoft CFB Header](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/05060311-bfce-4b12-874d-71fd4ce63aea)、[MiniFAT](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/c5d235f7-b73c-4ec5-bf8d-5c08306cd023)、[DIFAT](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/0afa4e43-b18f-432a-9917-4f276eca7a73)。

合成测试覆盖 4095/4096 转换、MiniFAT 1→2、FAT 扩表、8 MiB+13 与 16 MiB+7 导致的单/双 DIFAT，以及 v4 4096 字节扇区。数值仅为边界测试输入，未写成生产容量限制。DIFAT 大文件经过独立读取，但尚未做同规模 GX 原生工程编译实验。

`build_gxw_project` 将 POU 和声明一起写入嵌套 `_hdb`，按实际流长度同步 history.xml 的有效行 `iFileSize`，按已确认格式同步 MD5。未知字段、历史 before 行和未修改流保持。报告包含各逻辑流修改 offset/长度、对象、元数据及两层分配变化。输出必须为新文件；不做原地覆盖。已有文件写入 API 默认不补声明，以保留负面对照；对象生成入口明确启用 `sync_fb_declarations=True`。

## 原生生成闭环

| 输入 → 原生保存重开 | 编译 Error / Warning / CheckWarning | 比较结果 |
| --- | --- | --- |
| auto_decl → auto_decl_compile | 0 / 0 / 0 | 局部 1008 行；设备、拓扑、wire、semantic graph、未知 record、声明保持 |
| fresh_fbd → fresh_fbd_compile | 0 / 0 / 0 | TIMER_A.Q 经显式线连接 TIMER_B.IN；设备、拓扑、wire、semantic graph、声明保持 |
| relay_parallel → relay_parallel_compile | 0 / 1 / 0（C2034） | X0→(X1 或常闭 X2)→Y0/Y1；另一路 X3→Y2；所有比较项保持 |

对应 `research/results/*-roundtrip.json` 包含逐项 parser 输出与文件 hash。编译截图独立保留；parser 测试只复核这些保存产物，不声称自动启动了 GX。C2034 原文为“1个梯形图块中有多个梯形图”，当前不消除此已知限制。

## Web 集成与验收边界

入口为 `src/application/fbd.py`，HTTP 适配层仅转发模型与命令。新建工程可选 FBD，现有 generation 工作流通过原 ModelProvider 获取受限对象 JSON，确定性生成 GXW、对象投影、SVG 和写入报告，进入现有冻结候选及审批。导入上传 GXW 时先选择 POU，其他工程流原样保留；编辑可改对象、坐标、正交连线和声明。当前工程种子只覆盖 FX3U；导入工程的 CPU 参数不会被重写，也没有实现所有导入 CPU 的识别校验。

`accept_local` 不会操作 GX。`gx_import` 经既有 COM 单线程执行队列和跨进程桌面锁，把已确认版本复制到独立目录，再由 `gxworks2/project_import.py` 打开副本。副本 hash 和预览一致性复核在执行前进行；编译/保存不会修改正式版本。编译后的文件可从网页“导入 GXW”再形成候选。

原生实测发现 GX Works2 文件名（不含扩展名）最多 30 字符，完整 UUID 前缀方案被 GUI 明确拒绝；现改为 28 字符 stem，独立目录保留完整 UUID。截图 `gxw-filename-limit.jpg` 记录产品限制，非凭经验猜测。未知提示和保存提示不会自动批准；成功只报告已打开，编译仍 `unverified`。

网页验收使用固定离线模型响应，验证生成流程及文件一致性，不代表在线模型的正确率。已完成生成→预览→接受 v0001、FB 实例和声明同步重命名→v0002、上传已编译 GXW→v0003、批准导入→原生正确打开。原 v0003 hash 未变。FBD 预览使用自身的白底样式，避免旧 ladder 深色转换只改变背景而导致文本不可读。

尚未覆盖完整 IEC 初始化值语法检查、任意 FB ABI、自动原生 compile 命令、FBD 仿真/诊断、FBD 的 CSV 同步路径、发布包实机验收。继电器转换只支持已有 NO/NC/COIL 与串并联结构，遇到不支持指令明确拒绝；注释保留为 source_ladder.json 附件，不猜测 GXW 注释记录。

## 离线复现入口

```powershell
python tools/gxw_project.py fbd research/models/two-ton.json -o new.gxw --report new-report.json
python tools/gxw_project.py inspect new.gxw -o objects.json
python tools/gxw_project.py fbd objects.json --baseline new.gxw -o copy.gxw --report copy-report.json
python tools/gxw_project.py declarations new.gxw research/models/declarations.json -o declared.gxw --report declarations-report.json
python tools/gxw_project.py compare new.gxw declared.gxw --report compare-report.json
python -m pytest -q tests/test_gxw_declarations.py tests/test_gxw_cfb_allocator.py tests/test_gxw_object_model.py tests/test_gxw_generation_roundtrip.py tests/test_web_fbd.py
```

输出路径必须不存在。`declarations` 只执行显式声明修改；调用与声明一起改用 `fbd` 的 `declaration_edits`。原有 regression/series/symbol 命令继续保留。完整该实验测试记录见 `research/results/generation-verification.json`。
