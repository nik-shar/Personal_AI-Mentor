"""
orchestrator/llm.py

Single source of truth for LLM access across the entire project.

Three-tier model strategy:
  1. Router    — OpenAI gpt-4o-mini (fast, cheap routing/classification).
  2. Converser — Nebius Qwen/Qwen3-235B-A22B-Instruct-2507 (high-quality direct responses).
  3. Writer    — Nebius MiniMax-M3 (deep content generation, tutorials).

All agents and orchestrator nodes call the appropriate factory function —
nothing in the codebase should import ChatOpenAI or any other LLM client directly.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv(override=True)

# Enable LangSmith tracing before any chat model is built (no-op without creds;
# setup_tracing itself is fail-open and never raises). This makes every LLM call
# in the repo auto-observable.
from orchestrator.tracing import setup_tracing

setup_tracing()


# ---------------------------------------------------------------------------
# Nebius health check — cached per process to avoid repeated round-trips.
# ---------------------------------------------------------------------------

_NEBIUS_HEALTHY: bool | None = None

_NEBIUS_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
_NEBIUS_MODEL    = "Qwen/Qwen3-235B-A22B-Instruct-2507"

_WRITER_BASE_URL = "https://api.tokenfactory.us-central1.nebius.com/v1/"
_WRITER_MODEL    = "MiniMaxAI/MiniMax-M3"
_FALLBACK_MODEL  = "gpt-4o-mini"


def _get_clean_nebius_key() -> str | None:
    """Return the Nebius API key stripped of accidental whitespace/newlines."""
    api_key = os.getenv("NEBIUS_API_KEY")
    if api_key:
        return api_key.replace("\n", "").replace("\r", "").strip()
    return None


def _check_nebius_health() -> bool:
    """
    Probe the Nebius endpoint once per process lifetime.
    Returns True if reachable and the key is valid, False otherwise.
    Caches the result so every get_reasoning_llm() call after the first is free.
    """
    global _NEBIUS_HEALTHY
    if _NEBIUS_HEALTHY is not None:
        return _NEBIUS_HEALTHY

    api_key  = _get_clean_nebius_key()
    base_url = os.getenv("NEBIUS_BASE_URL", _NEBIUS_BASE_URL)

    if not api_key:
        print("[LLM] NEBIUS_API_KEY not set — falling back to OpenAI.")
        _NEBIUS_HEALTHY = False
        return False

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=3.0)
        client.models.list()
        _NEBIUS_HEALTHY = True
        print(f"[LLM] Nebius endpoint healthy — using {_NEBIUS_MODEL}.")
    except Exception as exc:
        print(f"[LLM] Nebius probe failed ({exc}) — falling back to OpenAI {_FALLBACK_MODEL}.")
        _NEBIUS_HEALTHY = False

    return _NEBIUS_HEALTHY


def reset_nebius_health() -> None:
    """Force a fresh health probe on the next get_reasoning_llm() call (useful in tests)."""
    global _NEBIUS_HEALTHY
    _NEBIUS_HEALTHY = None


# ---------------------------------------------------------------------------
# Tier 1: Router — gpt-4o-mini (fast classification/routing)
# ---------------------------------------------------------------------------

def get_reasoning_llm(temperature: float = 0.2) -> ChatOpenAI:
    """
    Return a LangChain ChatOpenAI client configured for fast reasoning/routing tasks.
    Model: gpt-4o-mini
    """
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Cannot use gpt-4o-mini for routing.")
    
    print("[LLM] Fast Router configured — using gpt-4o-mini.")
    return ChatOpenAI(
        model=_FALLBACK_MODEL,
        api_key=openai_key,
        temperature=temperature,
    )


# ---------------------------------------------------------------------------
# Tier 2: Converser — Qwen3-235B-A22B (high-quality direct responses)
# ---------------------------------------------------------------------------

def get_conversational_llm(temperature: float = 0.4) -> ChatOpenAI:
    """
    Return a LangChain ChatOpenAI client for high-quality conversational responses.
    Uses Nebius Qwen/Qwen3-235B-A22B-Instruct-2507 for deep, context-aware mentor responses.
    Falls back to OpenAI gpt-4o-mini if Nebius is unavailable.
    """
    api_key = _get_clean_nebius_key()
    base_url = os.getenv("NEBIUS_BASE_URL", _NEBIUS_BASE_URL)

    if _check_nebius_health() and api_key:
        print(f"[LLM] Conversational model — using {_NEBIUS_MODEL}.")
        return ChatOpenAI(
            model=os.getenv("NEBIUS_MODEL", _NEBIUS_MODEL),
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
        )

    # Fallback: OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise RuntimeError(
            "No LLM backend available: NEBIUS_API_KEY is unreachable and "
            "OPENAI_API_KEY is not set. Set at least one of them in your .env."
        )
    print("[LLM] Conversational model — falling back to gpt-4o-mini.")
    return ChatOpenAI(
        model=_FALLBACK_MODEL,
        api_key=openai_key,
        temperature=temperature,
    )


def get_writer_llm(temperature: float = 0.4) -> ChatOpenAI:
    """
    Return a LangChain ChatOpenAI client configured for content creation / goal decomposition.
    Model: MiniMaxAI/MiniMax-M3 (or WRITER_MODEL)
    Base URL: https://api.tokenfactory.us-central1.nebius.com/v1/
    """
    api_key  = _get_clean_nebius_key()
    base_url = os.getenv("WRITER_BASE_URL", _WRITER_BASE_URL)
    writer_model = os.getenv("WRITER_MODEL", _WRITER_MODEL)

    if _check_nebius_health() and api_key:
        print(f"[LLM] Writer Nebius endpoint healthy — using {writer_model} @ {base_url}")
        return ChatOpenAI(
            model=writer_model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
        )

    # Fallback: OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise RuntimeError(
            "No LLM backend available: NEBIUS_API_KEY is unreachable and "
            "OPENAI_API_KEY is not set. Set at least one of them in your .env."
        )
    return ChatOpenAI(
        model=_FALLBACK_MODEL,
        api_key=openai_key,
        temperature=temperature,
    )


def get_deep_reasoning_llm(temperature: float = 0.2) -> ChatOpenAI:
    """
    Return high-capacity Nebius OpenAI-compatible LLM client (MiniMaxAI/MiniMax-M3)
    for multi-step deep tutorial generation and complex code synthesis.
    Base URL: https://api.tokenfactory.us-central1.nebius.com/v1/
    """
    return get_writer_llm(temperature=temperature)


def get_reflection_llm(temperature: float = 0.1) -> ChatOpenAI:
    """
    Tier for DNA memory writes — reflection, compare/merge dedup, and the
    weekly/monthly consolidation summaries.

    During the DNA base-building phase the mentor's understanding is only as
    good as the model that writes it, so this defaults to the STRONG
    conversational tier (Qwen3-235B via Nebius). Switch to a cheaper tier later
    with DNA_REFLECTION_MODEL=reasoning (gpt-4o-mini) or =writer (MiniMax-M3).
    """
    tier = os.getenv("DNA_REFLECTION_MODEL", "conversational").strip().lower()
    if tier == "writer":
        return get_writer_llm(temperature=temperature)
    if tier == "reasoning":
        return get_reasoning_llm(temperature=temperature)
    # Default: the strong conversational tier (Qwen3-235B).
    return get_conversational_llm(temperature=temperature)
