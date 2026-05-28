#!/usr/bin/env python3
from __future__ import annotations

import argparse
import binascii
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_AI_KEY_ENDPOINT = "https://wizard-ai.paic.com.cn/code_pilot/api/request"
DEFAULT_CAS_LOGIN_URL = "https://vt.paic.com.cn/user-center-web/cas/login"
DEFAULT_SM4_KEY_HEX = "52aad0c09e6b46818a11ee702d6fe0ba"
AUTH_DIR_NAME = ".aima-go"
AUTH_FILE_NAME = "auth.json"
SERVICE_NAME_PARAM = "c3Ftcy1jb2RlLXBpbG90"
LOGIN_TIMEOUT_SECONDS = 300
SM4_BLOCK_SIZE = 16


@dataclass
class Config:
    ai_key_endpoint: str
    aima_pub_salt_key: str
    request_timeout: int
    cas_login_url: str


@dataclass
class AuthInfo:
    token: str
    username: str
    loginTime: int
    expireTime: int


class APIKeyGenerator:
    def __init__(self) -> None:
        home = Path.home()
        auth_dir = home / AUTH_DIR_NAME
        auth_dir.mkdir(parents=True, exist_ok=True)

        self.config = Config(
            ai_key_endpoint=os.getenv("AI_KEY_ENDPOINT", DEFAULT_AI_KEY_ENDPOINT),
            aima_pub_salt_key=os.getenv("AI_MA_PUB_SALT_KEY", DEFAULT_SM4_KEY_HEX),
            request_timeout=get_env_int("REQUEST_TIMEOUT", 60000),
            cas_login_url=os.getenv("CAS_LOGIN_URL", DEFAULT_CAS_LOGIN_URL),
        )
        self.auth_file = auth_dir / AUTH_FILE_NAME

    def generate_api_key(self) -> str:
        result: dict[str, str | BaseException] = {}
        done = threading.Event()

        class CallbackHandler(BaseHTTPRequestHandler):
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
                    result["error"] = RuntimeError("missing ticket in callback")
                    done.set()
                    self.send_response(HTTPStatus.BAD_REQUEST)
                    self.end_headers()
                    self.wfile.write(b"Missing ticket")
                    return

                result["ticket"] = ticket
                done.set()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(LOGIN_SUCCESS_HTML.encode("utf-8"))

        server = ThreadingHTTPServer(("localhost", 0), CallbackHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        callback_url = f"http://localhost:{port}/cas/callback"
        service = urllib.parse.quote(callback_url, safe="")
        login_url = f"{self.config.cas_login_url}?service={service}&n={SERVICE_NAME_PARAM}"

        logging.info("Opening browser for CAS login: %s", login_url)
        if not webbrowser.open(login_url):
            print(f"Please open this URL in your browser to login: {login_url}", file=sys.stderr)

        try:
            if not done.wait(LOGIN_TIMEOUT_SECONDS):
                raise TimeoutError("login timeout")

            if "error" in result:
                error = result["error"]
                if isinstance(error, BaseException):
                    raise error
                raise RuntimeError(str(error))

            ticket = str(result["ticket"])
            logging.info("Received CAS ticket")
            api_key = sm4_ecb_pkcs7_hex(ticket, self.config.aima_pub_salt_key)

            now = int(time.time())
            self.save_auth_info(
                AuthInfo(
                    token=api_key,
                    username="cas_user",
                    loginTime=now,
                    expireTime=now + 24 * 60 * 60,
                )
            )
            return api_key
        finally:
            server.shutdown()
            server.server_close()

    def save_auth_info(self, auth: AuthInfo) -> None:
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_file.write_text(
            json.dumps(asdict(auth), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logging.info("认证信息已保存到: %s", self.auth_file)

    def load_auth_info(self) -> AuthInfo:
        if not self.auth_file.exists():
            raise FileNotFoundError(f"认证文件不存在: {self.auth_file}")

        try:
            data = json.loads(self.auth_file.read_text(encoding="utf-8"))
        except OSError as exc:
            raise OSError(f"读取认证文件失败: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"反序列化认证信息失败: {exc}") from exc

        return AuthInfo(
            token=str(data.get("token", "")),
            username=str(data.get("username", "")),
            loginTime=int(data.get("loginTime", 0)),
            expireTime=int(data.get("expireTime", 0)),
        )

    def is_auth_expired(self, auth: AuthInfo | None) -> bool:
        return auth is None or auth.expireTime == 0 or auth.expireTime <= int(time.time())

    def get_existing_api_key(self) -> str:
        auth = self.load_auth_info()
        if self.is_auth_expired(auth):
            raise RuntimeError("API Key 已过期")
        return auth.token


def get_env_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logging.warning("环境变量 %s 的值 '%s' 无法转换为整数，使用默认值 %d", key, value, default)
        return default


def parse_key_16(key_text: str) -> bytes:
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


def pkcs7_pad(data: bytes, block_size: int = SM4_BLOCK_SIZE) -> bytes:
    padding = block_size - len(data) % block_size
    if padding == 0:
        padding = block_size
    return data + bytes([padding]) * padding


def sm4_ecb_pkcs7_hex(plain_text: str, key_text: str) -> str:
    key = parse_key_16(key_text)
    round_keys = sm4_key_schedule(key)
    padded = pkcs7_pad(plain_text.encode("utf-8"))
    encrypted = bytearray()
    for offset in range(0, len(padded), SM4_BLOCK_SIZE):
        encrypted.extend(sm4_encrypt_block(padded[offset : offset + SM4_BLOCK_SIZE], round_keys))
    return encrypted.hex()


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


def rotl32(value: int, shift: int) -> int:
    value &= 0xFFFFFFFF
    return ((value << shift) & 0xFFFFFFFF) | (value >> (32 - shift))


def sm4_tau(value: int) -> int:
    return (
        (SM4_SBOX[(value >> 24) & 0xFF] << 24)
        | (SM4_SBOX[(value >> 16) & 0xFF] << 16)
        | (SM4_SBOX[(value >> 8) & 0xFF] << 8)
        | SM4_SBOX[value & 0xFF]
    )


def sm4_t(value: int) -> int:
    b = sm4_tau(value)
    return b ^ rotl32(b, 2) ^ rotl32(b, 10) ^ rotl32(b, 18) ^ rotl32(b, 24)


def sm4_t_prime(value: int) -> int:
    b = sm4_tau(value)
    return b ^ rotl32(b, 13) ^ rotl32(b, 23)


def sm4_key_schedule(key: bytes) -> list[int]:
    mk = [int.from_bytes(key[i * 4 : i * 4 + 4], "big") for i in range(4)]
    k = [mk[i] ^ SM4_FK[i] for i in range(4)]
    round_keys = []
    for i in range(32):
        next_key = k[i] ^ sm4_t_prime(k[i + 1] ^ k[i + 2] ^ k[i + 3] ^ SM4_CK[i])
        next_key &= 0xFFFFFFFF
        k.append(next_key)
        round_keys.append(next_key)
    return round_keys


def sm4_encrypt_block(block: bytes, round_keys: list[int]) -> bytes:
    x = [int.from_bytes(block[i * 4 : i * 4 + 4], "big") for i in range(4)]
    for i in range(32):
        x.append((x[i] ^ sm4_t(x[i + 1] ^ x[i + 2] ^ x[i + 3] ^ round_keys[i])) & 0xFFFFFFFF)
    return b"".join(value.to_bytes(4, "big") for value in (x[35], x[34], x[33], x[32]))


LOGIN_SUCCESS_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Login Success</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 40px;
      color: #1f2937;
    }
  </style>
</head>
<body>
  <h1>Login Success</h1>
  <p>CAS authentication is complete. You can close this window.</p>
</body>
</html>"""


def run_self_test() -> None:
    key = "0123456789abcdeffedcba9876543210"
    plaintext = bytes.fromhex("0123456789abcdeffedcba9876543210")
    round_keys = sm4_key_schedule(bytes.fromhex(key))
    ciphertext = sm4_encrypt_block(plaintext, round_keys).hex()
    expected = "681edf34d206965e86b3e94f536e4246"
    if ciphertext != expected:
        raise AssertionError(f"SM4 self-test failed: got {ciphertext}, expected {expected}")
    print("SM4 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--self-test", action="store_true", help="run SM4 test vector and exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.self_test:
        run_self_test()
        return 0

    generator = APIKeyGenerator()

    print("=== API Key 生成器 ===")
    print("1. 检查现有的 API Key")
    print("2. 生成新的 API Key")
    choice = input("请选择操作 (1/2): ").strip()

    if choice == "1":
        print("\n检查现有的 API Key...")
        try:
            api_key = generator.get_existing_api_key()
        except Exception as exc:
            print(f"获取现有 API Key 失败: {exc}", file=sys.stderr)
            print("可能需要生成新的 API Key")
            return 0
        print(f"现有的 API Key: {api_key}")
        print("API Key 仍然有效")
        return 0

    if choice == "2":
        print("\n开始生成新的 API Key...")
        print("将会打开浏览器进行 CAS 认证...")
        try:
            api_key = generator.generate_api_key()
        except Exception as exc:
            print(f"生成 API Key 失败: {exc}", file=sys.stderr)
            return 1
        print(f"成功生成 API Key: {api_key}")
        print("API Key 已保存到 ~/.aima-go/auth.json")
        return 0

    print("无效的选择")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
