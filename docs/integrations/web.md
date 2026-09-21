# 本地 Web 工程工作台

Web 工作台通过本机 FastAPI 应用服务复用 `ToolRuntime`、PLC Core、生成/评审工作流和 SessionStore。浏览器显示后端生成的 SVG、ST、诊断和工程记录；它不实现 PLC 语义，不创建 Qt 窗口，也不通过 MCP 操作自身后端。

源码入口为 `python -m integrations.web`，仅监听 `127.0.0.1`，固定一个 Web worker。Qt 客户端已退役，Web 不依赖 Qt。代码和离线回归可在没有 GX/MX 软件的环境检查；Windows 真实导入、仿真、桌面锁屏恢复与发布包验收仍须按[迁移核对表](../architecture/web-migration-checklist.md)单独执行。

## Windows 发布包：双击启动

1. 把整个 `GXWorks-Agent-Web` 目录解压到本机可读取的位置，不要只移动其中的 `.exe`。发布包无需另装 Python、Node.js 或 Qt。
2. 双击目录内的 `start-web.cmd`，选择工作区文件夹。已有工作区应选择包含 `index.json` 和 `projects` 的外层目录；新工程可以选择一个空文件夹。取消选择不会启动服务。
3. 不再选择只读／操作员角色，默认打开可编辑工作区。已有其他服务正在编辑同一工作区时，先正常关闭该服务。审批模式在网页设置中调整。
4. 服务准备好后，浏览器自动打开本地工作台。若未自动打开，复制启动窗口中的 `Workbench link` 本地链接到浏览器。使用期间保持启动窗口打开；结束时按 `Ctrl+C` 停止服务。

关闭浏览器不会停止服务。登录链接只供本机操作员使用，请勿分享。启动器默认使用端口 `8765`；已被占用时自动选择空闲的本机端口，并显示实际登录链接。启动或浏览项目不会自动运行 GX Works2、Simulator Gateway 或操作 PLC；GX 导入、仿真和调试按网页设置中的审批模式执行；启动本身不会执行旧的待审批操作。

若启动窗口提示“源码环境尚未安装”，说明打开的是源代码目录，请完成下一节安装，或使用完整发布包。若提示工作区被占用，正常关闭原来的 Qt/Web 写入服务后再试。不要删除占用锁来绕过正在运行的任务。

## 源码启动

在仓库根目录建立 Python 3.10+ 环境，安装 Web 后端依赖，再运行根目录 `build-web.bat` 构建前端。Web 依赖与原 Win7/Qt 环境分开维护：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements/web.txt
.\build-web.bat --no-pause
$env:PYTHONPATH = Join-Path (Get-Location) "src"
python -m integrations.web --workspace "D:\PLCWorkspaces\my-workspace" --port 8765 --open-browser
```

`build-web.bat` 固定执行 `npm ci`、`npm run types` 和 `npm run build`，输出到 `web/dist`。完成依赖安装和前端构建后，源码目录也可双击 `start-web.cmd`；它使用本目录的 `.venv`。命令行不带 `--open-browser` 时只输出登录链接，不打开浏览器。需要固定工作区或端口，也可调用 `scripts/start_web.ps1 -Workspace "D:\PLCWorkspaces\my-workspace" -Port 8765 -ReadOnly`；加 `-NoBrowser` 只显示链接。

启动后控制台会显示只供当前操作员使用的本地登录链接。令牌放在 URL fragment `#token=...`，登录后换为 HttpOnly、SameSite=Strict 的操作员会话。若需要固定启动凭据，可以提前设置 `PLC_WEB_OPERATOR_TOKEN`；不要把登录链接、模型密钥或令牌写入项目文件或提交到 Git。

旧工作区首次核对应使用：

```powershell
python -m integrations.web --workspace "D:\PLCWorkspaces\my-workspace" --read-only --port 8765
```

