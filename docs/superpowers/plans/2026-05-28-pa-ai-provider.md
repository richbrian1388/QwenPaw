# PA-AI 模型供应商实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增内置模型供应商 PA-AI，接入公司内部部署的 OpenAI 兼容大模型，支持 Console 中一键生成 CAS 认证 API Key。

**Architecture:** 复用 `OpenAIProvider` 类（API 完全兼容 OpenAI 格式），新建 `pa_ai_auth.py` 模块封装 SM4 加密和 CAS 认证逻辑，新增后端 API 端点供 Console 前端调用。

**Tech Stack:** Python 3.10+ (后端), React + TypeScript + Ant Design (前端), FastAPI (API 路由), i18next (国际化)

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/qwenpaw/providers/pa_ai_auth.py` | 新增 | SM4 加密 + CAS 认证 + API Key 生成 |
| `src/qwenpaw/providers/provider_manager.py` | 修改 | 注册 PA-AI 供应商 |
| `src/qwenpaw/app/routers/providers.py` | 修改 | 添加 generate-key 端点 |
| `console/src/api/modules/provider.ts` | 修改 | 添加 generatePAIApiKey 函数 |
| `console/src/pages/Settings/Models/components/modals/ProviderConfigModal.tsx` | 修改 | 添加"生成 API Key"按钮和轮询逻辑 |
| `console/src/locales/zh.json` | 修改 | 中文 i18n 文案 |
| `console/src/locales/en.json` | 修改 | 英文 i18n 文案 |
| `tests/test_pa_ai_auth.py` | 新增 | SM4 加密和认证逻辑单元测试 |

---

### Task 1: 新建 SM4 加密 + CAS 认证模块

**Files:**
- Create: `src/qwenpaw/providers/pa_ai_auth.py`
- Test: `tests/test_pa_ai_auth.py`

- [ ] **Step 1: 编写 SM4 加密单元测试**

创建 `tests/test_pa_ai_auth.py`：

```python
"""Tests for PA-AI SM4 encryption and CAS auth module."""
from qwenpaw.providers.pa_ai_auth import sm4_ecb_pkcs7_hex


def test_sm4_self_test_vector():
    """Verify SM4 against the standard test vector from GB/T 32907-2016."""
    key = "0123456789abcdeffedcba9876543210"
    plaintext = "0123456789abcdeffedcba9876543210"
    expected = "681edf34d206965e86b3e94f536e4246"
    assert sm4_ecb_pkcs7_hex(plaintext, key) == expected


def test_sm4_known_input():
    """Verify SM4 encryption with a known key produces deterministic output."""
    key = "52aad0c09e6b46818a11ee702d6fe0ba"
    result = sm4_ecb_pkcs7_hex("test-ticket-123", key)
    assert len(result) > 0
    assert sm4_ecb_pkcs7_hex("test-ticket-123", key) == result


def test_sm4_roundtrip_consistency():
    """Same plaintext + same key always produces same ciphertext."""
    key = "52aad0c09e6b46818a11ee702d6fe0ba"
    a = sm4_ecb_pkcs7_hex("hello world", key)
    b = sm4_ecb_pkcs7_hex("hello world", key)
    assert a == b
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw && python -m pytest tests/test_pa_ai_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'qwenpaw.providers.pa_ai_auth'`

- [ ] **Step 3: 实现模块**

创建 `src/qwenpaw/providers/pa_ai_auth.py`：

```python
# -*- coding: utf-8 -*-
"""PA-AI provider authentication: SM4 encryption and CAS-based API key generation."""
from __future__ import annotations

import binascii
import logging
import os
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_CAS_LOGIN_URL = "https://vt.paic.com.cn/user-center-web/cas/login"
DEFAULT_SM4_KEY_HEX = "52aad0c09e6b46818a11ee702d6fe0ba"
SERVICE_NAME_PARAM = "c3FtcS1jb2RlLXBpbG90"
LOGIN_TIMEOUT_SECONDS = 300

SM4_BLOCK_SIZE = 16

