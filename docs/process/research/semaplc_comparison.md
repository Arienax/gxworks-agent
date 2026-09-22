# SemaPLC 对照研究与仿真验收改进

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

研究日期：2026-09-09。上游固定为 [midea-ai/SemaPLC @ 1c41c1b](https://github.com/midea-ai/SemaPLC/tree/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe)，提交日期 2026-08-25。本地基线为 feat/gxw2-skill-rag @ d1b78e8，包含此前尚未提交的响应语言验收改动。该实验阅读了上游源码、测试、技能文档和论文；未部署或实测 OpenPLC。

最值得吸收的是：通过结论必须来自可核验的执行记录，且失败阶段应决定下一步处理。该实验将这些原则落实在现有 SimulatorRegressionService、SessionStore 和 DebugPatchLoopService 中，继续使用现有 PLC IR、Test DSL、GX 导入服务及确认流程。

**论文与公开实现的边界**

论文的 Algorithm 1 / §4.5 描述三项原则：有限重试、编辑使旧验证失效、以工具日志核验结论。这是值得采用的设计目标。[论文原文](https://arxiv.org/html/2608.18565v1)

公开应用中可以确认的强制机制更具体：verify runner 保存执行信封和源码快照，子集运行不更新完整验收记录；buildSimulation 检查最近记录的源码指纹和通过状态，也提供显式跳过参数。不能把这些实现概括为“所有自然语言规格、编译和行为检查都由一个不可绕过的统一门保证”。[runner](https://github.com/midea-ai/SemaPLC/blob/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-tools/src/verify/runner.ts)、[buildSimulation](https://github.com/midea-ai/SemaPLC/blob/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-tools/src/tools/buildSimulation.ts)

此外，构建技能在预算耗尽时建议保留能通过的最小测试集以生成仿真，同时披露未验证项。这是上游交互产品的取舍。本项目已经批准的补丁回归集不能在执行时缩减后仍称为完整通过。[构建与验证技能](https://github.com/midea-ai/SemaPLC/blob/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-web/templates/.sema/skills/plc-build-and-verify/SKILL.md)

| 方向 | SemaPLC 中可吸收的设计 | GXWorks Agent 已有基础与判断 | 该实验处理 |
| --- | --- | --- | --- |
| Verification gate | 由执行记录决定完成；子集验证不能替代完整验收。[runner 测试](https://github.com/midea-ai/SemaPLC/blob/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-tools/tests/verify/runner.test.ts) | 已有候选校验、编译、确认、导入、回归和回滚，但最后启用候选仅检查回调的 status。 | 改为重读已落盘证据，核对候选版本和完整批准套件后才启用。 |
| Spec review | 按需求核对覆盖、阈值等号、互斥和优先级、跨扫描状态及复位。[规格评审技能](https://github.com/midea-ai/SemaPLC/blob/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-web/templates/.sema/skills/plc-spec-review/SKILL.md) | 已有可编辑规格、必填项和地址校验、生成约束、版本规格快照及程序评审。模型逐条自查仍是建议，不能自行产生“规格已证明”的状态。 | 保留现有硬校验与建议分层；未增加依赖模型自报的硬门。 |
| Skills / patterns | 工作流技能附带按拓扑选择的具体 ST 示例，适合作为局部实现起点。[模式目录](https://github.com/midea-ai/SemaPLC/tree/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-web/templates/.sema/skills/plc-build-and-verify/st-patterns) | 已有任务和机型路由、结构化指令知识、带来源的检索及限定范围的 gxw2-skill 辅助排序。ST 示例的“可编译”不证明改写后的行为，也不证明适用于 FX。 | 不导入 OpenPLC 模板或另建知识库；继续由本地机型和指令校验决定合法性。 |
| Failure taxonomy | 区分计划、编译、部署、运行环境、工况准备、断言、版本冲突及超时；工况没成立时不盲目改程序。[失败类型](https://github.com/midea-ai/SemaPLC/blob/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-tools/src/verify/planTypes.ts) | 原先已有 passed / failed / error / unavailable，并禁止环境错误触发补丁；细分原因和证据异常仍欠缺。 | 新增验收类别、后续动作和是否允许进入程序修复的标志；区分输入写入失败与执行错误。 |
| Runtime evidence / version binding | 源码哈希绑定信封；并行部署检查运行时程序 MD5；录波区分匹配、冲突和无法核验。[部署检查](https://github.com/midea-ai/SemaPLC/blob/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-tools/src/verify/cliEntry.ts)、[录波检查](https://github.com/midea-ai/SemaPLC/blob/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-tools/src/tools/record.ts) | 已有 project/version/revision/IR hash、追加式测试记录、GX 导入保护。原先读取时会合并两份绑定，执行结果也没有测试契约指纹。 | 增加执行前快照、套件和单项测试指纹、完整结果指纹，以及索引与两份证据的相互核验。 |

**为什么不直接移植规格规则和模式**

上游将“声明的输出没有赋值”做成编译前检查，说明适合确定性判断的规则可以从技能中下沉到代码。但其实现针对 ST 的 located 输出和赋值语法，不能直接用于 FX 梯形图。[输出检查实现](https://github.com/midea-ai/SemaPLC/blob/1c41c1bcb69bb43c51d7d0faed30a2b1930d67fe/sema-plc-tools/src/tools/detectIO.ts)

本地已经区分普通 COIL 与合法 SET/RST 写入，也为 HMI、预留和外部使用的 I/O 保留例外。将“所有输入必须使用”“所有 CASE 必须 ELSE”直接变成通用硬错误，会改变已确认需求或引入误报。具体依据见 [confirmed_spec](https://github.com/Arienax/gxworks-agent/blob/d1b78e8/src/confirmed_spec.py)、[plc_workflow_review](https://github.com/Arienax/gxworks-agent/blob/d1b78e8/src/plc_workflow_review.py)、[pattern_library](https://github.com/Arienax/gxworks-agent/blob/d1b78e8/src/pattern_library.py) 和 [knowledge_retriever](https://github.com/Arienax/gxworks-agent/blob/d1b78e8/src/knowledge_retriever.py)。

后续值得做的是给已确认需求条款分配稳定 ID，再关联评审发现和经确认的测试。当前测试上下文主要来自程序 IR；即使所有测试通过，也不能由此推导出全部自然语言需求都已覆盖。该实验没有虚构这种覆盖关系。

**已经落实的验收契约**

1. 在执行前固定 project_id、version_id、revision、ir_sha256、suite_sha256；保存前重新比对。批准计划的绑定不符时，在准备仿真环境之前拒绝执行。
2. 执行器在套件结果和单项结果中记录相应测试契约指纹。保存记录使用 evidence_schema_version=2，并为完整结果生成 result_sha256。仅在保存时补加绑定的调用标为 persistence_snapshot，不能获得执行验收资格。
3. 完整通过要求：测试身份和数量一致、全部执行完成、应有断言逐项出现、结果布尔值与汇总一致、存在相应观测及首末采样、设备值有效、后端身份一致。仅发送输入的任务可以完成操作，但不能因此通过行为验收。
4. 读取时分别校验索引、套件绑定和轨迹绑定，核对内容指纹及当前 IR，不再用字典合并覆盖来源。旧记录只读返回 legacy_evidence，不补写哈希、不迁移旧 IR，也不重新授予自动修复或候选启用资格。
5. 候选启用前重新读取记录，核对批准套件、候选 IR、回调结果和落盘结果。缺失记录、缩减测试、引用其他版本或篡改结果都会拒绝启用，并沿用已有回滚流程。

实现入口：[验收规则](https://github.com/Arienax/gxworks-agent/blob/d1b78e8/src/simulator/verification.py)、[版本证据存储](https://github.com/Arienax/gxworks-agent/blob/d1b78e8/src/session_store.py)、[执行服务](https://github.com/Arienax/gxworks-agent/blob/d1b78e8/src/simulator/service.py)、[调试闭环](https://github.com/Arienax/gxworks-agent/blob/d1b78e8/src/plc_debug_loop.py)。

| 验收类别 | 处理方向 | 允许进入程序修复 |
| --- | --- | --- |
| assertion / invariant | 同时复核程序与测试，再依据证据提出局部方案 | 是；仍需原有确认 |
| environment | 修复仿真环境 | 否 |
| setup | 检查初始化和输入驱动 | 否 |
| runtime | 检查运行器、读取及执行错误 | 否 |
| incomplete | 补齐观测并重跑完整套件 | 否 |
| evidence_invalid | 排查证据不一致并重跑 | 否 |
| version_conflict / legacy_evidence | 针对当前版本重新获取证据 | 否 |

**保证的范围**

这些哈希用于关联和检测不一致，不是防止有文件写权限者同时重写全部证据的数字签名。execution_snapshot 证明该实验执行使用的应用层版本和测试上下文；GX 网关目前没有与上游 live MD5 等价的运行映像指纹证明，也没有新增运行时独占锁。不能把 IR 绑定描述成已经独立核验了真实设备内部程序。

内存后端仍明确记录 test_memory_not_plc_simulator。自动测试只验证执行、验收和回滚控制流程，不证明梯形图在 GX Simulator2 或真实 PLC 上运行正确。该实验没有操作真实设备，也没有引入 OpenPLC、新的代理架构、模型调用或外部 MCP 写入能力。首次生成的候选仍需确认；静态编译通过没有被改称为运行验证通过。

**验证证据**

先增加了“没有落盘记录但回调返回 passed”的回归测试，旧实现实际错误启用了候选；修复后该测试通过。新增测试还覆盖缩减批准套件、跨版本结果、回调与磁盘不一致、缺失/重复断言、空观测值、执行期间版本变化、后端身份篡改以及旧证据只读兼容。

测试入口：[验收测试](../../../tests/test_simulator_verification.py)、[调试闭环测试](../../../tests/test_plc_debug_loop.py)。该实验新增 41 个测试场景；最终全量测试 997 passed、2 skipped（依赖 GX 窗口/工程状态的检查）。MCP 烟测通过，12 个共享工具、候选仍为 confirmation_required、workspace_unchanged=true。git diff --check 通过；上游 OpenPLC 环境未运行。
