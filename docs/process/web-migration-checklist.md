# Web 迁移交付核对表

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../README.md)进入。

> 2026-09-21 更新：Qt 已按[退役审查](qt-retirement.md)主动退役。下文保留迁移时期的行为基线、限制和验收记录；其中“保留 Qt”“全部验收前不删除”不再是当前发布要求。未完成的 Windows/GX/Simulator2 现场验收仍然未完成，不能由该实验删除或离线回归推定为通过。

本表区分代码实现、隔离回归和实际环境验收。迁移分支与迁移前固定输入证据见[行为基线](web-migration-baseline.md)。没有完成的实机/交付项必须保留；不得用模拟后端通过或前端构建成功替代 Windows 现场验收。

## PR 0–6 对照

| 阶段 | 实现与可检查证据 | 仍需实际验收的部分 |
| --- | --- | --- |
| PR 0 行为基线 | 基线文档记录原 main 提交、完整测试结果及 MCP 工作区不变烟测；沿用现有固定 Provider/IR/规格夹具 | 对真实工作区先备份、只读核对索引与产物字节；不得用生产工程做写入测试 |
| PR 1 工作流抽取 | `application/generation.py`、`review.py`、`planning.py`；Qt 保留信号/显示适配；`test_generation_workflow.py`、`test_application_planning.py` 验证无需创建窗口、冻结模型和语言、原修复/失败策略 | Qt 完整人工使用流程和模型服务实测；该实验不删除 Qt 入口 |
| PR 2 只读工作台 | FastAPI 资源接口、能力声明、React/Vite 页面、已有 SVG/ST/报告显示；SessionStore 显式只读，旧版本读取禁止迁移 | 在真实旧工程检查无修改、视图/长报告/缺产物反馈；后端和前端启动都不能拉起 GX/网关 |
| PR 3 生成与审批 | 后端任务/事件、冻结快照、持久提案、规格/基础/候选哈希校验、幂等审批和冲突；`test_application_persistence.py` 包括重复确认、过期输入、重启中断与事件补齐 | 浏览器双标签页冲突、刷新与断网恢复的真实交互；原始 Provider token 不得提前显示 |
| PR 4 GX/仿真执行 | 单执行线程、COM 配对、跨进程桌面锁、Qt 共用资源锁；复用原仿真/Debug 服务；显式读取形成往返校验候选，检查返回同步差异；`test_execution_coordinator.py`、`test_execution_read.py` 覆盖互斥、原生 CSV、无隐式保存、环境不可用 | **尚须 Windows 实机完成导入、读取/同步、模拟器基线、测试、回滚与锁屏/RDP 情况回归**；高级冲突解决交互须逐项比对 |
| PR 5 MCP 桥接 | 默认独立 stdio 不写入；显式 loopback 服务模式、Agent 独立凭据、原 registry schema、持久提案 ID；`test_mcp.py`、`test_mcp_service.py` 和真实 stdio 子进程测试 | 外部客户端 → 本地服务 → Web 人工审查的完整交互；Agent 不能批准或绕过工程锁 |
| PR 6 功能与包 | 附件、模型配置增删/采样和厂商参数、独立密钥写删/显式连接测试、旧配置只读投影、响应语言、SFC 需求输入、报告接口及独立 `web.spec`/构建脚本；双击启动器选择工作区、默认只读、服务就绪后打开本地登录；原 Qt 依赖保留 | 设置后端有 `test_application_settings.py` 隔离回归；界面对照、模型服务连接实测、键盘/大工程/多语言反馈、完整 Windows 发布包与实际网关资源仍须核对；**完成所有验收前不移除 Qt** |

## 迁移阻断用例