SM4_SBOX = [
    0xD6, 0x90, 0xE9, 0xFE, 0xCC, 0xE1, 0x3D, 0xB7, 0x16, 0xB6, 0x14, 0xC2, 0x28, 0xFB, 0x2C, 0x05,
    0x2B, 0x67, 0x9A, 0x76, 0x2A, 0xBE, 0x04, 0xC3, 0xAA, 0x44, 0x13, 0x26, 0x49, 0x86, 0x06, 0x99,
    0x9C, 0x42, 0x50, 0xF4, 0x91, 0xEF, 0x98, 0x7A, 0x33, 0x54, 0x0B, 0x43, 0xED, 0xCF, 0xAC, 0x62,
    0xE4, 0xB3, 0x1C, 0xA9, 0xC9, 0x08, 0xE8, 0x95, 0x80, 0xDF, 0x94, 0xFA, 0x75, 0x8F, 0x3F, 0xA6,
    0x47, 0x07, 0xA7, 0xFC, 0xF3, 0x73, 0x17, 0xBA, 0x83, 0x59, 0x3C, 0x19, 0xE6, 0x85, 0x4F, 0xA8,
    0x68, 0x6B, 0x81, 0xB2, 0x71, 0x64, 0xDA, 0x8B, 0xF8, 0xEB, 0x0F, 0x4B, 0x70, 0x56, 0x9D, 0x35,
    0x1E, 0x24, 0x0E, 0x5E, 0x63, 0x58, 0xD1, 0xA2, 0x25, 0x22, 0x7C, 0x3B, 0x01, 0x21, 0x78, 0x87,
    0xD4, 0x00, 0x46, 0x57, 0x9F, 0xD3, 0x27, 0x52, 0x4C, 0x36, 0x02, 0xE7, 0xA0, 0xC4, 0xC8, 0x9E,
    0xEA, 0xBF, 0x8A, 0xD2, 0x40, 0xC7, 0x38, 0xB5, 0xA3, 0xF7, 0xF2, 0xCE, 0xF9, 0x61, 0x15, 0xA1,
    0xE0, 0xAE, 0x5D, 0xA4, 0x9B, 0x34, 0x1A, 0x55, 0xAD, 0x93, 0x32, 0x30, 0xF5, 0x8C, 0xB1, 0xE3,
    0x1D, 0xF6, 0xE2, 0x2E, 0x82, 0x66, 0xCA, 0x60, 0xC0, 0x29, 0x23, 0xAB, 0x0D, 0x53, 0x4E, 0x6F,
    0xD5, 0xDB, 0x37, 0x45, 0xDE, 0xFD, 0x8E, 0x2F, 0x03, 0xFF, 0x6A, 0x72, 0x6D, 0x6C, 0x5B, 0x51,
    0x8D, 0x1B, 0xAF, 0x92, 0xBB, 0xDD, 0xBC, 0x7F, 0x11, 0xD9, 0x5C, 0x41, 0x1F, 0x10, 0x5A, 0xD8,
    0x0A, 0xC1, 0x31, 0x88, 0xA5, 0xCD, 0x7B, 0xBD, 0x2D, 0x74, 0xD0, 0x12, 0xB8, 0xE5, 0xB4, 0xB0,
    0x89, 0x69, 0x97, 0x4A, 0x0C, 0x96, 0x77, 0x7E, 0x65, 0xB9, 0xF1, 0x09, 0xC5, 0x6E, 0xC6, 0x84,
    0x18, 0xF0, 0x7D, 0xEC, 0x3A, 0xDC, 0x4D, 0x20, 0x79, 0xEE, 0x5F, 0x3E, 0xD7, 0xCB, 0x39, 0x48,
]

SM4_FK = [0xA3B1BAC6, 0x56AA3350, 0x677D9197, 0xB27022DC]

