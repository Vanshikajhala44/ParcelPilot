from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def get_llm_provider() -> str:
    return (os.getenv("LLM_PROVIDER") or "local").strip().lower()


def get_mistral_api_key() -> str | None:
    key = os.getenv("MISTRAL_API_KEY")
    return key.strip() if key and key.strip() else None


def get_mistral_model() -> str:
    return os.getenv("MISTRAL_MODEL") or "mistral-small-latest"


def get_mistral_embed_model() -> str:
    """Embedding model used by the Qdrant semantic-retrieval integration."""
    return os.getenv("MISTRAL_EMBED_MODEL") or "mistral-embed"


def is_mistral_enabled() -> bool:
    return get_llm_provider() == "mistral" and bool(get_mistral_api_key())


def generate_llm_response(prompt: str, context: str | None = None) -> str | None:
    if not is_mistral_enabled():
        return None

    try:
        try:
            from mistralai import Mistral
        except ImportError:
            # Some installed layouts expose the SDK under mistralai.client.
            from mistralai.client import Mistral
    except ImportError:
        return None

    try:
        client = Mistral(api_key=get_mistral_api_key())
        system_prompt = (
            "You are a careful ParcelPilot support assistant. "
            "Use the supplied context and prefer direct factual answers. "
            "If a source is uncertain or conflicting, say so briefly."
        )
        full_prompt = prompt if context is None else f"Context:\n{context}\n\nQuestion:\n{prompt}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt},
        ]
        try:
            response = client.chat.complete(
                model=get_mistral_model(),
                messages=messages,
                temperature=0.2,
            )
        except AttributeError:
            # Alternative chat surface present in some installed SDK layouts.
            response = client.chat.completions.create(
                model=get_mistral_model(),
                messages=messages,
                temperature=0.2,
            )
        return response.choices[0].message.content
    except Exception:
        return None