| 用例 | 正确结果 | 证据入口 |
| --- | --- | --- |
| A/B 标签页分别选择不同活动版本后，批准旧候选 | 明确冲突，不能覆盖较新活动版本 | 提案基础快照与 HTTP 409；`test_application_persistence.py` |
| 同一个批准请求重复发送/并发发送 | 一个本地版本或一次外部执行；重复结果可查 | 同文件的并发审批与幂等用例 |
| 生成过程中刷新、切换项目或修改设置 | 后端任务继续使用提交快照；按任务 ID/事件序号恢复 | `jobs.py`、`test_generation_workflow.py` |
| Provider 拒绝、响应语言不符或验收失败 | 不发布未接受内容，不生成错误产物/提案 | 生成、语言、streaming 回归 |
| 已批准 GX 导入过程中服务退出 | 重启标记中断，要求核对现场；不自动重复操作 | 提案恢复与外部副作用回归；真实故障演练另做 |
| GX/MX/Simulator2 不可用或桌面资源被占用 | `unavailable`/失败，绝不显示 passed | 协调器、仿真工作流和仿真证据测试 |
| Qt 和 Web 在同一工作区争夺写入 | 第二个写入方被工作区锁拒绝 | `WorkspaceWriterLock` 进程/线程测试 |
| 不同工作区仍操作同一 GX 桌面 | 共享桌面资源互斥；不并发 GUI 自动化 | 协调器跨进程锁与 Qt 入口测试 |
| 旧工程只读查看 | 原文件不变，不创建/迁移版本，不调用模型/GX | 默认 MCP 不写入、无 Qt 应用服务测试与 Web 只读回归 |
| 无 modelProfiles 的旧设置或未创建 config.json | 读取时在内存中转换，不写配置/不搬密钥；原模型可用于冻结快照 | `test_application_settings.py` 旧配置与默认模板用例 |
| 设置密钥/厂商扩展与连接测试 | 密钥只走操作员专用命令；嵌套凭据拒绝，连接测试限时且不保存草稿或密钥 | 同文件的公开投影、模型快照、路由权限与失败脱敏用例 |
| Agent 使用服务连接提出候选 | 后端保存提案，返回完整公开投影及 proposal_id；没有审批权限 | MCP 服务真实 stdio/HTTP 及 Web 认证回归 |
| 构建发布包时网关缺失 | 默认阻止完整包；只有显式许可才能产出不含网关的包 | `build_web_package.ps1` 与发布目录检查 |

## 发布验收记录

发布负责人填写日期、工程样例、GX Works2/GX Simulator2/MX Component 版本和证据文件位置。以下状态不能由离线单元测试自动改成完成。

- [x] 浏览器工作台完成需求确认 → 生成 → 差异预览 → 接受本地版本，刷新后状态一致。2026-09-10 在隔离 Provider 和临时工程中实际操作，v0001 → v0002；使用隔离 Provider；未运行真实模型或 GX。
- [ ] 外部 MCP 客户端以 Agent token 提案，Web 操作员批准；Agent token 请求操作员路由被拒绝。
- [ ] 导入指定版本后检查 GX 当前程序和注释，确认结果只表示导入，不伪称原生编译。
- [ ] 真实 Simulator2 按指定方案运行并保存版本/IR/方案绑定的轨迹与验证结果。
- [ ] 调试候选失败后验证原程序恢复、回归证据和当前版本状态。
- [ ] 实际退出/锁屏/RDP 断开情形的不可用提示与中断恢复，未自动重复 GX 操作。
- [ ] 旧 Qt 同步基线/拉取/回滚/报告功能与 Web 对照完成，遗漏功能明确列出。
- [x] 发布目录启动成功，静态资源、知识库/指令、语言包、默认配置、第三方声明和网关都能定位。2026-09-10 包级烟测通过，网关仅检查文件，未启动。
- [ ] 普通 Windows 用户双击启动、选择工作区与模式、浏览器登录和 Ctrl+C 停止均完成核对；取消选择及端口占用有明确反馈。
- [x] 新包不带用户工程、API Key 或操作员/Agent token；无需 Qt 即可启动 Web。独立安全配置及显式资源收录，发布目录未发现用户配置/工程文件，PYZ 检查确认排除 Qt。
- [x] 全量回归、MCP 烟测和前端生产构建通过，记录实际命令与结果，见下方。

### 2026-09-10 代码与浏览器验收

`.venv/Scripts/python -m pytest -q`：1236 passed、3 skipped、1 个第三方弃用警告，44.42 秒。跳过项为依赖运行中 GX 的检查及当前 Windows 无权限创建的符号链接用例；不计为通过。`scripts/mcp_smoke.py`：12 个注册工具、候选 `confirmation_required`、版本数 0、`workspace_unchanged=true`。OpenAPI 从实际路由重新导出，前端类型重新生成，`npm --prefix web run build` 的 TypeScript 和生产构建通过。

在浏览器中实际检查了分析中刷新、任务恢复、显式查看规格草稿、确认规格、生成、冻结差异、接受本地版本及刷新后 v0002 保持一致；并检查了 ST、已保存报告、SFC 需求输入和语言切换。模型设置的新增配置、采样参数保存、成功/认证失败连接测试、清除密钥、删除配置使用真实设置服务和内存凭据探针，未访问外部模型。整页深浅主题覆盖侧栏、工具栏、对话区、弹窗、下拉菜单和 SVG 内部，保留主题选择；SVG 主题转换不改产物或 IR。

提交按 PR 0–6 归档。共享工作流、工作区锁和 GX 资源锁在前置基础提交中一并引入，HTTP、前端、MCP 与打包随后接入；阶段编号表示实现分组，不表示远端 PR 已发布或所有现场验收已完成。

### 2026-09-10 Windows 包级证据

