"""
Provider-agnostic LLM client for the Forest Data MCP server.

The MCP server itself is fully deterministic (rule-based routing + parameterized
SQL). This client powers ONLY the optional "narrative analysis" layer that turns
query results into a short business-facing summary.

It speaks the OpenAI Chat Completions schema, so any OpenAI-compatible endpoint
works without code changes — you only set environment variables.

------------------------------------------------------------------------------
Supported setups (pick one; all free / open-source friendly)
------------------------------------------------------------------------------
1. Groq + Llama (recommended for LIVE / deployed, free tier, very fast):
       LLM_BASE_URL=https://api.groq.com/openai/v1/chat/completions
       LLM_MODEL=llama-3.3-70b-versatile
       LLM_API_KEY=<your free Groq key>      # https://console.groq.com

2. Ollama + Llama (recommended for LOCAL dev, zero key, fully offline):
       LLM_BASE_URL=http://localhost:11434/v1/chat/completions
       LLM_MODEL=llama3.1:8b
       # no LLM_API_KEY needed

3. OpenRouter (many free open models behind one key):
       LLM_BASE_URL=https://openrouter.ai/api/v1/chat/completions
       LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
       LLM_API_KEY=<your OpenRouter key>

4. Legacy ClimateGPT (kept for backward compatibility only):
       LLM_BASE_URL=https://erasmus.ai/models/climategpt_8b_test/v1/chat/completions
       LLM_BASIC_USER=ai
       LLM_BASIC_PASSWORD=<password>

If no provider is reachable, the client fails SOFT: it returns an empty string,
and the MCP tools simply omit the narrative section (structured data is always
returned regardless).
"""
import os
import base64
import logging
from typing import List, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration (all overridable via environment variables)
# ---------------------------------------------------------------------------
# Default to Groq + Llama 3.3 70B: free tier, OpenAI-compatible, production-ready.
LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL",
    "https://api.groq.com/openai/v1/chat/completions",
)
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# Optional HTTP basic auth (only used by the legacy ClimateGPT endpoint).
LLM_BASIC_USER = os.getenv("LLM_BASIC_USER", os.getenv("CLIMATEGPT_USER", ""))
LLM_BASIC_PASSWORD = os.getenv("LLM_BASIC_PASSWORD", os.getenv("CLIMATEGPT_PASSWORD", ""))

# Generation params
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "400"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "30.0"))

# ---------------------------------------------------------------------------
# Backward compatibility: if the deployment still sets only CLIMATEGPT_URL,
# honor it so existing configs keep working without edits.
# ---------------------------------------------------------------------------
_legacy_url = os.getenv("CLIMATEGPT_URL")
if _legacy_url and not os.getenv("LLM_BASE_URL"):
    LLM_BASE_URL = _legacy_url
    LLM_MODEL = os.getenv("CLIMATEGPT_MODEL", LLM_MODEL)


def _build_headers() -> Dict[str, str]:
    """Build auth headers based on which credentials are configured."""
    headers = {"Content-Type": "application/json"}

    if LLM_API_KEY:
        # Bearer token: Groq, OpenRouter, OpenAI, Together, most providers.
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    elif LLM_BASIC_USER and LLM_BASIC_PASSWORD:
        # HTTP basic auth: legacy ClimateGPT endpoint.
        auth = base64.b64encode(
            f"{LLM_BASIC_USER}:{LLM_BASIC_PASSWORD}".encode()
        ).decode()
        headers["Authorization"] = f"Basic {auth}"
    # else: no auth (e.g. local Ollama)

    return headers


def is_configured() -> bool:
    """Return True if an LLM endpoint URL is set (a key may still be required)."""
    return bool(LLM_BASE_URL)


def provider_info() -> str:
    """Human-readable summary of the active LLM config (no secrets)."""
    auth = "bearer-key" if LLM_API_KEY else (
        "basic-auth" if (LLM_BASIC_USER and LLM_BASIC_PASSWORD) else "none"
    )
    return f"url={LLM_BASE_URL} model={LLM_MODEL} auth={auth}"


async def chat_completion(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> str:
    """
    Send a chat completion request to the configured OpenAI-compatible endpoint.

    Returns the assistant message content, or an empty string on any failure
    (so callers can degrade gracefully and still return structured data).
    """
    if not LLM_BASE_URL:
        logger.warning("No LLM_BASE_URL configured; skipping narrative analysis.")
        return ""

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens or LLM_MAX_TOKENS,
        "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
    }

    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
            response = await client.post(
                LLM_BASE_URL,
                headers=_build_headers(),
                json=payload,
            )

        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]

        logger.error(
            "LLM call failed: HTTP %s - %s",
            response.status_code,
            response.text[:300],
        )
        return ""

    except Exception as e:  # noqa: BLE001 - fail soft by design
        logger.error("LLM call raised an exception: %s", e)
        return ""
