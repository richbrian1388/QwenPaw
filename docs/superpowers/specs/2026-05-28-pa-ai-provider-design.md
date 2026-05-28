# PA-AI Provider Design

## Background

Add a new built-in model provider "PA-AI" for the company's internally deployed LLM. The API endpoint (`wizard-ai.paic.com.cn`) is OpenAI-compatible but requires a CAS-authenticated, SM4-encrypted API key with 24-hour expiry.

## API Compatibility Analysis

**Request format**: Fully OpenAI-compatible. Standard `POST .../v1/chat/completions` with `model`, `messages`, `temperature`, `max_tokens`.

**Response format**: OpenAI-compatible. Core structure (`choices`, `message`, `usage`, `finish_reason`, `object: "chat.completion"`) matches OpenAI. Extra fields (`metadata`, `matched_stop`, `reasoning_tokens`) are ignored by the OpenAI SDK.

**Authentication**: The main difference. Requires CAS login → SM4 encryption of ticket → Bearer token. Key expires every 24 hours.

## Design Decisions

- **Reuse `OpenAIProvider`** — no new provider class needed since the API is OpenAI-compatible
- **Manual key refresh** — user triggers re-auth via Console when key expires
- **Console-integrated key generation** — "Generate API Key" button in provider config modal
- **Initial model**: PUB-GLM-4.7 (hardcoded, more can be added later)

## Architecture

### 1. Provider Registration (`provider_manager.py`)

Add `PROVIDER_PA_AI` as an `OpenAIProvider` instance:

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

Register in `_init_builtins()` with `self._add_builtin(PROVIDER_PA_AI)`.

No changes needed in `_provider_from_data()` — `OpenAIProvider` is the default fallback.

### 2. Backend Auth Module (`src/qwenpaw/providers/pa_ai_auth.py`)

New file containing CAS/SM4 logic extracted from `llm/apikey-generator.py`:

- `sm4_ecb_pkcs7_hex(plain_text: str, key_text: str) -> str` — SM4 ECB encryption with PKCS7 padding
- `start_cas_auth() -> tuple[str, threading.Event, dict]` — starts local HTTP callback server on random port, returns (cas_url, done_event, result_dict)
- `generate_api_key_via_cas() -> str` — full flow: start callback server → wait for ticket → SM4 encrypt → return key

Constants:
- `DEFAULT_CAS_LOGIN_URL = "https://vt.paic.com.cn/user-center-web/cas/login"`
- `DEFAULT_SM4_KEY_HEX = "52aad0c09e6b46818a11ee702d6fe0ba"`
- `SERVICE_NAME_PARAM = "c3FtcS1jb2RlLXBpbG90"`
- `LOGIN_TIMEOUT_SECONDS = 300`

The local callback server runs in a background thread (same pattern as `apikey-generator.py`). On CAS callback:
1. Receive ticket from query parameter
2. SM4-encrypt ticket to produce API key
3. Save key to `ProviderManager` (thread-safe singleton)
4. Shut down callback server

### 3. Backend API Endpoint (`providers.py` router)

`POST /api/models/pa-ai/generate-key`

Request: empty body

Response:
```json
{
  "cas_url": "https://vt.paic.com.cn/user-center-web/cas/login?service=...",
  "status": "waiting"
}
```

Error cases:
- A CAS auth session is already in progress → 409 Conflict
- Auth flow fails → 500 with error message

Implementation:
1. Check if a CAS auth session is already running (module-level flag)
2. Call `generate_api_key_via_cas()` in a background thread
3. Immediately return the CAS URL
4. The background thread saves the key to `ProviderManager` when callback arrives
5. Frontend detects completion by polling provider config

### 4. Frontend API Client (`provider.ts`)

Add function:
```typescript
export const generatePAIApiKey = () =>
  request.post('/models/pa-ai/generate-key')
```

### 5. Frontend UI (`ProviderConfigModal.tsx`)

Conditionally render (when `provider.id === 'pa-ai'`):

**"Generate Key" button** next to the API Key input field:
1. Calls `generatePAIApiKey()`
2. Opens returned `cas_url` in new browser tab (`window.open`)
3. Shows "等待 CAS 认证..." loading indicator below API Key field
4. Polls `GET /api/models` every 3 seconds to check if `pa-ai` provider now has an `api_key`
5. On detection: stops polling, updates form with masked key, hides loading indicator
6. On timeout (5 minutes): stops polling, shows "认证超时，请重试" error

The button is disabled while a generation session is in progress.

### 6. i18n (`locales/`)

Add keys for:
- Button label: "生成 API Key" / "Generate API Key"
- Loading text: "等待 CAS 认证..." / "Waiting for CAS authentication..."
- Timeout error: "认证超时，请重试" / "Authentication timeout, please try again"

## Error Handling

| Scenario | Handling |
|----------|----------|
| CAS login timeout (5 min) | Backend callback server shuts down, frontend polling timeout shows error |
| User closes browser tab | Backend callback server times out after 5 min, no resource leak |
| API key expired | User clicks "Generate Key" again, overwrites old key |
| Network unreachable (intranet) | "Test Connection" fails, prompts user to check network |
| Concurrent "Generate Key" clicks | Backend detects existing session, returns 409 Conflict |

## File Change Summary

| File | Type | Description |
|------|------|-------------|
| `src/qwenpaw/providers/pa_ai_auth.py` | New | SM4 encryption + CAS auth logic |
| `src/qwenpaw/providers/provider_manager.py` | Modify | Add `PROVIDER_PA_AI`, register in `_init_builtins()` |
| `src/qwenpaw/app/routers/providers.py` | Modify | Add `POST /models/pa-ai/generate-key` endpoint |
| `console/src/api/modules/provider.ts` | Modify | Add `generatePAIApiKey()` function |
| `console/src/components/modals/ProviderConfigModal.tsx` | Modify | Conditional "Generate Key" button + polling |
| `console/src/locales/` | Modify | i18n strings |

Total: 1 new file + 5 modified files, ~200-300 lines of core logic.
