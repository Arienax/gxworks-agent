# GXWorks Agent

[English](README.md) · [日本語](README.ja.md)

GXWorks Agent 是面向 GX Works2 的本地工程工作台。Web 与 MCP 入口共用 Python
应用服务，可创建和检查 PLC 工程、生成与审阅梯形图/ST/FBD 候选，并在确认后发送到
选定的 GX Works2 实例。本次公开源码包含 GXW 原生工程编辑代码和运行必需的空白
工程模板。

## Windows 源码启动

安装 Python 3.10+、Node.js 22.14+；原生操作还需要自行取得并安装 GX Works2。
然后运行：

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent
.\build-web.bat --no-pause
.\start-web.cmd
```

独立 MCP 入口为 `gxworks-agent-mcp.cmd`，可使用 Web 安装的 Python 环境。
只用 MCP 时，可在 `.venv-mcp` 安装 `requirements/mcp.txt` 后运行同一入口。
只构建前端可运行 `npm --prefix web ci` 和 `npm --prefix web run build`。

`src/gxw/templates/` 包含 FX3U 基线、节点与语法模板，以及 57 个按 CPU 区分的
空白 FBD 工程。复制或打包程序时须保留这些文件。存在空白模板不代表该型号的
PLC 运行行为已经验证。

本仓库不分发三菱手册、由手册生成的知识库和完整指令定义，因为尚未确定它们的再分发
权限。应用可以在缺少这些资料时启动，但基于手册的 RAG 和部分详细指令事实需要
用户用自己有权使用的资料在本地建立索引。源码构建会提示这一限制。模型返回某个
指令或地址，也不等于目标 CPU 一定支持。

设备投运前，应结合选定 CPU、接线和安全设计审查程序，在目标 GX Works2 中编译、
检查，并在目标设备上验证行为。应用检查和空白工程模板不能代替这些步骤。

参见[本次改动](CHANGELOG.md)、[贡献指南](CONTRIBUTING.md)、[许可范围](LICENSE-SCOPE.md)
和[第三方声明](THIRD_PARTY_NOTICES.md)。项目贡献者拥有权利的源码按
[Apache License 2.0](LICENSE)提供；旧版本仍保留其发布时的许可。