在 Windows 11、Python 3.13.14、PyInstaller 6.21.0 上，通过 `scripts/build_web_package.ps1 -SkipInstall -GatewayDirectory <本地已编译网关目录>` 重建完整目录 `dist/GXWorks-Agent-Web`。该次构建重新执行 TypeScript 检查和 Vite 8.2.2 生产构建，均通过。发布目录共 297 个文件，约 213.76 MiB；其中包含 9 个知识库文件、3 个 Mitsubishi 指令文件、中英文 README、完整 Web 指南和 `start-web.cmd`/`scripts/start_web.ps1`。

前端资源为 `index-B6qxI_Tq.js` 和 `index-Bo57J5XO.css`。主程序 SHA-256 为 `5042641B6EDEF963D1D06178DA659618635C9C7C9FEE957A4C76AE627630889D`；随包网关 SHA-256 为 `756ED495CD1CDF6DE3F36AA005CAECF6E9F461493F51ED87111C5128FE6A5336`。网关来自该实验对仓库 C# 源码的本地编译，没有下载或运行它。

`python scripts/web_package_smoke.py` 实际启动发布包可执行文件，并通过只读临时工作区完成本机 HTTP 登录、首页及两个静态资源加载；工作区路径仍不存在，进程由烟测脚本关闭。结果为：

```json
{"ok":true,"packaged_server_started":true,"static_assets_loaded":2,"operator_login":true,"read_only_workspace_unchanged":true,"qt_modules_excluded":true,"gateway_binary_bundled_not_started":true,"native_gx_not_tested":true}
```

另以 Windows PowerShell 5.1 执行发布包启动器的 `-Workspace <临时不存在路径> -ReadOnly -NoBrowser -ValidateOnly`，确认识别发布包、参数校验通过、不打开浏览器、不启动服务、不创建工作区。文件夹对话框、真实默认浏览器及 Ctrl+C 的普通用户交互仍保留人工核对项。上述证据仅覆盖包级交付；真实 GX Works2/GX Simulator2/MX Component、PLC、锁屏/RDP 和设备回滚均未执行，不改变其未验收状态。

该实验引入应用服务与独立 Web 入口，未引入 FBD 编辑器、GX SFC 编译、云端 GX 执行、嵌入式 Codex Harness 或真实 PLC 写入。SFC 仍是需求文本输入；结构化语义模型仍是实验性只读模型。

兼容限制：含语义目录未覆盖的原生 vendor 指令时，Web 读取明确返回 `unsupported`，不能进入通用候选审批。现有 Qt 的原生保真读取继续可用；不应把这一类工程的完整迁移标为通过。


### 2026-09-10 真实使用反馈修复

在用户实际运行的 Windows 发布包、Edge、`test` 工程和 GLM-5.3-Flash 上逐项点击确认：旧规格页的程序形式选择会仍显示第一项且丢失 `selected_approach`；参数候选变成空白文本框；确认问题位于 I/O 分配之后。修复后实际完成两种程序形式切换、展开并点选 X/Y 候选、自定义填写列表外地址、漏填时字段下提示、规格连续保存，以及深浅主题下的候选菜单检查。测试规格以 X0 启动、X1 常闭停止、Y0 输出保存；只保存本地规格，没有创建程序版本或操作 GX/PLC。

共享草稿保留结构化候选与建议值，历史分析输出在读取时恢复候选但不写原文件；程序形式使用核心 `approach_id`。确认问题移到 I/O 分配上方；候选允许自定义答案。规格保存先校验原草稿，保留常开/常闭答案并返回实际保存的哈希，重复保存不产生虚假冲突。

同一真实页面再次提交“起保停”时捕获 `reasoning:latin_prose`，正文未报告违规。此前两次历史失败没有保存具体原因，不能断言都由同一原因造成。新增安全诊断仅包含固定原因码和经过投影的字段位置，不公开原始失败响应。相关模型接受策略与后续验收见响应语言文档。


最终修复检查：全量 `pytest -q` 为 **1277 passed、3 skipped、1 个已有第三方弃用警告**（47.52 秒），前端 TypeScript/Vite 构建通过。MCP 烟测仍返回 12 个共享工具、候选 `confirmation_required`、`workspace_unchanged=true`。完整更新包通过独立暂存构建和包级烟测，最终 EXE SHA-256 为 `C4F2D7F7EBD9B80A316D9B48B61A39E6CF97B6B2AFED8E12382B2450F69C9AC9`，前端资源为 `index-Z6vJu4Od.js` / `index-CEkuyu2G.css`。仅保留原 `config.json` 后更新运行包，沿用原工作区和私有状态目录，Edge 由服务启动入口恢复。


最终包在用户 Edge 中实际重新发送“起保停”，任务成功完成并显示 FX3U 起保停分析摘要；随后打开规格，实际用键盘上/下方向键及 Enter 切换候选，再恢复原测试答案。浅色和深色菜单均通过截图目视检查。页面保留新分析草稿供用户继续修改，新分析没有自动覆盖已确认规格；旧失败记录继续保留。
