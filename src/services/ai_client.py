"""
AI Client with Anthropic → OpenRouter fallback.
Anthropic API 실패 시 자동으로 OpenRouter로 전환.
"""

import os
import httpx
from src.utils.logger import logger

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Model mapping: Anthropic → OpenRouter equivalent
OPENROUTER_MODEL = "anthropic/claude-sonnet-4"


def _get_keys() -> tuple[str, str]:
    return (
        os.getenv("ANTHROPIC_API_KEY", ""),
        os.getenv("OPENROUTER_API_KEY", ""),
    )


async def chat(
    system: str,
    user_message: str,
    max_tokens: int = 4000,
    timeout: float = 180.0,
) -> str:
    """
    Send a chat request. Tries Anthropic first, falls back to OpenRouter.
    Returns the text content of the response.
    """
    anthropic_key, openrouter_key = _get_keys()

    # --- Try Anthropic ---
    if anthropic_key:
        try:
            text = await _anthropic_chat(anthropic_key, system, user_message, max_tokens, timeout)
            return text
        except Exception as e:
            logger.warning(f"Anthropic API failed: {e}")
            if not openrouter_key:
                raise

    # --- Fallback: OpenRouter ---
    if openrouter_key:
        logger.info("Falling back to OpenRouter")
        return await _openrouter_chat(openrouter_key, system, user_message, max_tokens, timeout)

    raise RuntimeError("No AI API key configured (ANTHROPIC_API_KEY or OPENROUTER_API_KEY)")


async def chat_stream(
    system: str,
    user_message: str,
    max_tokens: int = 32000,
    timeout: float = 180.0,
):
    """
    Streaming chat. Tries Anthropic first, falls back to OpenRouter.
    Yields text chunks as they arrive.
    """
    anthropic_key, openrouter_key = _get_keys()

    # --- Try Anthropic ---
    if anthropic_key:
        try:
            async for chunk in _anthropic_stream(anthropic_key, system, user_message, max_tokens, timeout):
                yield chunk
            return
        except Exception as e:
            logger.warning(f"Anthropic stream failed: {e}")
            if not openrouter_key:
                raise

    # --- Fallback: OpenRouter ---
    if openrouter_key:
        logger.info("Falling back to OpenRouter stream")
        async for chunk in _openrouter_stream(openrouter_key, system, user_message, max_tokens, timeout):
            yield chunk
        return

    raise RuntimeError("No AI API key configured")


# ── Anthropic ──

async def _anthropic_chat(key: str, system: str, user_message: str, max_tokens: int, timeout: float) -> str:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user_message}],
            },
        )
        if response.status_code != 200:
            body = response.text
            logger.error(f"Anthropic {response.status_code}: {body[:500]}")
            raise httpx.HTTPStatusError(
                f"Anthropic {response.status_code}: {body[:300]}",
                request=response.request,
                response=response,
            )
        data = response.json()
        return data["content"][0]["text"]


async def _anthropic_stream(key: str, system: str, user_message: str, max_tokens: int, timeout: float):
    import json
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            ANTHROPIC_URL,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": max_tokens,
                "stream": True,
                "system": system,
                "messages": [{"role": "user", "content": user_message}],
            },
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise httpx.HTTPStatusError(
                    f"Anthropic {response.status_code}: {body.decode()[:300]}",
                    request=response.request,
                    response=response,
                )
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "content_block_delta":
                    text = event.get("delta", {}).get("text", "")
                    if text:
                        yield text
                elif event.get("type") == "message_stop":
                    break


# ── OpenRouter ──

async def _openrouter_chat(key: str, system: str, user_message: str, max_tokens: int, timeout: float) -> str:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message},
                ],
            },
        )
        if response.status_code != 200:
            body = response.text
            logger.error(f"OpenRouter {response.status_code}: {body[:500]}")
            raise httpx.HTTPStatusError(
                f"OpenRouter {response.status_code}: {body[:300]}",
                request=response.request,
                response=response,
            )
        data = response.json()
        return data["choices"][0]["message"]["content"]


async def _openrouter_stream(key: str, system: str, user_message: str, max_tokens: int, timeout: float):
    import json
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "max_tokens": max_tokens,
                "stream": True,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message},
                ],
            },
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise httpx.HTTPStatusError(
                    f"OpenRouter {response.status_code}: {body.decode()[:300]}",
                    request=response.request,
                    response=response,
                )
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = event.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        yield text
