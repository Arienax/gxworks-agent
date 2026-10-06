# 2026-10-06 统一能力发现与三回路报警系统对照

共享能力发现、选择要求及本地可维护性审阅已接入已有生成与编辑入口。在线效果对照收窄为三菱三回路报警系统，共12个首次旅程；API与MCP各做基线、统一上下文两组，各重复三次。12份候选均通过本轮冻结行为轨迹及实际CSV回读验收。
本轮没有观测到行为通过率的差异。API统一组的重复写入建议少一项，MCP两组均没有命中当前重复检查模式；增加了输入上下文，耗时仍有波动。三次样本不足以断言通用质量或速度优势。

## 实现与接口

应用检索统一经knowledge.retriever进入。中英功能查询复用现有手册、Registry、指令定义、范例及工程目录；显式调用先查精确事实，其余按功能覆盖排序并保留替代项。型号检查技术适用性，语言和工程目录明确可用接口，工作流仍由用户选择。
候选简表、必要语义和选择要求使用现有知识预算，按完整证据单元保留，删除重复并记录遗漏和缺口。发现结果不写入confirmed_spec。行为、类型、接口、扫描、边沿及停止/复位正确后，才比较重复程度和修改便利；没有必用指令或数量门槛。
API与MCP共用生成上下文与公开投影；MCP服务端不嵌套调用模型。可选generation_handoff.capability_discovery记录发现、交付/遗漏和估算。候选及版本保存可选maintainability_review，旧记录保持可读。Web现有结果/检查区展示相同的发现、重复位置和检查覆盖。
Ladder审阅IR，FBD审阅对象及连接可识别的调用，ST审阅可可靠识别的调用和行首字面赋值。建议不改程序、不拒绝正确候选、不证明替换等价；未命中模式也不能证明实现最优。程序、IR、CSV及各语言工程协议保持原链路。
实现规则见[能力发现与审阅](../architecture/capability-selection.md)，功能查询见[共享发现](../../src/knowledge/capability_discovery.py)，预算与投影见[应用上下文](../../src/application/capability_context.py)，审阅见[Python Core审阅](../../src/plc/maintainability.py)。

## 冻结需求与参考

