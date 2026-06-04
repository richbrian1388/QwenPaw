#!/usr/bin/env python3
"""Test PA-AI API with stream=True vs stream=False."""
import asyncio
import json
import traceback

from src.qwenpaw.security.secret_store import decrypt
from openai import AsyncOpenAI


def get_key():
    with open("/Users/zhengbangzhen664/.qwenpaw.secret/providers/builtin/pa-ai.json") as f:
        config = json.load(f)
    return decrypt(config["api_key"]), config["base_url"]


async def test_stream(client: AsyncOpenAI, model: str):
    print("\n=== stream=True ===")
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "你好"}],
            max_tokens=20,
            stream=True,
        )
        chunk_count = 0
        async for chunk in stream:
            chunk_count += 1
            delta = chunk.choices[0].delta.content if chunk.choices else ""
            print(f"chunk #{chunk_count}: delta={repr(delta)}")
        print(f"Total chunks: {chunk_count}")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()


async def test_non_stream(client: AsyncOpenAI, model: str):
    print("\n=== stream=False ===")
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "你好"}],
            max_tokens=20,
            stream=False,
        )
        content = resp.choices[0].message.content if resp.choices else ""
        print(f"SUCCESS: content={repr(content)}")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        traceback.print_exc()


async def main():
    api_key, base_url = get_key()
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    import sys
    model = sys.argv[1] if len(sys.argv) > 1 else "PUB-DeepSeek-V4-Flash"

    await test_non_stream(client, model)
    await test_stream(client, model)


if __name__ == "__main__":
    asyncio.run(main())