只读模式不启动任务管理器、提案写入或 GX 执行，不自动迁移旧版本。可写模式的任务、提案和暂存产物默认放在工作区父目录下的 `.gxworks-state/<workspace-hash>`，也可使用 `--state-dir` 指定工作区外的私有目录。工程索引、版本和已接受产物仍使用现有 SessionStore 格式。

一个工作区只有一个写入负责人。Qt 与 Web 不能同时以独立写入方打开同一工作区；切换入口前需等当前任务到达安全检查点并正常关闭。不要使用 Uvicorn 多 worker、热重载或 Windows 后台服务来运行真实 GX 操作。

## 操作与审批

项目和版本是工程状态的主入口。需求分析先形成可检查的规格，再确认并生成；程序校验通过后自动保存为新版本并显示梯形图，无需二次点击接受。失败或方案冲突不会保存为活动程序。顶部“导出文件”下载实际存在的 CSV、注释、SVG、JSON、IR、ST 或 GXW 产物；“刷新结果 / 重绘梯形图”不调用模型。

在“设置 → 常规 → 操作审批”选择：逐项审批（默认，GX／仿真／调试执行前确认）；替我审批（按确定性规则自动批准绑定版本的仿真／调试，GX 导入仍确认）；完全访问（显式确认后自动批准这三类已支持操作）。模式不是 Windows 安全隔离，也不会放宽校验或开放任意命令。策略版本冲突会拒绝保存；升级权限不会执行旧提案，降级会阻止尚未启动的自动操作。

授权动作分别记录：

| 提案动作 | 获批准后执行的范围 | 不可推断的结论 |
| --- | --- | --- |
| `accept_local` | 内部自动保存事务；兼容旧草稿／默认外部 Agent 提案的显式保存 | 没有 GX 导入或 PLC 写入 |
| `gx_import` | 把指定版本的托管 CSV 导入 GX Works2；FBD 版本则打开 GXW 工程副本 | 导入完成不等于原生编译或仿真通过 |
| `simulation` | 导入指定版本并运行指定已保存测试方案 | 环境不可用、未执行或执行错误不能显示通过 |
| `debug` | 执行指定版本和失败证据绑定的调试方案，沿用现有回归与回滚策略 | 不能绕过候选哈希、版本和仿真证据校验 |

批准时浏览器只提交提案 ID 和 `accept`/`reject`。后端重新检查活动版本、规格、候选与基础产物哈希；重复提交不会产生第二次执行。另一标签页改变版本或规格后，旧提案返回冲突。执行中进程退出会留下“中断，需要核对”的记录，重启不会自动再次导入或重复运行外部操作。

Web 顶部通过 CSV“发送到 GX”时，每次先显示手动备份提醒、本地项目与待发送版本。“取消”不创建提案；“继续发送”同时确认本次发送，无需再进入预览页审批，完全访问模式下也保留此提醒。确认后使用原有提案/批准接口和桌面队列，依次校验 CSV 与 GX 目标、导入 MAIN、导入软元件注释、保存工程。发送前不再自动导出 MAIN 或注释，也不读取旧基线或因 GX 内容存在外部修改而阻止发送；请先自行备份目标 GX 工程。

执行提案请求中的 `manual_backup_acknowledged` 默认 `false`，只有上述普通 CSV `gx_import` 接受 `true`；该字段固定在提案载荷中，本身不替代执行审批。共享导入 API 的 `pre_import_policy` 默认为 `protected`，此路径显式使用 `manual_backup`。Agent/MCP、旧提案、高级同步、仿真、调试和 FBD 保持原流程。CSV 校验、版本/产物绑定、跨域检查和桌面资源锁仍有效。

快速发送的原始返回值中备份路径为空、`backup_performed=false`，`details.timings_ms` 记录实际执行阶段与总耗时。Web 任务和提案结果通过 `gx_import_summary` 保留策略、是否自动备份和阶段耗时，过滤文件路径等私有信息。两项导入成功后按目标内容摘要记录同步基线，不额外回读；记录失败仅警告，导入成功仍不表示已核验编译或仿真。注释失败明确报告程序已导入，保存失败提示手动保存，均不自动重试。