参考为Mitsubishi Electric《Training Manual GX IEC Developer (Hardware: MELSEC FX)》，208661-B，04/2010，印刷页5-1至5-4、PDF第135至138页：前页给出要求和I/O，中间两页给出完整十个Ladder网络，后页给出下载及监视步骤。[官方手册](https://eu-assets.contentstack.com/v3/assets/blt5412ff9af9aef77f/blt8d64a77044c9f9ab/6171adc99d191809f6ba06e2/208661.pdf)
X1为布防钥匙开关；X2至X4为常闭报警回路，1正常、0报警。Y0布防显示，Y1警笛，Y2外置旋转/闪烁灯的持续使能，Y3至Y5为独立报警记忆。连续布防20秒；首次故障后延时10秒，警笛持续30秒；后来故障不能延长或重启警笛。撤防同扫描清理所有输出、状态和等待，再布防可重新完整循环。
初态、外置灯使能、后来回路加入、取消及重新计时均在调用前补入两组相同原文并注明来源。没有新增现场I/O，也未指定某条指令。冻结样本见[需求与来源](../../benchmarks/capability_alarm_case.json)。参考程序和独立预期只在评估侧，未放入生成上下文。
官方参考转换后的IR及实际CSV先通过同一独立验收，每份产物16,511个10ms扫描。原生软件及设备执行未在本轮实施；公开完整示例及本轮软件验收不能替代原生运行证据。

## 测量方法与环境

Windows 11 build 26200，Python 3.13.14。基线冻结HEAD cbe05d38de8f45feec4b517520128307bfbe44ce及当时工作树已有改动；基线与统一上下文源码副本、配置、实际命令保存在独立测量目录。
API使用保存的官方DeepSeek预设，https://api.deepseek.com、deepseek-flash，保留保存参数；MCP命令指定当前CLI配置的gpt-6.1-sol、max。模型名称是请求配置，响应记录未提供可独立核实的底层版本ID。全局活动预设未改。
种子20261006控制旅程顺序，未新增模型采样参数。收窄后沿用原随机清单中的报警子序列，交替组别、串行执行；先前两个报警结果保留。两组同Core、协议、事实来源和范例设置，实验范例关闭，模型重试及自动修复关闭。
API通过真实Workbench作业提交、轮询、候选检查与版本/CSV保存，按组替换冻结的生成上下文。MCP通过真实stdio客户端及七个获授权隔离工具执行：生成上下文及工具定义/服务端提示按组冻结，get_generation_context返回这份冻结上下文；候选经共享Core后由隔离测量脚本保存版本及CSV。该实验比较冻结上下文的效果；生产服务的动态入参、版本绑定、取消、过期状态及公开契约另由离线回归覆盖。
七项MCP许可仅经本次命令传入：读取工程、生成上下文、手册检索、FBD目录、读取FBD、创建Ladder候选、创建FBD候选。实际报警旅程只调用前三项和创建Ladder候选；未使用其他工具或原生软件，不改全局审批配置。
机器总耗时包括上下文准备、模型/工具等待、Core及产物保存；独立验收另计，取得正确CSV时间包括这段验收。局部分项可能重叠，不再相加。人工阅读、审批等待和操作未测。

## 四组观测

|接口|组别|行为与CSV通过|机器耗时范围/中位数（秒）|取得正确CSV中位数（秒）|重复建议总数|
|---|---|---|---|---|---|
|API|基线|3/3|58.28–87.45 / 75.76|84.45|2|
|API|统一上下文|3/3|52.75–90.73 / 66.99|74.73|1|
|MCP|基线|3/3|172.24–181.58 / 174.56|182.05|0|
|MCP|统一上下文|3/3|145.77–232.29 / 163.03|169.79|0|

API六次均只生成一次，无Agent A、确认表或不必要提问。MCP六次各提交一次候选；模型内部调用数及工具模型轮次不可见，保持缺失。发现与本地审阅为0模型调用。首次失败后追加调用与等待为0，本轮12次没有首次失败或未验证产物；没有后续修复覆盖首轮结果。

独立轨迹包含布防前故障、取消不足20秒的布防、瞬时故障及恢复、后来两路加入、警笛到期、报警延时中撤防、第三个完整周期和最后撤防。所有IR及CSV分别检查如下六项：

|要求|每份产物观测数|IR / CSV结果|
|---|---|---|
|常闭极性与三个回路独立记忆|49533|12/12通过 / 12/12通过|
|20秒布防边界与中途撤防重计|16511|12/12通过 / 12/12通过|
|10秒触发延时与撤防取消|3404|12/12通过 / 12/12通过|
|警笛30秒且不被后续异常重启|12401|12/12通过 / 12/12通过|
|光报警与回路记忆保留|49604|12/12通过 / 12/12通过|
|撤防同扫描清理及完整再布防周期|55326|12/12通过 / 12/12通过|

仅稳定输入下的计时输出边界允许最多一扫描偏差，撤防和回路记忆要求同扫描。不存在多余现场I/O；间接、范围及分组访问仍使用Core的设备投影，不增加独立PLC规则。验收限于冻结轨迹，并非完整状态空间证明。

## 全部12个旅程与CSV

|旅程|机器/独立验收（秒）|模型调用 / 工具调用|重复建议|逐项验收 / CSV|
|---|---|---|---|---|
|07_api_multi_zone_alarm_unified_1|52.75 / 9.88|1 / 不适用|1|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_unified_1.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_unified_1.csv)|
|08_api_multi_zone_alarm_baseline_1|75.76 / 8.69|1 / 不适用|1|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_baseline_1.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_baseline_1.csv)|
|39_api_multi_zone_alarm_unified_3|66.99 / 7.74|1 / 不适用|0|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_unified_3.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_unified_3.csv)|
|40_api_multi_zone_alarm_baseline_3|58.28 / 7.24|1 / 不适用|1|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_baseline_3.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_baseline_3.csv)|
|47_mcp_multi_zone_alarm_unified_3|163.03 / 6.76|缺失 / 10|0|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_unified_3.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_unified_3.csv)|
|48_mcp_multi_zone_alarm_baseline_3|181.58 / 6.49|缺失 / 8|0|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_baseline_3.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_baseline_3.csv)|
|55_api_multi_zone_alarm_unified_2|90.73 / 6.68|1 / 不适用|0|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_unified_2.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_unified_2.csv)|
|56_api_multi_zone_alarm_baseline_2|87.45 / 7.55|1 / 不适用|0|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_baseline_2.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/api_baseline_2.csv)|
|59_mcp_multi_zone_alarm_unified_2|145.77 / 7.54|缺失 / 9|0|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_unified_2.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_unified_2.csv)|
|60_mcp_multi_zone_alarm_baseline_2|172.24 / 7.81|缺失 / 7|0|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_baseline_2.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_baseline_2.csv)|
|69_mcp_multi_zone_alarm_baseline_1|174.56 / 7.49|缺失 / 8|0|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_baseline_1.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_baseline_1.csv)|
|70_mcp_multi_zone_alarm_unified_1|232.29 / 7.17|缺失 / 9|0|[逐项验收](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_unified_1.acceptance.json) / [CSV](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/mcp_unified_1.csv)|

