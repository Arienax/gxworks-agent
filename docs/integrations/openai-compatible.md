# 通用 OpenAI-compatible 接入与参数检测

## 实现范围

所有兼容服务使用同一个 `OpenAICompatibleProvider`，不根据模型名称编写参数分支。Web 设置页提供 OpenAI、千问、Kimi、Claude 和 Gemini 的地址预设；预设不携带模型 ID、推理强度、温度或供应商专用参数。自定义网关、区域和工作区地址可直接编辑；模型可手动填写，也可从 `/models` 获取。已有 DeepSeek/GLM 配置保留，不自动改写用户配置。

本功能适配 **Chat Completions 兼容协议**，不是所有供应商原生 API 的完整实现。Claude 官方兼容层不等于原生 Messages API；不能把兼容层静默忽略的参数标成已支持。需要原生扩展时，必须由对应兼容网关提供映射，或者单独实现原生协议适配器。Responses-only 模型的工具能力也不能从 Chat Completions 推断。

## 参数合同与滑条

`model_capabilities.py` 定义 `parameterSupport`；HTTP 使用 `parameter_support`。合同包含端点、模型 ID、非采样请求扩展指纹，以及 `reasoning_effort` / `temperature` 的状态、来源和合法值。模型名称不参与判断逻辑。

优先使用当前端点 `/models` 返回的明确 `parameters` / `parameter_schema`（JSON Schema 的 `enum`、`const`、`minimum`、`maximum`、`multipleOf`）。没有参数模式时，以固定短提示进行探测：先发送无效对照值，只有服务明确拒绝该参数的无效值后，才枚举合法候选值。推理候选为 none、minimal、low、medium、high、xhigh、max；元数据可声明其他档位。温度探测 0、0.5、1、1.5、2；只验证了离散值时仅提供这些刻度，**不虚构连续取值范围**。有明确范围元数据时按其步长提供细分滑条。

状态的含义：

- `supported`：元数据明确声明，或合法值与无效值对照通过；并不证明模型的实际推理质量会按比例变化。
- `accepted`：连无效对照值也被接受，可能被忽略；不提供已验证滑条。
- `unknown`：证据不足、超时或其他无关错误；不当作“不支持”。
- `unsupported`：服务明确拒绝该参数。
- `fixed`：元数据给出固定值，或温度的全部候选中只有一个被接受；不给出可调范围。

已检测参数的滑条优先于分析阶段、修复阶段和 Agent 内部的推理强度建议；未采用检测合同的旧配置仍保留原有参数合并行为。滑条包含“服务默认值”：选择它会真正省略参数，即使工作流提供了 low/high 等默认建议也不会重新发送。调整滑条会清理 `requestOverrides` 和 `extra_body` 内的同名覆盖，保留无关扩展。固定或明确不支持的参数在采用检测结果后不再发送。温度检测结果绑定当时的推理强度；调整推理强度会清除温度值，并要求重新检测其适用性。

切换模型、端点、API Key 或高级参数会使前端检测结果失效。合同不会跨端点、模型和思考扩展自动复用。可在高级 JSON 中显式配置尚未确认的参数；后端没有根据模型名字猜测出来的强制名单。手动参数合同可通过设置 API 声明 `source: manual`，仍受数据校验与作用域约束。

## 检测、成本和凭据

`POST /api/settings/detect` 支持第一个尚未保存的 Profile，使用与原设置接口相同的操作者身份、CSRF 和只读保护。检测本身不保存配置、不修改凭据、不发送工程或历史消息。

检测采用 90 秒调度预算，每个请求最多设置 15 秒超时、禁用 SDK 重试，并为生成设置较小输出上限。完整检测通常最多 18 个 HTTP 请求；仅在服务明确拒绝 `max_completion_tokens` 时，多一次限长字段协商，改为 `max_tokens`。耗尽预算时保留已经验证的结果，不把未检测项判为不支持。实际网络超时行为仍受 HTTP 客户端实现约束。

认证错误和限流不是能力缺失，不继续穷举重试。服务不提供 `/models` 时仍可验证手填模型；显式模型别名不在列表中时也不会擅自换成列表第一项。不要将 `/models` 的认证成功视为所有模型都可用。检测会产生少量 API 费用，UI 在操作附近说明。

更换检测地址时不会自动把原地址保存的密钥发送给新地址，需明确输入密钥。错误正文可能回显密钥，因此接口仅返回固定消息、受限错误码与结构化检测结果。

## 请求与多轮工具调用

未知 SDK 顶层扩展根据当前 SDK 方法签名统一放入 `extra_body`，不按供应商分类。`extra_body` 禁止覆盖规范的 model、messages、tools、tool_choice、stream、response_format，避免绕过工程工具和输出合同。

保留响应携带的 reasoning_content、reasoning、reasoning_details、extra_content，以及工具调用上的 extra_content（例如 Gemini thought_signature）。这些是后端私有回传状态，不进入显示回调或持久化历史。回传限定同端点、同模型和同凭据。流式和非流式均有覆盖，取消或关闭流时释放响应。

本次不修改 PLC 工具注册表、执行权限、校验器或 GX/仿真操作。

## 验证与验收

离线单测使用模拟 SDK / HTTP 服务，不需要真实 API Key：

```bash
python -m pytest -q tests/test_model_capabilities.py tests/test_model_detection.py tests/test_model_provider.py tests/test_application_settings.py tests/test_model_profile_config.py tests/test_model_profile_deletion.py tests/test_web_contract.py
cd web
node --experimental-strip-types --test tests/model-parameters.test.mjs
npm ci
npm run build
```

浏览器验收只替换模型传输，使用真实 Web HTTP、鉴权、设置服务、保存和重载：

```bash
python scripts/web_model_settings_e2e.py --web-dist web/dist --evidence model-api-evidence
```

人工验收：新建配置，选择/编辑兼容地址并输入 Key；获取模型或手填模型后检测；检查推理滑条只包含合法档位、固定温度不出现可调范围；调整推理后重新检测温度；保存并重开设置页，确认值保留。切换模型后旧检测和旧采样值应清除。真实各家端点和账户权限需用自己的密钥验收，离线通过不等于真实服务逐家通过。

## 协议参考（核对日期：2026-09-17）

- OpenAI Chat Completions 参数定义：<https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create/>
- 千问 OpenAI 兼容说明：<https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope>
- Kimi 参数与多轮回传：<https://platform.kimi.ai/docs/guide/kimi-k3-quickstart>
- Claude OpenAI SDK 兼容限制：<https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk>
- Gemini OpenAI 兼容及思考签名：<https://ai.google.dev/gemini-api/docs/openai>