GX 导入、仿真和调试统一进入 `GXExecutionCoordinator` 固定单线程队列，COM 的初始化和释放发生在同一线程，桌面资源另有跨进程锁。删除 Qt 不删除 GX 桌面资源锁、COM 生命周期或其他跨进程互斥。打开网页及环境查询只观察环境，不启动 Simulator Gateway。真实执行仍需要已登录且可交互的 Windows 桌面、GX Works2、GX Simulator2 和 MX Component。

显式的 GX 读取和同步检查也进入同一队列。同步检查返回已有基线与两侧程序的比较报告；读取沿用原生 CSV 解码和无损往返验证，通过原有校验后自动保存新的本地快照。Web 的只读调用关闭旧 GX 服务中默认的工程保存与基线写入，因此不会因为“检查”而保存 GX 工程或覆盖活动版本。空项目也可读取 GX 初始程序并保存。

原生程序如果含有本地语义目录尚未覆盖的 vendor 指令，Web 读取返回明确的 `unsupported`，不创建候选、不放宽 Agent/提案校验。独立的 `gxworks2.csv_importer` 原生保真解析/材料化接口保留；Qt 界面已移除，这种程序不能据此视为已完成 Web 编辑/审批迁移。

## 结构化梯形图 / FBD

新建工程选择 `FBD` 后，可在 Agent 中描述需求并生成候选，也可在 FBD 页签的对象、连接和声明表中编辑。校验通过后自动保存并显示图形，原版本保留。候选包含 `program.gxw`、`fbd.json`、`fbd.svg` 和写入报告；正式版本提供 GXW 下载。

“导入 GXW”上传本机工程（最多 30 MiB），选择其中一个 Program.pou，经校验后自动保存。原工程其他 POU、元数据及未知字段保留。CPU 参数保持原样；目前没有完整的导入 CPU 识别，工作台机型设置不能作为导入工程 CPU 已校验的依据。现有 ladder 版本可“转换为 FBD”，范围为 NO/NC/COIL 串并联；原注释保留为 source_ladder 附件。

“发送到 GX”按当前审批模式处理，使用与 CSV 相同的桌面执行队列，打开已确认 GXW 的独立副本，避免 GX 编译或保存修改正式版本。随后在 GX Works2 中执行全部编译、保存；需要回读时再次“导入 GXW”选择保存的副本。打开成功只报告 `imported`，编译状态仍为 `unverified`。遇到已有保存提示或未知对话框时停留待操作员处理，不自动选择保存或丢弃。

默认生成模板目前覆盖 FX3U，支持常开/常闭触点、线圈、输入/输出终端、MOV、TON/TON_E/CTU/CTU_E。声明编辑覆盖已知基本类型、数组、常量和 FB 实例；FB 调用会同步实例声明。未知调用保留源记录，不能任意新增未知 ABI。FBD 仿真、诊断和 CSV 同步目前未接通。实际原生编译证据、已知 C2034 警告及边界见[本轮实验记录](../research/gxw_declarations_allocation_web_fbd_20260910.md)。

## 程序定位、修改范围与验证证据

梯形图查看器支持点击网络和地址，按地址或注释搜索，跳转到读取/写入引用。图形由后端依据当前版本的 IR 重新渲染；版本指纹不符时拒绝展示，缺失的旧 SVG 不妨碍依据完整 IR 重绘。生成后可展开“本版变更”，检查保存前计算的实际网络、地址、共享注释及程序属性变化。

在 Agent 输入区展开“修改范围”，可指定已有网络 ID、地址，或同时指定两者。范围适用于生成、Agent 候选和 GX 读取；创建候选和保存版本前都会重新校验。越界不会自动重试或保存，显式的结构修复继承原范围。没有指定范围时沿用原流程；`generation_structural` 仍只检查结构完整性，不恢复隐式语义修复循环。间接地址、多字或块指令无法证明地址范围时，应使用网络范围。FBD、ST 和首次生成暂不支持该约束。