[全部CSV交付包](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/three-zone-alarm-csv-delivery.zip)包含12份程序CSV、12份注释CSV及12份验收记录。全部CSV、配套注释CSV和逐项验收可从[交付清单](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/csv-deliveries/manifest.json)核对。原始请求/响应、CLI事件、检索、分项耗时、候选及版本产物保存在每个旅程目录。[完整测量报告](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/report.md)、[机器汇总](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/summary.json)和[交付核对](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/final-delivery-audit.json)保留全部重复及归档路径。

## Token与上下文增量

供应商返回值与本地估算分开列。推理字段单列，未再次叠加到output/total。供应商没有返回的数据保持缺失；本地估算不是计费token。

|接口|组别|供应商输入中位数|供应商输出中位数|推理中位数|缓存输入中位数|程序输出本地估算中位数|
|---|---|---|---|---|---|---|
|API|基线|12539|14988|14390|12288|260|
|API|统一上下文|15411|11560|11066|15232|261|
|MCP|基线|341996|4860|2901|296704|237|
|MCP|统一上下文|396034|4078|2318|338688|236|

两接口统一组的候选简表估算272、新增事实1,902、选择要求299；现有知识预算仍为12,000，知识证据由7,216增至9,688，净增2,472。API完整输入估算10,386→13,152；实际供应商输入12,539→15,411，每次增加2,872（22.9%）。程序输出估算中位数260→261，未观测到程序文本token下降。
MCP单次生成上下文工具文本估算13,551→17,065，增3,514（25.9%）；三次基线读取4次、统一组读取5次。实际工具调用基线为8/7/8，统一组为10/9/9。工具返回文本和重复读取另存，不把它们的和冒充内部模型上下文重放。供应商总输入中位数增加15.8%，其中包括不同工具轨迹和缓存；无法把全部差额单独归因于能力发现。
供应商输出和推理的中位数有所下降，但上下文与缓存用量增加；统一组最长耗时也更高。缓存状态未控制，三次结果不能建立成本节省、稳定提速或通用质量提升结论。

## 回归、失败与兼容记录

相关检查归属沿用既有测试文件；测试集合重叠，不能把下列数量相加为独立覆盖。完整环境及早期失败见[回归记录](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-capability-discovery-final/verification.json)。

