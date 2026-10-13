# GXWorks Agent · 公开工作台

[English](README.md)

GXWorks Agent 是本地 PLC 工程工作台。本仓库公开 React 界面、HTTP 接口契约和前端测试；工程后端作为独立私有组件保存，不随本仓库提供。

连接兼容后端后，界面可用于工程管理、需求整理、程序查看、编辑提案、诊断和模型设置。实际操作能力及 CPU 支持范围由后端决定。仅克隆本仓库不能获得程序生成、GXW 处理、GX Works2 原生集成、仿真或 MCP 服务。

## 连接私有组件运行

准备 Node.js 22.14 或更高版本及 npm。先启动另行取得的私有后端，使其监听 `http://127.0.0.1:8765`，再运行：

```powershell
git clone https://github.com/Arienax/gxworks-agent-public.git
cd gxworks-agent-public/web
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173`，输入本机后端提供的操作员令牌。开发服务器将 `/api` 请求转发到该后端。没有后端时，界面无法加载工程或执行工程操作；本仓库不提供托管后端或公开的核心下载。

```powershell
npm run build
node --experimental-strip-types --test tests/*.test.mjs
```

构建输出为 `web/dist` 中的前端资源，可由私有后端维护者配合匹配的 API 提供。`npm run preview` 只预览静态资源，不连接 API。

参见[私有组件接入](docs/private-component.md)、[发布规则](docs/publishing.md)和[贡献指南](CONTRIBUTING.md)。

## 许可

项目自有文件沿用[源码可见的专有许可证](LICENSE)，不是开源许可证。第三方依赖保留各自的[许可证和归属](THIRD_PARTY_NOTICES.md)。
