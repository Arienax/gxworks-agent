# 本地 HTTP 接口

## 定义来源

运行服务的 `/api/openapi.json` 是该实例的接口描述。路由在 [integrations.web.app.create_app](../../src/integrations/web/app.py)及相邻 `*_routes.py` 模块中注册；字段、默认值和限制由 [schemas.py](../../src/integrations/web/schemas.py)与 [responses.py](../../src/integrations/web/responses.py)维护。

仓库保存 [web/openapi.json](../../web/openapi.json)，[generated.ts](../../web/src/api/generated.ts)从它生成。修改接口后的更新顺序为：

```powershell
python scripts/export_web_schema.py
npm run types --prefix web
npm run build --prefix web
```

生成器是 [export_web_schema.py](../../scripts/export_web_schema.py)，前端命令由 [package.json](../../web/package.json)定义。文档不复制完整 JSON schema。

## 资源入口

| 资源 | 路径入口 | 实现 |
| --- | --- | --- |
| 工程、版本、登记产物 | `/api/projects` | [app.py](../../src/integrations/web/app.py) |
| 持久任务、输出和 SSE | `/api/jobs` | [app.py](../../src/integrations/web/app.py) |
| 提案及决定 | `/api/proposals` | [app.py](../../src/integrations/web/app.py) |
| 设置与模型配置 | `/api/settings` | [app.py](../../src/integrations/web/app.py) |
| FBD 目录及编辑 | `/api/fbd` | [app.py](../../src/integrations/web/app.py) |
| 交付摘要 | 版本路径下的 `/delivery` | [delivery_routes.py](../../src/integrations/web/delivery_routes.py) |
| MCP 接入 | `/api/integrations/mcp` | [mcp_routes.py](../../src/integrations/web/mcp_routes.py) |

任务交互导出使用 `GET /api/jobs/{job_id}/diagnostics`，成功和失败任务均可导出已有记录。版本交付摘要使用 `GET /api/projects/{project_id}/versions/{version_id}/delivery`。前者返回 ZIP，后者返回结构化摘要及其 Markdown。具体可用路径以实例 OpenAPI 为准。

## 会话与请求

服务监听 loopback。操作员写请求需要会话、同源 Origin 和 `X-CSRF-Token`；Agent 请求使用独立 Bearer token。鉴权及 Host/Origin 规则在 [security.py](../../src/integrations/web/security.py)。不要把操作员 token 当作 MCP Agent token，也不要把登录链接写入工程。

登记产物按受限 ID 下载，不接受任意本机路径。模型配置读取只返回公开状态；凭据留在后端。交互 ZIP 为操作员导出，不作为 Agent 的数据读取接口。

## 任务身份与事件

创建任务时提交幂等 `request_id`；应用冻结工程、版本、规格、模型和语言快照。刷新后读取任务及输出，使用事件序号恢复 SSE。客户端按序号去重，不以重发生成恢复页面。

任务结束、产物构建、GX 导入、原生编译和仿真结果分别处理。取消与中断语义见 [Web 工作台](web.md#任务恢复)。