SM4_CK = [
    0x00070E15, 0x1C232A31, 0x383F464D, 0x545B6269,
    0x70777E85, 0x8C939AA1, 0xA8AFB6BD, 0xC4CBD2D9,
    0xE0E7EEF5, 0xFC030A11, 0x181F262D, 0x343B4249,
    0x50575E65, 0x6C737A81, 0x888F969D, 0xA4ABB2B9,
    0xC0C7CED5, 0xDCE3EAF1, 0xF8FF060D, 0x141B2229,
    0x30373E45, 0x4C535A61, 0x686F767D, 0x848B9299,
    0xA0A7AEB5, 0xBCC3CAD1, 0xD8DFE6ED, 0xF4FB0209,
    0x10171E25, 0x2C333A41, 0x484F565D, 0x646B7279,
]

_LOGIN_SUCCESS_HTML = """<!DOCTYPE html>
<html>
<head><title>Login Success</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 40px; color: #1f2937; }
</style>
</head>
<body><h1>Login Success</h1><p>CAS authentication is complete. You can close this window.</p></body>
</html>"""


# --- SM4 encryption primitives ---

def _rotl32(value: int, shift: int) -> int:
    value &= 0xFFFFFFFF
    return ((value << shift) & 0xFFFFFFFF) | (value >> (32 - shift))


def _sm4_tau(value: int) -> int:
    return (
        (SM4_SBOX[(value >> 24) & 0xFF] << 24)
        | (SM4_SBOX[(value >> 16) & 0xFF] << 16)
        | (SM4_SBOX[(value >> 8) & 0xFF] << 8)
        | SM4_SBOX[value & 0xFF]
    )


def _sm4_t(value: int) -> int:
    b = _sm4_tau(value)
    return b ^ _rotl32(b, 2) ^ _rotl32(b, 10) ^ _rotl32(b, 18) ^ _rotl32(b, 24)


def _sm4_t_prime(value: int) -> int:
    b = _sm4_tau(value)
    return b ^ _rotl32(b, 13) ^ _rotl32(b, 23)


def _sm4_key_schedule(key: bytes) -> list[int]:
    mk = [int.from_bytes(key[i * 4 : i * 4 + 4], "big") for i in range(4)]
    k = [mk[i] ^ SM4_FK[i] for i in range(4)]
    round_keys = []
    for i in range(32):
        next_key = k[i] ^ _sm4_t_prime(k[i + 1] ^ k[i + 2] ^ k[i + 3] ^ SM4_CK[i])
        next_key &= 0xFFFFFFFF
        k.append(next_key)
        round_keys.append(next_key)
    return round_keys


def _sm4_encrypt_block(block: bytes, round_keys: list[int]) -> bytes:
    x = [int.from_bytes(block[i * 4 : i * 4 + 4], "big") for i in range(4)]
    for i in range(32):
        x.append((x[i] ^ _sm4_t(x[i + 1] ^ x[i + 2] ^ x[i + 3] ^ round_keys[i])) & 0xFFFFFFFF)
    return b"".join(value.to_bytes(4, "big") for value in (x[35], x[34], x[33], x[32]))


def _pkcs7_pad(data: bytes, block_size: int = SM4_BLOCK_SIZE) -> bytes:
    padding = block_size - len(data) % block_size
    if padding == 0:
        padding = block_size
    return data + bytes([padding]) * padding


def _parse_key_16(key_text: str) -> bytes:
    try:
        decoded = binascii.unhexlify(key_text)
        if len(decoded) == SM4_BLOCK_SIZE:
            return decoded
    except (binascii.Error, ValueError):
        pass
    raw = key_text.encode("utf-8")
    if len(raw) == SM4_BLOCK_SIZE:
        return raw
    raise ValueError(f"SM4 key must be 16 bytes, got {len(raw)}")


def sm4_ecb_pkcs7_hex(plain_text: str, key_text: str) -> str:
    """SM4 ECB-mode encryption with PKCS7 padding, returning hex string."""
    key = _parse_key_16(key_text)
    round_keys = _sm4_key_schedule(key)
    padded = _pkcs7_pad(plain_text.encode("utf-8"))
    encrypted = bytearray()
    for offset in range(0, len(padded), SM4_BLOCK_SIZE):
        encrypted.extend(_sm4_encrypt_block(padded[offset : offset + SM4_BLOCK_SIZE], round_keys))
    return encrypted.hex()