诊断及报告中的问题卡片关联实际网络、地址和已有证据，并可打开绑定的复现方案或设计新测试。旧版报告始终跳转到它绑定的版本；失效或跨版本测试链接不作为有效证据展示。

仿真页提供步骤编辑、输入、等待条件、断言和需求关联。保存产生新的不可变方案，不覆盖已经批准的方案；执行继续遵循工作区审批设置。波形仅回放保存的观测值，支持播放、采样定位和失败断言跳转；需求的设计关联与实际执行结果分别展示，内存后端不会标为真实 PLC 仿真。

FBD 草稿修改后立即隐藏旧图，经统一 GXW 写入及回读校验后显示草稿预览。保存沿用自动保存事务。原生验证页可记录操作员编译结论及保存后的 GXW 附件，绑定当前版本及文件 SHA256；操作员报告不会被提升为自动编译通过。

“工程交付摘要”汇总版本、规格、变更、产物指纹、测试及原生验证记录，可下载 Markdown。其“高级维护”区域提供默认关闭的真实 PLC 只读入口。独立读取器需按 [构建说明](../../hardware_reader/README.md)配置；一次人工授权绑定目标逻辑站、项目版本、地址白名单和期限，每次读取后关闭连接，并保留审计。不提供写入、强制、下载或运行状态切换，MCP 也不暴露该入口。

## 任务与事件接口

`POST /api/jobs` 接受 `analysis`、`generation`、`agent`、`review`、`test_plan`、`debug_plan` 以及显式的 `gx_read`、`gx_inspect`，请求必须带幂等 `request_id`，返回持久任务 ID。提交时冻结项目、基础版本、确认规格、模型配置与响应语言，Provider/密钥仅留在后端运行上下文，不能进入任务 JSON。

刷新后读取 `GET /api/jobs/{job_id}` 与 `GET /api/jobs/{job_id}/output`；不要再次提交生成。通过 `GET /api/jobs/{job_id}/events?after=<sequence>` 或 `Last-Event-ID` 恢复 SSE。事件包括 `job_id`、`project_id`、`version_id`、`sequence`、`event_type`、`payload`。客户端按序号去重，完整保留已验收内容。模型文本经过 `collect_response` 的完整响应/语言验收才发布，不能把 Provider 原始 token 直接透传给浏览器。

任务取消是协作式安全检查点取消。等待执行的 GX Future 可取消；已经开始的外部操作不会因为页面关闭、网络断开或点击取消而被假装撤销。任务结束、产物构建成功、GX 导入成功和仿真通过是不同状态。

## 本地 HTTP 访问边界

服务校验 Host 和 Origin，不启用通配 CORS。操作员写操作需要会话、同源 Origin 与 `X-CSRF-Token`。Agent 路由使用单独的 Bearer token，操作员和 Agent token 不能相同。模型设置的读取只返回配置状态，不返回密钥；模拟器网关令牌也不进入浏览器。

项目、版本和产物使用受限 ID；下载接口通过登记产物定位文件，不接受任意本机路径。完整的受保护接口定义位于 `GET /api/openapi.json`。主要资源如下：

| 资源 | 接口 |
| --- | --- |
| 工程和版本 | `/api/projects`、`/api/projects/{project_id}/versions/{version_id}` |
| 程序、诊断与下载 | 版本资源下的 `/program`、`/diagnostics`、`/artifacts/{artifact_id}` |
| 任务与重连事件 | `/api/jobs`、`/api/jobs/{job_id}/events` |
| 提案预览和决定 | `/api/proposals/{proposal_id}/preview`、`/decision` |
| 设置与附件 | `/api/settings`、`/api/settings/approval`、`/api/projects/{project_id}/attachments` |
| 需求形式的 SFC 输入 | `/api/sfc/requirement`，不代表 GX SFC 编译能力 |
| FBD 对象、文件及候选 | `/api/fbd/catalog`、`/api/fbd/inspect`、`/api/fbd/proposals`；后者支持 generate/edit/import/convert，沿用既有审批接口 |
| 环境观察 | `/api/environment`，不自动启动网关 |

