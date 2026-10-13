# GXWorks Agent

[English](README.md) · [下载发布包](https://github.com/Arienax/gxworks-agent/releases/latest)

GXWorks Agent 提供本地 PLC 工程 Web 工作台和独立 MCP 服务。两个 Windows 包分别安装，各自包含编译后的工程核心、原生适配器和离线知识库，无需安装 Python。Web 使用 Node.js 22.14 或更新版本，通过 npm 构建和启动；MCP 不需要 Node.js。

本仓库公开界面、HTTP 契约和前端测试。核心源码、逆向研究记录和报告保留在私有开发仓库；可运行的核心随发布包分发。

## 选择安装

| 下载包 | 使用方式 | 依赖另一个包 |
| --- | --- | --- |
| GXWorks-Agent-MCP-windows-x64.zip | 接入本地 stdio MCP 客户端，创建和选择工程、读取上下文、提交及保存程序、获取产物 | 不需要 Web |
| GXWorks-Agent-Web-windows-x64.zip | 浏览器工作台，管理工程、配置模型、生成和编辑程序、查看图形、诊断及审批 | 不需要 MCP |

将所选 ZIP 完整解压到 Windows 10/11 x64 的独立文件夹，保留随附文件。更新时解压到新目录，保留原有工程和用户设置。

**Web：** 解压 Web 包，或克隆本仓库，在目录内依次运行：

```sh
npm install
npm run build
npm start
```

首次从源码启动时自动下载匹配版本的编译核心；Web 下载包已包含核心。启动后自动打开浏览器，工程直接保存到 `%LOCALAPPDATA%\GXWorks Agent\web-workspace`，不弹出工作区选择框。运行状态与个人设置另行保存。高级使用可运行 `npm start -- --workspace <目录>`。模型生成需要配置自己的模型服务与密钥；查看和管理工程不要求调用模型。

**MCP：** 在客户端添加 stdio 服务，命令设为解压目录的 `gxworks-agent-mcp.exe`，参数为 `--local`。客户端使用自己的模型，无需启动 Web。先调用 `list_projects`，再选择工程或明确指定 PLC 型号和目标语言创建工程。启动选项见 `gxworks-agent-mcp.exe --help`。

## 能力与边界

两个入口共用工程核心，提供工程管理、候选校验、版本保存、梯形图产物、GXW 读取及已实现的 FBD/SFC 操作。原生保存、代码检查、导入和仿真需要本机对应的三菱软件；包内不含 GX Works2 或 MX Component。具体 CPU、工程类型和版本的支持边界以实际结果为准，不把单个型号的验证推广到整个系列。

结构校验、原生编译、仿真和真实 PLC 执行分别报告。外部操作保留现有审批规则，不会因安装或打开界面而自动操作设备。

MCP 可选用 `--service` 接入 Web 后端，共用工程状态和审批提案。这是工具桥接，不同步聊天上下文，也不提供多 Agent 编排。默认安装保持独立，详见[组件与桥接](docs/private-component.md)。

## 界面开发

仓库根目录的 `npm run build` 构建界面，`npm test` 运行公开测试。`npm start` 加载当前的 `web/dist`，修改后重新构建即可生效。需要前端热更新时，在已启动 Web 后端的基础上进入 `web` 运行 `npm run dev`，代理连接 `127.0.0.1:8765`，使用后端操作员令牌登录。仅克隆源码不含工程核心；可预先用 `npm run setup` 下载，离线安装支持 `npm run setup -- --archive <Web发布包.zip>`。

参见[发布规则](docs/publishing.md)和[贡献指南](CONTRIBUTING.md)。许可沿用 [LICENSE](LICENSE)，第三方组件保留各自[归属和许可](THIRD_PARTY_NOTICES.md)。