# --- CAS authentication session management ---

_active_cas_session: bool = False
_cas_session_lock = threading.Lock()


def is_cas_session_active() -> bool:
    """Check whether a CAS auth session is currently running."""
    with _cas_session_lock:
        return _active_cas_session


def start_cas_auth_session(
    on_success: Callable[[str], None] | None = None,
) -> tuple[str, threading.Thread]:
    """Start a CAS auth session that runs in the background.

    Starts a local HTTP callback server, constructs the CAS login URL, and
    waits for the callback in a background thread.  When the ticket arrives,
    it is SM4-encrypted to produce the API key.

    Args:
        on_success: Optional callback invoked with the generated API key
                    string when authentication succeeds.

    Returns:
        (cas_url, bg_thread) — the URL the user should open in the browser,
        and the background thread handling the callback.

    Raises:
        RuntimeError: if a session is already active.
    """
    global _active_cas_session
    with _cas_session_lock:
        if _active_cas_session:
            raise RuntimeError("CAS authentication session already in progress")
        _active_cas_session = True

    sm4_key = os.getenv("PA_AI_SM4_KEY", DEFAULT_SM4_KEY_HEX)
    cas_login_url = os.getenv("PA_AI_CAS_LOGIN_URL", DEFAULT_CAS_LOGIN_URL)

    ticket_result: dict[str, str | BaseException] = {}
    ticket_done = threading.Event()

    class _CBHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            ticket = query.get("ticket", [""])[0]
            if parsed.path != "/cas/callback":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not ticket:
                ticket_result["error"] = RuntimeError("missing ticket in callback")
                ticket_done.set()
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.end_headers()
                self.wfile.write(b"Missing ticket")
                return
            ticket_result["ticket"] = ticket
            ticket_done.set()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_LOGIN_SUCCESS_HTML.encode("utf-8"))

    server = ThreadingHTTPServer(("localhost", 0), _CBHandler)
    port = server.server_address[1]
    callback_url = f"http://localhost:{port}/cas/callback"
    service = urllib.parse.quote(callback_url, safe="")
    cas_url = f"{cas_login_url}?service={service}&n={SERVICE_NAME_PARAM}"

    def _bg_worker() -> None:
        global _active_cas_session
        try:
            if not ticket_done.wait(LOGIN_TIMEOUT_SECONDS):
                logger.error("CAS auth: timed out waiting for callback")
                return
            if "error" in ticket_result:
                err = ticket_result["error"]
                logger.error("CAS auth: callback error — %s", err)
                return
            ticket = str(ticket_result["ticket"])
            api_key = sm4_ecb_pkcs7_hex(ticket, sm4_key)
            logger.info("CAS auth: API key generated successfully")
            if on_success:
                on_success(api_key)
        except Exception as exc:
            logger.exception("CAS auth: unexpected error: %s", exc)
        finally:
            server.shutdown()
            server.server_close()
            with _cas_session_lock:
                _active_cas_session = False

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    bg_thread = threading.Thread(target=_bg_worker, daemon=True)
    bg_thread.start()

    logger.info("CAS auth: started session, callback on localhost:%d", port)
    return cas_url, bg_thread
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw && python -m pytest tests/test_pa_ai_auth.py -v`
Expected: 3 个测试全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/qwenpaw/providers/pa_ai_auth.py tests/test_pa_ai_auth.py
git commit -m "feat: add PA-AI SM4 encryption and CAS auth module"
```

---

### Task 2: 注册 PA-AI 供应商到 ProviderManager

**Files:**
- Modify: `src/qwenpaw/providers/provider_manager.py` (line ~1003, 在最后一个 PROVIDER 常量之后)

- [ ] **Step 1: 添加 PROVIDER_PA_AI 常量**

在 `provider_manager.py` 的 `PROVIDER_VOLCENGINE_CN_CODINGPLAN` (line ~994-1003) 之后，`class ProviderManager` 之前，添加：

