# 模型设置界面交互

记录日期：2026-09-24。本文说明"设置 → 模型 → 模型 API"的交互形态及其依据，属于已实施的设计记录。

## 问题

原先的模型设置是一张常驻长表单：配置用下拉框选择，新建与编辑共用同一批控件，保存、测试连接、清除密钥、删除配置与其他参数验证按钮全部铺在同一层。参数区每个可调项各带"验证当前值"与"恢复服务默认值"，能力区每项各带"单项验证"。一份包含 4 个参数和 6 项能力的配置会产生二十余个按钮，用户难以判断下一步该点哪个。

## 对齐依据

参照 DeepSeek Harness 的模型设置页（`packages/client/ui-settings-models`）的交互模型。它把复杂度收在三个决定里：

1. **列表 + 行**：每个已保存的 provider 是一行，只显示身份、手写标记与凭据状态点；行内动作只有"编辑"和"删除"。
2. **同一时间只有一张卡**：编辑某一行会关闭新增卡，新增卡打开时行编辑器关闭。每种卡各自持有开关状态，关闭一张不会丢弃另一张的草稿。
3. **新增是一条路径、两个模式**：一个"新增"按钮背后用分段控件区分"采用目录中已知的服务"与"声明自定义服务"，两个面板各自保持挂载，切换不丢草稿。

删除使用模态确认而不是行内展开。保存成功后由 `role="status"` announce 保存的 provider 名称。

## 实施内容

见 [Settings.tsx](../../../web/src/features/Settings.tsx) 与 [ModelParameterControls.tsx](../../../web/src/features/ModelParameterControls.tsx)。

| 项目 | 变更 |
|---|---|
| 配置选择 | 下拉框 + "新建配置" → 配置行列表 + 单个"新增配置"按钮 |
| 编辑形态 | 常驻长表单 → 同一时间一张编辑卡，卡内提供"取消" |
| 删除 | 行内展开的二次确认 → 模态确认（复用现有 `Modal`） |
| 清除已存密钥 | 与删除同区的危险操作 → 移入编辑卡，仅在该配置已存密钥时出现 |
| 每参数"恢复服务默认值" | 删除。模式下拉的"服务默认值（不发送）"已提供同一语义，按钮是重复入口 |
| 空配置首屏 | 保持直接进入新建卡，首次使用不需要额外点击 |
| 地址来源 | 单一预设下拉 → 卡内两模式开关：「选择已知服务」与「自定义模型 API」 |

## 服务选择与地址

### 两个模式，一条服务列表

卡片顶部是 DSH 式的模式开关：**「选择已知服务」**从列表里选一个端点，**「自定义模型 API」**由用户手填地址。这正是 DSH 新增卡 `catalog` / `custom` 两模式的对应物。服务列表只有一条（31 条），不再按来源分组；打开一份已保存的配置时，地址能匹配到列表项就进入「选择已知服务」，匹配不到就是一份自定义声明，自动进入「自定义模型 API」。

列表项只在选中后通过一行说明区分：命中本地能力合同的端点提示可以直接使用已声明的参数，其余提示使用通用模板、参数支持未经确认。信息保留，结构上的分组去掉。

### 修复：切换不生效

原实现有两个独立缺陷，用户看到的表现是"只能定死在自定义 OpenAI-compatible 服务"：

1. `<select value="">` 把值写死为空串，选中的项立即被 React 拉回占位项，界面因此不反映选择。
2. `onChange` 先调用 `beginCreate()`（该函数把 `name`/`model`/`baseUrl` 全部重置为空），再赋值预设字段；即使 (1) 修好，写入的地址也会被这次重置与随后的字段同步覆盖。

修法是让选择成为**受控状态**：`preset` 状态、`value={preset}`、`applyPreset` 只写地址与起始名称，不再调用 `beginCreate`。打开已有配置、手动改地址时都会重新同步 `preset`，手填地址不再匹配任何列表项时回到占位项。选择服务不会重命名用户已经命名的配置。

### 列表来源与收录口径

原列表是 7 条手写端点，条目偏少且缺少一批第三方提供商。新列表 31 条，来源分两类（`source` 字段，仅用于那行说明，不再分组）：

- **本项目已收录端点**（`source: "local"`，9 条）：与 [resources/model_catalog](../../../resources/model_catalog/) 中带精确 `match.endpoints` 的能力合同一一对应——OpenAI、DeepSeek、智谱 GLM、通义千问（北京/新加坡）、Kimi（中国/国际）、Claude、Gemini。
- **来自 DSH provider 目录**（`source: "dsh"`，22 条）：厂商同时出现在 DSH 的 pi-ai 内建 provider 目录中，且其端点提供 Chat Completions 兼容接口——OpenRouter、Groq、Together、Fireworks、Cerebras、Baseten、NVIDIA NIM、Hugging Face Router、Ant Ling、Mistral、MiniMax（国际/中国）、Vercel AI Gateway、OpenCode Zen、小米 MiMo、Z.AI Coding（国际/中国）、通义千问 Token Plan（新加坡/北京）、小米 Token Plan（AMS/中国/新加坡）。

