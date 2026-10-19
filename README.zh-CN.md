<h1 align="center">GXWorks Agent</h1>

<h3 align="center">面向三菱 PLC 工程的 MCP 工具服务与 AI 工作台</h3>

<p align="center"><strong>读取工程上下文 · 生成与审阅程序 · 驱动 GX Works2 原生工程与诊断</strong></p>

<p align="center">
  <a href="https://github.com/Arienax/gxworks-agent/releases/latest"><img src="https://img.shields.io/github/v/release/Arienax/gxworks-agent?style=flat-square&amp;color=2563eb" alt="Latest release"></a>
  <a href="#mcp"><img src="https://img.shields.io/badge/MCP-engineering_tools-7c3aed?style=flat-square" alt="MCP engineering tools"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/Windows-10%2F11_x64-0078d4?style=flat-square" alt="Windows 10/11 x64"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache--2.0-f59e0b?style=flat-square" alt="License: Apache-2.0"></a>
</p>

<p align="center"><strong><a href="https://github.com/Arienax/gxworks-agent/releases/latest">下载发布包</a> · <a href="#mcp">MCP 与 Web</a> · <a href="https://github.com/Arienax/gxworks-agent/issues">问题反馈</a></strong></p>

<p align="center"><a href="#quick-start">快速上手</a> · <a href="#ladder">Ladder</a> · <a href="#st">ST</a> · <a href="#fbd">FBD</a> · <a href="#native-session">原生会话</a> · <a href="#web">Web 工作台</a> · <a href="#diagnostics">诊断与验证</a></p>

<p align="center"><strong>简体中文</strong> · <a href="README.md">English</a> · <a href="README.ja.md">日本語</a></p>

GXWorks Agent 将三菱 PLC 的工程上下文、按 CPU 组织的指令事实、确定性静态校验与 GX Works2 原生操作封装为统一的工程工具链，支持 **Ladder（梯形图）**、**ST（结构化文本）**、**FBD（功能块图）** 以及已有 **SFC** 工程的受控编辑与诊断。

你可以通过 **MCP** 将 42 个工程工具直接接入 Codex、Claude Desktop、Cursor 等客户端，复用客户端自身的模型完成规划、生成、原生会话编辑与验证，并保存带版本的工程产物；也可以启动可选的 **Web 工作台**，在浏览器中生成程序、审阅图形差异、运行仿真测试并按授权交付到 GX Works2。

<a name="ladder"></a>

## Ladder：从工程上下文到可交付产物

![Ladder 上下文检索、指令依据、候选构造与产物保存](resources/assets/ladder-rag-core-demo.gif)

以 FX3U 输送线控制片段为例：先按工程型号检索 `WSFL`、`INC` 与 `T200` 的指令事实和手册依据，构造梯形图候选并通过 MCP 提交保存。Python Core 对候选执行结构与操作数检查、共享条件归并及可维护性审阅；保存后自动生成 Ladder JSON、PLC IR、SVG 梯形图、由 IR 派生的可追溯 ST，以及用于 GX Works2 导入的 MAIN 与软元件注释 CSV。针对已有版本的小范围修改，可通过 `read_network` 与 `patch_program` 按网络提交局部补丁。

动图根据实际 SDK/MCP 返回数据与生成产物绘制；候选程序由外部客户端依据返回上下文构造，并非内置模型后台自主运行的录屏。

<a name="st"></a>

## ST：结构化文本生成与原生工程编辑

ST 工作流兼顾独立文本生成与 GXW 原生工程维护：

- **Web 独立生成与审阅**：在工作台中分析需求、确认规格，结合所选 CPU 的指令上下文与 ST 调用签名生成 `program.st`，支持候选预览、版本间文本差异比对与产物保存。Ladder 工程也可从 PLC IR 导出可追溯的 ST。
- **GXW 原生 ST 工程读写**：通过 `read_st_project` 读取已有 `.gxw` 中的普通 ST Program、局部声明与任务归属；使用 `create_st_candidate` 导入现有 ST 工程，或在指定资源与现有任务下新建、编辑普通 ST Program，并在持续原生会话中与 ST FB、FBD 图形协同修改和编译。

