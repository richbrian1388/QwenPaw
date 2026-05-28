# PA-AI 模型供应商设计文档

## 背景

新增内置模型供应商 "PA-AI"，接入公司内部部署的大模型。API 端点 (`wizard-ai.paic.com.cn`) 兼容 OpenAI 格式，但需要通过 CAS 认证获取 ticket，再用 SM4 加密生成 API key，key 有效期 24 小时。

## API 兼容性分析

**请求格式**: 完全兼容 OpenAI 格式。标准的 `POST .../v1/chat/completions`，参数包含 `model`、`messages`、`temperature`、`max_tokens`。

**响应格式**: 兼容 OpenAI 格式。核心结构 (`choices`、`message`、`usage`、`finish_reason`、`object: "chat.completion"`) 与 OpenAI 一致。额外字段 (`metadata`、`matched_stop`、`reasoning_tokens`) 会被 OpenAI SDK 忽略。

**认证方式**: 主要差异。需要 CAS 登录 → SM4 加密 ticket → Bearer token。Key 每 24 小时过期。

## 设计决策

- **复用 `OpenAIProvider`** — API 兼容 OpenAI，无需新建 Provider 类
- **手动刷新 Key** — 用户在 Console 中手动触发重新认证
- **Console 集成 Key 生成** — 在供应商配置弹窗中增加"生成 API Key"按钮
- **初始模型**: PUB-GLM-4.7（硬编码，后续可扩展）

## 架构设计

### 1. 供应商注册 (`provider_manager.py`)

新增 `PROVIDER_PA_AI` 常量，使用 `OpenAIProvider` 实例：

```python
PROVIDER_PA_AI = OpenAIProvider(
    id="pa-ai",
    name="PA-AI",
    base_url="https://wizard-ai.paic.com.cn/code_pilot/api/v1",
    api_key="",
    chat_model="OpenAIChatModel",
    require_api_key=True,
    freeze_url=True,
    support_model_discovery=True,
    support_connection_check=True,
    models=[
        ModelInfo(id="PUB-GLM-4.7", name="PUB-GLM-4.7"),
    ],
)
```

在 `_init_builtins()` 中通过 `self._add_builtin(PROVIDER_PA_AI)` 注册。

无需修改 `_provider_from_data()` — `OpenAIProvider` 是默认的反序列化 fallback。

### 2. 后端认证模块 (`src/qwenpaw/providers/pa_ai_auth.py`)

新建文件，从 `llm/apikey-generator.py` 提取 CAS/SM4 核心逻辑：

- `sm4_ecb_pkcs7_hex(plain_text: str, key_text: str) -> str` — SM4 ECB 加密 + PKCS7 填充
- `start_cas_auth() -> tuple[str, threading.Event, dict]` — 启动本地 HTTP 回调服务器（随机端口），返回 (cas_url, done_event, result_dict)
- `generate_api_key_via_cas() -> str` — 完整流程：启动回调服务器 → 等待 ticket → SM4 加密 → 返回 key

常量：
- `DEFAULT_CAS_LOGIN_URL = "https://vt.paic.com.cn/user-center-web/cas/login"`
- `DEFAULT_SM4_KEY_HEX = "52aad0c09e6b46818a11ee702d6fe0ba"`
- `SERVICE_NAME_PARAM = "c3FtcS1jb2RlLXBpbG90"`
- `LOGIN_TIMEOUT_SECONDS = 300`

本地回调服务器在后台线程中运行（与 `apikey-generator.py` 相同模式）。CAS 回调处理：
1. 从 query 参数获取 ticket
2. SM4 加密 ticket 生成 API key
3. 保存 key 到 `ProviderManager`（线程安全的单例）
4. 关闭回调服务器

### 3. 后端 API 端点 (`providers.py` 路由)

`POST /api/models/pa-ai/generate-key`

请求：空 body

响应：
```json
{
  "cas_url": "https://vt.paic.com.cn/user-center-web/cas/login?service=...",
  "status": "waiting"
}
```

错误场景：
- 已有 CAS 认证会话进行中 → 409 Conflict
- 认证流程失败 → 500 + 错误信息

实现逻辑：
1. 检查是否已有 CAS 认证会话在运行（模块级标志）
2. 在后台线程中调用 `generate_api_key_via_cas()`
3. 立即返回 CAS URL
4. 后台线程在回调到达后将 key 保存到 `ProviderManager`
5. 前端通过轮询 provider config 检测完成

### 4. 前端 API 客户端 (`provider.ts`)

新增函数：
```typescript
export const generatePAIApiKey = () =>
  request.post('/models/pa-ai/generate-key')
```

### 5. 前端 UI (`ProviderConfigModal.tsx`)

条件渲染（仅当 `provider.id === 'pa-ai'`）：

**"生成 API Key" 按钮** 位于 API Key 输入框旁：
1. 调用 `generatePAIApiKey()`
2. 在新标签页打开返回的 `cas_url`（`window.open`）
3. 在 API Key 输入框下方显示 "等待 CAS 认证..." 加载状态
4. 每 3 秒轮询 `GET /api/models` 检查 `pa-ai` 是否已设置 `api_key`
5. 检测到 key 后：停止轮询，自动填入显示，隐藏加载状态
6. 超时（5 分钟）未完成：停止轮询，显示 "认证超时，请重试" 错误提示

按钮在生成会话进行中时禁用，防止重复点击。

### 6. 国际化 (`locales/`)

新增 i18n key：
- 按钮文本: "生成 API Key" / "Generate API Key"
- 加载提示: "等待 CAS 认证..." / "Waiting for CAS authentication..."
- 超时提示: "认证超时，请重试" / "Authentication timeout, please try again"

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| CAS 登录超时（5 分钟） | 后端回调服务器自动关闭，前端轮询超时后提示错误 |
| 用户关闭浏览器标签页 | 后端回调服务器 5 分钟后超时关闭，无资源泄漏 |
| API key 已过期 | 用户再次点击"生成 API Key"覆盖旧 key |
| 网络不通（公司内网限制） | "测试连接"失败，提示用户检查网络 |
| 并发多次点击"生成 API Key" | 后端检测到已有会话，返回 409 Conflict |

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/qwenpaw/providers/pa_ai_auth.py` | 新增 | SM4 加密 + CAS 认证逻辑 |
| `src/qwenpaw/providers/provider_manager.py` | 修改 | 添加 `PROVIDER_PA_AI`，注册到 `_init_builtins()` |
| `src/qwenpaw/app/routers/providers.py` | 修改 | 添加 `POST /models/pa-ai/generate-key` 端点 |
| `console/src/api/modules/provider.ts` | 修改 | 添加 `generatePAIApiKey()` 函数 |
| `console/src/components/modals/ProviderConfigModal.tsx` | 修改 | 条件渲染"生成 API Key"按钮 + 轮询逻辑 |
| `console/src/locales/` | 修改 | i18n 文案 |

总计：1 个新文件 + 5 个修改文件，核心逻辑约 200-300 行。