```python
PROVIDER_PA_AI = OpenAIProvider(
    id="pa-ai",
    name="PA-AI",
    base_url="https://wizard-ai.paic.com.cn/code_pilot/api/v1",
    api_key_prefix="",
    models=[
        ModelInfo(id="PUB-GLM-4.7", name="PUB-GLM-4.7"),
    ],
    freeze_url=True,
    require_api_key=True,
    support_model_discovery=True,
    support_connection_check=True,
)
```

- [ ] **Step 2: 注册到 _init_builtins()**

在 `_init_builtins()` 方法中 (line ~1073)，在 `self._add_builtin(PROVIDER_VOLCENGINE_CN_CODINGPLAN)` 之后添加：

```python
        self._add_builtin(PROVIDER_PA_AI)
```

- [ ] **Step 3: 验证无语法错误**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw && python -c "from qwenpaw.providers.provider_manager import PROVIDER_PA_AI; print(PROVIDER_PA_AI.id, PROVIDER_PA_AI.name)"`
Expected: 输出 `pa-ai PA-AI`

- [ ] **Step 4: 提交**

```bash
git add src/qwenpaw/providers/provider_manager.py
git commit -m "feat: register PA-AI provider in ProviderManager"
```

---

### Task 3: 添加后端 generate-key API 端点

**Files:**
- Modify: `src/qwenpaw/app/routers/providers.py`

- [ ] **Step 1: 添加 import**

在 `providers.py` 文件顶部 import 区域 (line ~29)，在 `from ...providers.openrouter_provider import OpenRouterProvider` 之后添加：

```python
from ...providers import pa_ai_auth
```

- [ ] **Step 2: 添加端点**

在文件末尾（OpenRouter 的 `filter_models` 端点之后），添加以下代码。此端点启动 CAS 认证会话并立即返回 CAS 登录 URL。当后台线程完成认证后，通过 `on_success` 回调将 API key 保存到 ProviderManager。

```python
# ---- PA-AI specific endpoint ----


class GenerateKeyResponse(BaseModel):
    cas_url: str = Field(..., description="CAS login URL to open in browser")
    status: str = Field(default="waiting", description="Session status")


