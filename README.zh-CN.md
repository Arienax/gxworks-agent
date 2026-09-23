# GXWorks Agent

简体中文 | [English](README.md)

GXWorks Agent 是面向三菱 PLC 工程的本地工作台，重点支持 FX3U 与 GX Works2。它将需求整理为可审查的规格，生成梯形图候选，保存带版本的 PLC IR，并导出工程产物。Web 工作台和外部 MCP 客户端共用 Python 工程服务。

在工作台中可以分析需求、确认 I/O、生成或修改程序、查看版本变更，以及导出该版本实际保存的文件。GX Works2 导入、仿真和硬件观察各有操作条件，见 [Web 使用指南](docs/integrations/web.md)。

## 快速上手

### Windows 源码运行

安装 Git 及 Git LFS、Python、Node.js/npm。依赖清单与常见安装问题见[环境准备](docs/guides/getting-started.md)。

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent
git lfs install --local
git lfs pull
.\build-web.bat --no-pause
.\start-web.cmd
```

构建入口准备后端环境和前端，启动器选择工作区后打开本地工作台。使用期间保持启动窗口打开，结束时用 `Ctrl+C` 停止服务。

在“设置 → 模型 → 模型 API”中配置模型，然后创建工程。先用 Direct 分析需求，检查规格和 I/O，再生成程序。完整操作见[第一个工程](docs/guides/getting-started.md#first-project)。

### Web 发布包运行

对于包含 `start-web.cmd` 和 `GXWorks-Agent-Web.exe` 的 Web 包，解压完整目录后运行 `start-web.cmd`，保留可执行文件旁的资源。[安装与更新](docs/guides/packaging.md)说明目录布局及替换程序的步骤。

### 连接外部 Agent

打开工程后进入“设置 → 模型 → Integrations / MCP”。产品启动器见 [MCP 接入](docs/integrations/onboarding.md)，Codex 配置见[客户端指南](docs/integrations/codex.md)，独立模式和显式服务连接见 [MCP 参考](docs/integrations/mcp.md)。

## 文档

[文档索引](docs/README.md)按使用指南、实现参考、版本实测报告和历史过程分类。

- [模型配置与持久化数据](docs/guides/model-settings.md)
- [GX Works2 传送](docs/guides/gxworks2.md)与[结构化梯形图 / FBD](docs/guides/fbd.md)
- [诊断与交互记录导出](docs/guides/diagnostics.md)
- [架构与代码导航](docs/architecture/source-layout.md)
- [知识库](resources/knowledge/README.md)与 [GXW 研究证据](research/README.md)
- [验证报告](docs/reports/README.md)

## 设备投运前

按所选 CPU、实际接线、运动边界和运行模式审查生成逻辑，在目标 GX Works2 环境编译、检查后再调试设备。急停、防护等安全功能应由设备的独立安全系统承担，生成的应用逻辑和软件测试不能替代这些功能。

## 许可

本项目采用源码可见的专有[许可证](LICENSE)。许可范围内的副本须保留许可证原文及[第三方归属声明](resources/knowledge/THIRD_PARTY_NOTICES.md)。
