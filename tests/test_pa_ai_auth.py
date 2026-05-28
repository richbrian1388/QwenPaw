"""Tests for PA-AI SM4 encryption and CAS auth module."""
from qwenpaw.providers.pa_ai_auth import (
    _sm4_encrypt_block,
    _sm4_key_schedule,
    sm4_ecb_pkcs7_hex,
)


def test_sm4_block_level_test_vector():
    """Verify raw SM4 block encryption against GB/T 32907-2016 test vector."""
    key_bytes = bytes.fromhex("0123456789abcdeffedcba9876543210")
    plaintext = bytes.fromhex("0123456789abcdeffedcba9876543210")
    expected = "681edf34d206965e86b3e94f536e4246"
    round_keys = _sm4_key_schedule(key_bytes)
    result = _sm4_encrypt_block(plaintext, round_keys).hex()
    assert result == expected


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