@router.post(
    "/pa-ai/generate-key",
    response_model=GenerateKeyResponse,
    summary="Generate PA-AI API key via CAS authentication",
)
async def generate_pa_ai_key(
    request: Request,
    manager: ProviderManager = Depends(get_provider_manager),
) -> GenerateKeyResponse:
    """Start CAS authentication to generate an API key for PA-AI provider.

    The flow:
    1. Backend starts a local HTTP callback server on a random port.
    2. Returns the CAS login URL — frontend opens it in a new tab.
    3. User authenticates with CAS, which redirects to the local callback.
    4. The background thread SM4-encrypts the ticket and saves the key
       via the on_success callback.
    5. Frontend polls GET /api/models to detect the key has been set.
    """
    if pa_ai_auth.is_cas_session_active():
        raise HTTPException(
            status_code=409,
            detail="A CAS authentication session is already in progress.",
        )

    def _save_key_to_provider(api_key: str) -> None:
        provider = manager.builtin_providers.get("pa-ai")
        if provider is None:
            logger.error("PA-AI: provider not found in builtin_providers")
            return
        provider.api_key = api_key
        # Persist to disk using the same flow as PUT /{provider_id}/config
        manager._save_provider_to_disk(provider)
        logger.info("PA-AI: API key saved to provider config")

    try:
        cas_url, _thread = pa_ai_auth.start_cas_auth_session(
            on_success=_save_key_to_provider,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return GenerateKeyResponse(cas_url=cas_url)
```

注意：`_save_key_to_provider` 中调用了 `manager._save_provider_to_disk(provider)`。需要在实现时确认 `ProviderManager` 是否有该方法。如果没有，查看 `ProviderManager` 中 `PUT /{provider_id}/config` 端点使用的持久化逻辑（通常是在 `configure_builtin_provider()` 或类似方法中），复用相同的持久化方式。备选方案是直接调用已有的配置更新方法，如：

```python
    def _save_key_to_provider(api_key: str) -> None:
        provider = manager.builtin_providers.get("pa-ai")
        if provider is None:
            return
        provider.update_config(api_key=api_key)
```

实现时应先查看 `ProviderManager` 中已有的持久化方法，选择最合适的方式。

- [ ] **Step 3: 验证 API 端点注册**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw && python -c "from qwenpaw.app.routers.providers import router; paths = [r.path for r in router.routes]; assert '/pa-ai/generate-key' in paths; print('PASS: endpoint registered')"`
Expected: `PASS: endpoint registered`

- [ ] **Step 4: 提交**

```bash
git add src/qwenpaw/app/routers/providers.py
git commit -m "feat: add PA-AI generate-key API endpoint"
```

---

### Task 4: 添加前端 API 客户端函数

**Files:**
- Modify: `console/src/api/modules/provider.ts`

- [ ] **Step 1: 添加 generatePAIApiKey 函数**

在 `provider.ts` 文件中，在 `filterOpenRouterModels` 方法之后（line ~191），`}` 闭合 `providerApi` 对象之前，添加：

```typescript
  /* ---- PA-AI specific endpoint ---- */

  generatePAIApiKey: () =>
    request<{ cas_url: string; status: string }>(
      "/models/pa-ai/generate-key",
      { method: "POST" },
    ),
```

- [ ] **Step 2: 验证无 TypeScript 编译错误**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw/console && npx tsc --noEmit 2>&1 | head -20`
Expected: 无与 `provider.ts` 相关的错误

- [ ] **Step 3: 提交**

```bash
git add console/src/api/modules/provider.ts
git commit -m "feat: add generatePAIApiKey frontend API function"
```

---

### Task 5: 添加 i18n 国际化文案

**Files:**
- Modify: `console/src/locales/zh.json`
- Modify: `console/src/locales/en.json`

- [ ] **Step 1: 在 zh.json 中添加中文文案**

在 `zh.json` 的 `models` 对象中，找到 `"apiKeyShouldStart"` 这一行（约 line 1195）之后，添加：

```json
    "generateApiKey": "生成 API Key",
    "generatingApiKey": "等待 CAS 认证...",
    "generateApiKeyTimeout": "认证超时，请重试",
    "generateApiKeyConflict": "已有认证会话进行中，请稍后重试",
    "generateApiKeyFailed": "生成 API Key 失败：{{message}}",
```

- [ ] **Step 2: 在 en.json 中添加英文文案**

在 `en.json` 的 `models` 对象中，找到 `"apiKeyShouldStart"` 这一行（约 line 1347）之后，添加：

```json
    "generateApiKey": "Generate API Key",
    "generatingApiKey": "Waiting for CAS authentication...",
    "generateApiKeyTimeout": "Authentication timeout, please try again",
    "generateApiKeyConflict": "An authentication session is already in progress. Please try again later.",
    "generateApiKeyFailed": "Failed to generate API Key: {{message}}",
```

- [ ] **Step 3: 验证 JSON 格式正确**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw/console && python3 -c "import json; json.load(open('src/locales/zh.json')); json.load(open('src/locales/en.json')); print('JSON valid')"`
Expected: `JSON valid`

- [ ] **Step 4: 提交**

```bash
git add console/src/locales/zh.json console/src/locales/en.json
git commit -m "feat: add PA-AI i18n strings for zh and en"
```

---

### Task 6: 前端 ProviderConfigModal 添加"生成 API Key"按钮

**Files:**
- Modify: `console/src/pages/Settings/Models/components/modals/ProviderConfigModal.tsx`

- [ ] **Step 1: 添加 state**

在组件函数内部（约 line 298 `const [authMode, ...]` 之后），添加：

```typescript
  const [generatingKey, setGeneratingKey] = useState(false);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
```

- [ ] **Step 2: 添加轮询清理 effect**

在 `useEffect` 块之后（约 line 469 之后），添加：

```typescript
  // Cleanup polling timer on unmount
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
      }
    };
  }, []);
```

- [ ] **Step 3: 添加 handleGenerateKey 函数**

在 `handleRevoke` 函数之后（约 line 607 之后），添加：

```typescript
  const isPAAIProvider = provider.id === "pa-ai";

  const handleGenerateKey = async () => {
    setGeneratingKey(true);
    try {
      const result = await api.generatePAIApiKey();
      window.open(result.cas_url, "_blank");

      const startTime = Date.now();
      const POLL_INTERVAL = 3000;
      const TIMEOUT = 5 * 60 * 1000;

      pollTimerRef.current = setInterval(async () => {
        if (Date.now() - startTime > TIMEOUT) {
          if (pollTimerRef.current) clearInterval(pollTimerRef.current);
          setGeneratingKey(false);
          message.error(t("models.generateApiKeyTimeout"));
          return;
        }
        try {
          const providers = await api.listProviders();
          const paAIProvider = providers.find((p: any) => p.id === "pa-ai");
          if (paAIProvider?.api_key) {
            if (pollTimerRef.current) clearInterval(pollTimerRef.current);
            setGeneratingKey(false);
            await onSaved();
            message.success(
              t("models.configurationSaved", { name: provider.name }),
            );
          }
        } catch {
          // Ignore poll errors, keep trying
        }
      }, POLL_INTERVAL);
    } catch (error) {
      setGeneratingKey(false);
      const errMsg =
        error instanceof Error
          ? error.message
          : t("models.generateApiKeyFailed", { message: "Unknown error" });
      message.error(errMsg);
    }
  };
```

- [ ] **Step 4: 在 API Key 表单项后添加按钮**

在 `{/* API Key */}` 表单项的 `</Form.Item>` 之后（约 line 772），`<div className={styles.advancedConfigSection}>` 之前，添加：

```tsx
        {/* PA-AI Generate Key Button */}
        {isPAAIProvider && (
          <Form.Item>
            <Button
              type="primary"
              loading={generatingKey}
              onClick={handleGenerateKey}
              disabled={generatingKey}
            >
              {generatingKey
                ? t("models.generatingApiKey")
                : t("models.generateApiKey")}
            </Button>
          </Form.Item>
        )}
```

- [ ] **Step 5: 验证 TypeScript 编译**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw/console && npx tsc --noEmit 2>&1 | head -20`
Expected: 无与 `ProviderConfigModal.tsx` 相关的错误

- [ ] **Step 6: 提交**

```bash
git add console/src/pages/Settings/Models/components/modals/ProviderConfigModal.tsx
git commit -m "feat: add Generate API Key button for PA-AI in ProviderConfigModal"
```

---

### Task 7: 集成验证

**Files:**
- No new files

- [ ] **Step 1: 验证 PA-AI provider 已注册**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw && python -c "
from qwenpaw.providers.provider_manager import PROVIDER_PA_AI
assert PROVIDER_PA_AI.id == 'pa-ai'
assert PROVIDER_PA_AI.name == 'PA-AI'
assert PROVIDER_PA_AI.base_url == 'https://wizard-ai.paic.com.cn/code_pilot/api/v1'
assert any(m.id == 'PUB-GLM-4.7' for m in PROVIDER_PA_AI.models)
assert PROVIDER_PA_AI.freeze_url == True
print('PASS: PROVIDER_PA_AI configured correctly')
"`

- [ ] **Step 2: 验证 API 端点注册**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw && python -c "
from qwenpaw.app.routers.providers import router
paths = [r.path for r in router.routes]
assert '/pa-ai/generate-key' in paths, f'Not found in: {paths}'
print('PASS: /pa-ai/generate-key endpoint registered')
"`

- [ ] **Step 3: 运行 SM4 单元测试**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw && python -m pytest tests/test_pa_ai_auth.py -v`
Expected: 3 个测试全部 PASS

- [ ] **Step 4: 验证前端编译**

Run: `cd /Users/zhengbangzhen664/codebases/web/QwenPaw/console && npx tsc --noEmit`
Expected: 无编译错误