|检查|实际结果|
|---|---|
|生成、共享上下文、RAG、HTTP/MCP、预算与路径一致性初轮|474通过；最终扫描/程序迭代区分后305通过|
|Core、Candidate、Agent、IR/CSV、Registry|较早特性检查点4,247通过；随后4,178通过、4项UTF-8读取失败，显式编码修正后相关34项通过|
|Direct缺参与ST元数据及FBD契约|17通过|
|最终HTTP Ladder/ST/FBD、Workbench生命周期、MCP及路径一致性|181通过、1跳过；Windows进程不能创建符号链接|
|ST字面赋值审阅与生成边界及持久化|63通过|
|最终FBD对象/连接审阅、共享上下文与FBD HTTP|95通过；连接快照不反写源对象的定向检查2项通过；最后Web类型检查/构建通过|
|Web|49单元检查通过，类型检查/构建通过；最终标签修改后构建及2项投影检查通过|
|真实HTTP/Headless Edge交互|10项通过，0模型调用；初版导航使响应句柄失效已修正，原失败保留|
|真实MCP stdio离线预检|通过，0模型调用|
|官方参考IR/CSV、最终12旅程交付核对、测量脚本语法|通过；无原生或设备执行|

持久化检查发现原提案元数据转发漏掉审阅，ST/FBD还漏掉生成handoff；已修正共享提案保存。较早07/08报警版本保持原元数据，报告对两组程序采用同一确定性事后审阅并保存独立投影，不改原候选或首次结果。原有旧分析测试遗漏既有canonical io_binding，冻结基线也复现该断言失败；更新预期后保留原文件未被写入的断言，最终回归通过。
FBD审阅补充连接清单与显式命名端点到对象位置的引用，坐标连接不推断绑定；返回记录保持独立快照。随后一次翻译补充与既有key重复，类型检查拒绝后删除新增重复key并重建。该修正不改变模型上下文或报警候选。

ST审阅补充行首字面赋值位置，未改变模型上下文。类型、控制条件、控制流及替换等价性未被这项本地审阅证明。型号适用性参数化覆盖已有资料，原生适用范围仍未验证。

## 范围偏离与限制

在线清单最初错误沿用旧六类任务。用户明确要求只测三回路报警后，停止对应非报警客户端和隔离服务，保存旧72项清单，收窄为12个报警旅程，并在运行入口拒绝其他案例。实际另保留16个非报警旅程记录，其中8个是审批引导；另有1个中断和前期pilot。它们的错误、用量、产物及耗时均保留，全部排除本报告报警效果统计。中断未返回完整用量与耗时，保持缺失。
首次工具审批模式auto仍被CLI拒绝；获人类授权的七个工具改用本次命令的approve覆盖后正常执行，不改全局配置。该审批引导发生在已排除案例，不作为报警首次结果。
本轮不执行GX Works2原生导入/编译、PLC Simulator、Factory I/O或真实设备，也不补齐ST完整语义验证、不扩建全指令人工事实库或自动修复架构。在线结论仅适用于此FX3U Ladder样本；共享入口回归不等于其他型号、ST/FBD在线效果或真实设备运行。

## 后续修正：共享条件归并与离线回放

用户审阅指出 `mcp_unified_1` 和 `mcp_unified_3` 仍有明显重复条件。旧归并器把 T/C 条件整体视为屏障，并逐个贪心合并相邻网络；后接较短条件时，已经提取的较长公共条件会重新展开。旧可维护性审阅只检查完全相同条件下的部分写指令，因此“0条建议”不能说明不存在重复。