## MCP 的显式服务连接模式

默认 stdio 仍是独立、无模型的只读工具适配器：创建候选和导入请求只返回 `confirmation_required`，不持久化版本或提案，不执行桌面操作。旧配置继续使用 `--workspace`，行为不变。

要把外部 Agent 的候选送到正在运行的 Web 后端，先给后端设置与操作员令牌不同的 `PLC_WEB_AGENT_TOKEN`，再显式启动桥接：

```powershell
# GXWORKS_MCP_AGENT_TOKEN 的值由本机凭据配置注入，应与后端
# PLC_WEB_AGENT_TOKEN 相同；它不是 PLC_WEB_OPERATOR_TOKEN。
$env:PYTHONPATH = Join-Path (Get-Location) "src"
python -m integrations.mcp --stdio `
  --service-url "http://127.0.0.1:8765" `
  --service-token-env GXWORKS_MCP_AGENT_TOKEN `
  --project PROJECT_ID
```

MCP 环境另需 `python -m pip install -r requirements/mcp.txt`。服务连接模式不需要本地 `--workspace`，也不推断浏览器当前选择；`--project` 必填，`--version` 可固定版本。只接受 loopback HTTP origin，拒绝重定向，令牌只从所指环境变量读取。

桥接只调用两个端点：`GET /api/agent/tools` 返回原注册表的 function schema；`POST /api/agent/tools/call` 提交 `{project_id, version_id?, name, arguments, call_id}`，返回完整公开 `data`、`content`、`is_error`，待确认时附上 `proposal_id`。后端仍经过共享 `ToolRuntime.invoke` 和安全工具白名单；`_candidate_ir`、`_confirmed_spec` 不外传。进程级 UUID 与每逻辑调用 UUID 防止 stdio 重启后的数字请求 ID 撞上历史幂等记录。Agent 本身不能调用用户设置或审批接口。默认逐项审批时提案仍待用户确认；替我审批／完全访问模式下，Web 后端按用户授权保存受支持的本地候选，完全访问还可批准 GX 导入。独立 MCP 不改变，服务桥接返回的 version_id／execution_job_id 指向实际保存结果或执行任务。

## 开源 Agent 框架的取舍

本轮保留自有的确定性工作流与官方 MCP Python SDK。SDK 本身是 [MIT 许可](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE)，已承担 MCP 协议，无需再装一个 Agent 框架完成审批桥接。后续如果需要更丰富的模型工具编排，可在现有 `ModelProvider → ToolRuntime` 边界评估 [PydanticAI 的 MCP/toolset 接入](https://pydantic.dev/docs/ai/mcp/client/)；需要图式持久状态与中断恢复时，再评估 [LangGraph checkpoint](https://docs.langchain.com/oss/python/langgraph/persistence)。两者开源核心均为 MIT（[PydanticAI 许可](https://github.com/pydantic/pydantic-ai/blob/main/LICENSE)、[LangGraph 许可](https://github.com/langchain-ai/langgraph/blob/main/LICENSE)）。这是可插拔的后续选择，不能替代 ProposalService 的工程审批、不可自动重放 GX 副作用，也不能为框架另造一套 PLC 工具。免费开源不等于模型推理免费；更换框架不会自动减少模型 token、远程模型服务或本地算力的费用。

## 发布包

`packaging/pyinstaller/web.spec` 打包独立 Web 入口，收录双击启动器、中英文 README、Web 指南、`web/dist`、默认安全配置、模型与指令资料、语言包、知识库及第三方声明，不收录用户 `config.json`、工作区、密钥或 Qt。Qt/Win7 打包入口已移除；MCP 启动器使用同目录的 `mcp.spec`。

已有网关二进制时：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_web_package.ps1 `
  -GatewayDirectory "C:\release-inputs\simulator-gateway"