#### 收录口径（含一次被推翻的判断）

**唯一口径是：端点必须提供 Chat Completions 兼容接口，并能用已保存的 API Key 访问。** 供应商的原生协议是什么不影响收录——Claude 与 Gemini 正是通过各自的 OpenAI 兼容层进入列表，这与本项目自带的 [`anthropic-compat`](../../../resources/model_catalog/anthropic-compat.json) 合同完全一致：该合同的 `id` 就是"Anthropic 的兼容层"，`references` 直接指向 OpenAI SDK 兼容说明，并声明 `temperature` 上限为 1、`reasoning_effort` 与 `structured_output` 不支持——这些都是兼容层的真实约束，而不是原生 Messages API 的能力。

本记录的第一版曾按 pi-ai 的 `api` 字段筛选，把 Claude、Gemini、Mistral、MiniMax、Vercel、OpenCode 等一并排除，并删除了原有的 `Claude / Anthropic` 预设。**那个判断是错的**：pi-ai 的 `api` 是它为该厂商**首选**的协议，不是端点**唯一**支持的协议，两者不能互换。[openai-compatible 设计记录](openai-compatible.md#L9)本来就写明"Web 设置页提供 OpenAI、千问、Kimi、Claude 和 Gemini 的地址预设"（#L11 进一步说明适配的是 Chat Completions 兼容协议，Claude 走官方兼容层），因此删除 Claude 预设属于背离既有设计。现已恢复，并把 Gemini 条目改名为"Gemini（OpenAI 兼容端点）"以消除歧义。

仍未收录的厂商及原因：

| 厂商 | 未收录原因 |
|---|---|
| Amazon Bedrock | 用 AWS SigV4 请求签名，OpenAI SDK 的 `api_key` Bearer 头无法通过认证 |
| Google Vertex AI | 用 Google OAuth 服务账号令牌，且路径含 project/region |
| GitHub Copilot | OAuth 设备流，令牌短时效，不是静态 API Key |
| OpenAI Codex | ChatGPT 会话后端，不是 API Key 端点 |
| Azure OpenAI | 接口兼容，但 base URL 含调用者自己的资源名（`https://<resource>.openai.azure.com/openai/v1/`），无法做成固定预设，需用户手填 |
| Cloudflare Workers AI / AI Gateway | 同上，URL 含 account id |
| Kimi For Coding | `https://api.kimi.com/coding` 为 Anthropic 风格；Kimi 的兼容接口已由"Kimi / Moonshot（中国/国际）"覆盖，不重复提供 |

列表项仍然只写地址（[model-parameters.test.mjs](../../../web/tests/model-parameters.test.mjs#L77) 断言条目只有 `name`/`source`/`url`，且地址唯一、必须为 https）。不在本地目录中的端点解析为 `unknown` 能力并默认省略参数，连不通会在"测试连接"处显式失败，不会静默继承其他端点的结论。


未改动：能力参数控件本身（模式选择、滑条、布尔开关、值校验）、参数与能力的单项验证、手动能力覆盖 JSON、查看能力合同、"获取模型列表"与"加载能力配置（零生成请求）"的分工。这些是能力合同 v3 的功能，与 DSH 的交互简化无关，不在本次范围内。

## 验证

- `npx tsc -b` 通过；`npm run build` 产出新的 `web/dist`。
- `node --test web/tests/model-parameters.test.mjs`：17 项全部通过（含新增的预设唯一性与 https 断言）。
- `scripts/web_model_settings_e2e.py` 已按新交互更新：打开设置后若编辑器未打开则点击"新增配置"；页面重载后点击对应配置行的"编辑"再断言参数值。
- Python 侧 [test_mcp_local_onboarding.test_integrations_ui_does_not_require_token_copy](../../../tests/test_mcp_local_onboarding.py#L215) 仍通过（MCP 区块未改动）。

未执行：浏览器 E2E 未在本机运行（当前 Python 环境未安装 `playwright`，未安装以保持环境不变），因此"行列表 → 编辑 → 重新加载 → 参数值保留"这条链路由脚本更新而非实测覆盖。服务选择与两模式开关同样只有类型检查与构建覆盖，未在真实浏览器中点击验证。真实设备与真实端点未参与。

31 条服务地址中，只有 9 条有本地能力合同背书（`anthropic-compat` 与 `google-gemini25` 即为其中的 Claude 与 Gemini，合同自带 `references` 与 `reviewed_at`）。其余 22 条来自 DSH 目录或厂商通用兼容地址，**本机未做连通性验证**：本环境的 `web_fetch` 被网络策略拒绝（域名解析到非公网地址），无法查阅厂商文档核对。按[openai-compatible 设计记录](openai-compatible.md)的既有约定，真实端点与账户权限需用自己的密钥验收，离线通过不等于真实服务逐家通过。

Python 测试套件在本机以系统 Python 运行时存在 33 项既有失败（缺少虚拟环境内的依赖，如 `ModuleNotFoundError`），改动前后失败项与数量一致，未引入新失败。