此次由 Core 统一分类 9 种输入标签、7 种输出标签及 Registry 指令类别，依据现有型号事实和写入范围决定条件能否复用。纯条件支持不同位置的公共项及并联支路提取；连续分支采用动态规划划分，以条件出现次数、网络数、表示变化依次择优。边沿求值、扫描内写后读、控制流、未知效果、修改范围及注释仍构成明确边界。没有增加模型调用、专用指令事实包或改变 IR/CSV 协议。技术规则见[能力选择与条件归并](../architecture/capability-selection.md#ladder-condition-normalization)。

2026-10-06 在同一 Windows/Python 工作树离线回放全部12份报警程序。MCP从原始compact响应重新准备候选，API从原交付IR回放；两者的输入起点明确区分，本次不是新的在线模型效果对照。原旅程及首次验收记录保持不变。

|程序|原交付条件数→新条件数|原网络数→新网络数|IR / 实际CSV验收|
|---|---|---|---|
|api_unified_1|25→22|15→12|通过 / 通过|
|api_baseline_1|26→23|17→14|通过 / 通过|
|api_unified_3|19→19|9→9|通过 / 通过|
|api_baseline_3|22→18|15→11|通过 / 通过|
|mcp_unified_3|28→22|3→4|通过 / 通过|
|mcp_baseline_3|36→18|11→2|通过 / 通过|
|api_unified_2|22→19|16→13|通过 / 通过|
|api_baseline_2|22→22|8→8|通过 / 通过|
|mcp_unified_2|19→18|3→2|通过 / 通过|
|mcp_baseline_2|36→18|11→2|通过 / 通过|
|mcp_baseline_1|22→18|4→2|通过 / 通过|
|mcp_unified_1|34→16|12→3|通过 / 通过|

条件数统计实际结构中的触点和比较叶节点；共享项计一次，并联块按其叶节点计数。`mcp_unified_3` 多一个网络，是为了保留 `X1/M0` 子组和 `X1/M4/T1>=K100` 子组，减少重复展开；网络数不是单独的质量目标。每份程序的 IR 与读回的真实 CSV 指令流分别通过既有6项独立预期、16,511次扫描，沿用原先明确记录的一个扫描周期计时边界容差，撤防、回路记忆不使用该容差。

回归结果：14个相关Python测试文件共511项通过，包含Core、IR/CSV、候选、API/MCP一致性、范围和扫描器。最后补充并联公共项、注释保留及特殊寄存器副作用、优化区间求值后，归并/路径一致性/范围/生成边界184项通过；其中归并测试82项，含250组多扫描状态/写入轨迹生成式测试及60组与穷举连续划分对照的测试。上述集合有重叠，不相加。Web类型检查和构建通过，4项投影/组件渲染检查通过。最终代码重新准备全部12份候选，IR与已经验收的产物完全一致。

调试失败保留：旧测试将全部T触点和纯并联表达式都视为不可处理，已替换为对应稳定性/状态边界断言；生成式测试发现无显式条件的兼容分支拆分会改变现有IR累加结果，已禁止该变换。新增统计一度超出旧归并公开投影字段，已将统计留在可维护性元数据，恢复原投影契约。CMP存在已查证行为定义和未查证图表两种记录，已沿用Core对完整已查证行为的选取，不因未查证副本错误降级。扩展回归中的旧Web Agent测试仍假定问答入口可以提交修改，已按现有只读契约改为验证调用模型前拒绝范围修改，未放宽生产权限。

新CSV、注释CSV、逐项验收、原始输入、源码快照、失败和最终测试记录保存于独立[测量目录](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-condition-normalization/replay-results.json)。可下载[12份CSV交付包](C:/Users/Arienax/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/GXWorksAgent/measurements/2026-10-06-condition-normalization/condition-normalization-csv.zip)。

限制：归并搜索受现有扁平梯形图协议约束，不能声称任意嵌套布尔表达式全局最简；完整并联块、缓存边沿和未查证效果不会强行合并。局部编辑保持网络身份和顺序。适用性分类覆盖当前Core支持的FX3U/FX5U资料，效果回放仅为本报警FX3U样本，未进行GX原生导入/编译或实机执行。