本地 ST 检查器校验已支持的语法与语义并标出未覆盖构造；它是有界静态解析，不能替代 GX Works2 原生编译与 PLC 实机验证。

<a name="fbd"></a>

## FBD：从空白模板到复杂 GXW 工程编辑

![MCP 为已有 FBD 工程第一轴加入故障锁存、复位和延时使能](resources/assets/fbd-control-logic-demo.gif)

上图在三菱电机 [MotionControl JEC 示例](https://eu-assets.contentstack.com/v3/assets/blt5412ff9af9aef77f/blt9206f69c182f67c3/634ea5238602a36d8e991e63/MotionControl_JEC_a.zip)的第一轴中增补故障锁存、复位和 500 ms 延时使能：保留原有 FB 实现及 Axis 的 `IN_OUT` 连线，接入 SR/TON 逻辑，并将运动许可信号连入既有功率控制块。

除演示中的连线改接外，FBD 模块覆盖完整的工程构建与维护链路：

- **模板起步或导入现有工程**：内置 57 个按 CPU 区分的空白 FBD 起始工程，可直接按机型创建新工程，也可导入已有 `.gxw` 工程继续编辑。
- **参数级与图形级双模编辑**：既可依据 `get_fbd_catalog` 返回的参数 schema 直接提交 `calls` 与 `arguments`（由 Core 自动展开端口、`IN_OUT` 配对并生成布线），也可显式增删改节点、端子、正交连线与多网络结构。
- **定义编写、库导入与任务注册**：支持读取和编写项目结构体、ST/FBD 功能块及全局/局部声明表；支持从来源 `.gxw` 导入用户库定义（`read_fbd_libraries` / `import_library`）而保留当前调用程序；支持在来源工程允许的范围内新建 FB 与普通 Program，并将 Program 注册到既有任务、新任务或新资源，或调整系统软元件分配范围。
- **产物与验证解耦**：每次保存生成 `program.gxw`、`fbd.json`、`fbd.svg` 与写入报告。本地回读校验、原生保存、独立编译与代码检查分别执行，结果各自绑定对应工程版本。

<a name="native-session"></a>

## 持续可见的 GX Works2 原生工程会话

通过 `execute_native_engineering(mode="live")`，MCP 客户端可以在**同一个持续打开的 GX Works2 进程**中跨多次调用推进工程修改，而不必在每一步之间反复保存和重开文件：

- **同会话多步演进**：在同一会话内依次修改全局与局部标签表、新建或编辑普通 ST Program 与 ST FB、插入 FBD 实例调用，并基于当前活动文档的对象与端子身份改绑端子、移动节点或重连导线。
- **实时编译与作用域地址查询**：随时触发当前源码编译（`compile`），并通过原生交叉引用查询全局声明、Program 局部变量及 `实例.成员` 的编译器分配地址（`show_assignments`）。后续一旦继续修改源码，旧编译结果与地址会自动标记为 `stale`。
- **步进控制与中断恢复**：支持配置步骤停留时间（`dwell_ms`）、单步执行（`step_mode`）、暂停（`pause_native_engineering_session`）与恢复。会话显式区分活动修订（`revision`）、磁盘保存修订（`saved_revision`）与应用版本修订（`version_revision`）；超时或中断的步骤保留 `unconfirmed` 回执，通过 `rebind` 只读核对实际界面状态后再继续，不会盲目重放未确认动作。

原生工程会话要求 Windows 可交互桌面、GX Works2 `1.635.0.1` 以及操作员配置的执行许可。

<a name="web"></a>

## Web 工作台：Direct 生成、图形审阅与产物导出

![Web 工作台查看已保存梯形图、网络与软元件地址，以及导出菜单](resources/assets/web-gui-view-deliver.gif)

在工作台中直接配置模型 API，即可使用内置 **Direct 生成流程**完成需求分析、规格确认与程序生成，并在浏览器内查看梯形图网络、FBD/SFC 图形、软元件占用、版本差异及可导出产物。Direct 与 MCP 共用同一套工程上下文与 RAG 管线，可减少外部 Agent 编排开销；工作台内的工程问答仍可按需调用只读工具。

当需求缺少现场工艺事实时，每个待确认问题都会呈现为独立输入框，切换面板或重新加载不会丢失已填内容，填齐必答项后统一提交。生成阶段按模型接口能力启用严格结构化输出，并在进入 Core 校验前自动归一无歧义的格式差异。

## 工具链与架构分工

- **工程上下文与现场快照发现**：既可按 CPU 和程序类型创建、选择版本化工程，也可在不建工程的前提下调用 `list_gxworks2_sessions` 与 `read_gxworks2_snapshot`，直接读取本机已打开 GX Works2 窗口的只读程序快照并按网络分析。
- **CPU 作用域工程 RAG**：检索管线融合实体匹配、全文检索与 dense/LSA 向量检索。已知指令由结构化事实库提供精确操作数、步长与修饰规则，剩余检索补充手册依据与原创工程范例，并自动剔除重复上下文。
- **确定性 Core 校验与版本化产物**：Python Core 持有 PLC 领域语义，统一负责 IR 构建、操作数与时序检查、FBD 拓扑与声明核对以及产物生成。本地结构通过、原生编译检查、仿真观察与 PLC 实机行为严格区分。
- **受控的 GX Works2 交付**：按操作员配置的审批策略，将当前 Ladder 版本的 MAIN 与注释导入 GX Works2，或执行 FBD/ST 原生保存、编译与会话操作，返回绑定版本的操作回执。

外部客户端或 harness 负责模型推理与规划循环；GXWorks Agent 提供工程上下文、确定性工具、版本持久化与执行回执。

<a name="diagnostics"></a>

## 工程管理、诊断与仿真验证

| 功能模块 | 可完成的工作 |
| --- | --- |
| 工程与版本管理 | 按 CPU 和程序类型（Ladder / ST / FBD / SFC）创建或选择工程，维护需求与确认规格，审阅候选、对比版本差异并导出版本化产物。 |
| 运行中 GX 只读快照 | 发现本机已打开的 GX Works2 会话，无需先建 Agent 工程即可导出临时只读快照（`source_ref`），按网络读取指令并执行静态检查。 |
| 原生编译与源码级诊断 | 在隔离工作区对已保存的 `.gxw` 执行原生编译与代码检查，查询声明与 FB 实例成员的分配地址（`read_native_device_assignments`），比对已保存代码通道与新编译输出（`compare_native_compiled_code`），并将诊断或步号（`locate_native_code_steps`）定位到 ST 行、声明项、FBD 网络/对象或调用实例；位置存在歧义时保留 `unresolved`。 |
| SFC 已有工程编辑 | 导入已有 SFC 工程，读取流程图与程序结构，编辑既有动作（Action）与转换（Transition）子程序，修改受支持步的动作编号和跳转目标，预览候选与版本差异；不支持从零新建完整 SFC 工程。 |
| 梯形图仿真与实时时间轴 | 配置绑定版本的测试方案、软元件激励与预期结果并运行仿真后端；或通过 `connect_simulator` 与 `start_simulator_trace` 对已连接 GX Simulator2 的类型化信号进行只读采样，在内置的 Web 时间轴查看器（`simulator.html`）中回放波形。采样不执行未授权写入，并显式保留程序对应关系的 `unverified` 状态。 |
| PLC 只读监测 | 经操作员授权，通过已配置的 MX Component 逻辑站只读读取指定软元件并记录历史数值。权限绑定工程版本、地址范围与有效期且可随时撤销；此功能绝不向 PLC 写入。 |

<a name="quick-start"></a>

## 快速上手

在 Windows 10/11 x64 上，从[发布页](https://github.com/Arienax/gxworks-agent/releases/latest)下载所需发布包并完整解压。两个成品包均可独立运行，无需安装 Python 或 Node.js。

| 发布包 | 适用场景 | 启动入口 |
| --- | --- | --- |
| `GXWorks-Agent-MCP-windows-x64.zip` | 接入 Codex、Claude Desktop、Cursor 等 MCP 客户端，直接使用客户端模型 | `gxworks-agent-mcp.exe --local` |
| `GXWorks-Agent-Full-windows-x64.zip` | 同时包含 Web 工作台与 MCP，共用一套运行资源 | `start-web.cmd` / `gxworks-agent-mcp.exe --local` |

`MCP` 精简包不含 Web 服务端、模型 SDK、离线 PDF 解析依赖与演示动图，体积更精简，完整保留工程工具、RAG 检索、工程模板、原生辅助程序与仿真时间轴查看器。`Full` 包内置两个入口：其中的 MCP 既可用 `--local` 独立运行，也可用 `--service` 连接已启动的 Web 工作台，共用工程与审阅流程。

<a name="mcp"></a>

### 独立 MCP 模式

在客户端中添加 stdio 服务：命令填写解压目录中 `gxworks-agent-mcp.exe` 的完整路径，参数填写 `--local`。刷新连接后，先调用 `list_projects` 选择已有工程，或按实际 CPU 与程序类型创建新工程；若要直接分析本机已打开的 GX Works2 工程，可先调用 `list_gxworks2_sessions`。

你可以用下面这段提示词快速体验：

> “新建一个 FX3U Ladder 工程，先读取生成上下文和相关指令依据。实现 X0 每次按下计数一次，计满 3 次点亮 Y0，X1 复位。保存候选版本，列出生成的产物和剩余诊断。”

外部客户端使用自己的模型，无需启动 Web。GX 导入与原生操作遵循已配置的执行许可；需要图形化审阅或共用工程时，可随时搭配 Web 工作台使用。

### Web 工作台模式

运行 `Full` 解压目录中的 `start-web.cmd`，按提示选择工程工作区目录。新建工程后，在 **设置 → 模型 → 模型 API** 中配置模型服务即可生成程序；浏览已有工程、查看图形与导出产物无需调用模型。版本更新时，请将新压缩包解压到新目录，独立保存的工作区与个人设置会自动保留。

### 从源码运行

安装 Python 3.13 与 Node.js 22.14（或更新版本），然后执行：

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent
.\build-web.bat --no-pause
.\start-web.cmd
```

若仅从源码运行独立 MCP，可创建 `.venv-mcp` 并安装 `requirements/mcp.txt`，直接通过 `gxworks-agent-mcp.cmd --local` 启动，无需安装 Node.js。

### 工程模板与知识资源

成品包的 `gxw/templates/`（源码路径为 `src/gxw/templates/`）包含 FX3U 基线工程、节点与语法模板，以及 57 个按 CPU 区分的空白 FBD 工程；迁移或复制应用目录时请完整保留。提供空白模板不代表对应 CPU 均已通过原生编译或真机运行验证。

成品包内置经整理的运行事实库与项目原创案例供 RAG 检索使用，不包含三菱原始手册 PDF 及原文索引。运行知识数据与源码仓库分离分发；从源码运行时，可将成品包内的 `knowledge/` 复制到 `resources/knowledge/`，并将 `resources/instructions/mitsubishi/` 复制到源码同名目录。

原生保存、代码检查、GX 导入和仿真依赖本机安装相应的三菱软件；PLC 只读监测依赖 MX Component 及已配置的逻辑站。发布包不包含任何三菱商业软件。

## 设备投运前

请务必对照目标 CPU、实际外部接线、机构运动边界与运行模式逐项审查生成逻辑。导入前请先备份目标 GX 工程，并在目标 GX Works2 环境中完成编译与检查后再进入现场调试。**发送到 GX Works2 仅更新工程文件，不等于向 PLC 下装程序。**

急停、安全门联锁、超程限位等安全功能必须由独立的安全回路或安全控制器承担，生成的应用逻辑与软件检查不能替代硬件安全系统。公开分享诊断记录前请先脱敏，切勿泄露 API 凭据或本机会话链接。

## 资料与许可

[版本更新记录](CHANGELOG.md) · [贡献指南](CONTRIBUTING.md) · [问题反馈](https://github.com/Arienax/gxworks-agent/issues) · [安全政策](SECURITY.md)

项目贡献者拥有权利的 Core、Web、MCP 与原生适配器源码采用 [Apache License 2.0](LICENSE) 开源，具体适用范围见[许可范围](LICENSE-SCOPE.md)与[第三方声明](THIRD_PARTY_NOTICES.md)。各贡献者保留其版权；此前已发布的版本仍适用其发布时的许可证。