```

网关目录必须包含 `PlcAi.GxSimulator2Gateway.exe`。仓库提供的是 C# 网关源码；如需构建，可使用现有 `tools/build_simulator_gateway.ps1` 在仓库外输出，再把其目录传给打包脚本。打包脚本不下载、不启动、不连接网关。没有网关时必须显式指定 `-AllowWithoutGateway`，该包不能据此声称具备已验收的真实仿真能力。

脚本会校验依赖与必须资源，运行 `npm ci`、前端构建，再调用 PyInstaller；`-SkipInstall` 使用已经安装的前端依赖，`-ValidateOnly` 只检查现有构建结果和资源。使用 `-Python` 和 `-NpmCommand` 可指定构建环境。输出目录为 `dist/GXWorks-Agent-Web`，普通用户双击其中的 `start-web.cmd`；命令行启动例如：

```powershell
.\dist\GXWorks-Agent-Web\GXWorks-Agent-Web.exe --workspace "D:\PLCWorkspaces\my-workspace" --port 8765 --open-browser
```

完整目录一起分发；模拟器网关位于可执行文件旁的 `simulator-gateway`。发布前按核对表检查实际包中的资源、登录、无 Qt 启动、旧工程只读和 Windows 实机工作流；PyInstaller 完成不等于实机验收完成。

### 正在使用发布包时准备更新

构建脚本不会停止正在运行的服务。若默认发布目录中的程序仍在使用，请加 `-StageName` 在仓库内生成独立更新包；暂存名称只能包含字母、数字、点、下划线和连字符，并以字母或数字开头。暂存模式拒绝覆盖已有同名包，也不允许输出路径经过目录联接或符号链接。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_web_package.ps1 `
  -StageName "web-fix-20260910-01" -SkipInstall `
  -GatewayDirectory "C:\release-inputs\simulator-gateway"
python scripts/web_package_smoke.py `
  --package-dir "dist\staging\web-fix-20260910-01\GXWorks-Agent-Web" `
  --archive "build\staging\web-fix-20260910-01\web\PYZ-00.pyz"
```

加 `-ValidateOnly` 可只校验依赖、资源和目标路径，不构建、不启动，也不创建暂存目录。更新包验证完成后，再等待原服务任务停止并正常关闭服务，备份原发布目录后替换程序文件。原程序目录中的 `config.json` 是用户模型设置，应单独保留；Windows 凭据存储、工作区和原私有状态目录也必须保留，重启时继续使用相同工作区和 `--state-dir`（原来没有指定时仍不指定）。不要用清空目录或镜像删除方式更新正在使用的包。

构建机可运行 `python scripts/web_package_smoke.py`：该脚本校验发布资源与 Qt 排除项，只启动 Web 可执行文件，对临时空工作区执行只读登录/静态页面烟测，再关闭自己启动的进程。它不启动网关，也不调用 GX/MX。输出中的 `native_gx_not_tested=true` 必须保留为实际验收边界。

## Qt 退役后的旧数据

已有工程直接在启动器选择原工作区目录，不需要转换或重新生成。旧默认 Windows 路径通常为 `%APPDATA%/PLC AI Studio/PLC AI Workbench/workspace`；显式工作区和 `PLC_AI_WORKSPACE_DIR` 仍优先，不自动搬迁数据。

旧 `.sfc` 图文件可用 `python -m plc.sfc example.sfc` 只读转换为需求文本后在 Web 使用；这不是原生 SFC 编译器。GXW 多 POU 检查可用 `python -m gxw.decoder example.gxw --list-programs`，再通过 `--program 1.Program.pou` 选择程序。源码命令需要 `PYTHONPATH=src`。旧图形画布没有回填到 Web；其他保留与退役范围见[审查记录](../architecture/qt-retirement-audit.md)。
