"""Central, environment-driven settings for the optional integrations.

Every integration is opt-in and degrades gracefully: missing configuration or
offline environments simply fall back to the deterministic local behaviour.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _as_bool(raw_value: str | None, default: bool) -> bool:
    value = (raw_value or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "enabled"}


def testing_mode_enabled() -> bool:
    """True when running under the unit-test suite: forces offline behaviour."""
    return _as_bool(os.getenv("PARCELPILOT_TESTING"), default=False)


def get_qdrant_mode() -> str:
    return (os.getenv("QDRANT_MODE") or "auto").strip().lower()


def get_qdrant_url() -> str | None:
    url = os.getenv("QDRANT_URL")
    return url.strip() if url and url.strip() else None


def get_qdrant_api_key() -> str | None:
    key = os.getenv("QDRANT_API_KEY")
    return key.strip() if key and key.strip() else None


def get_qdrant_collection() -> str:
    return (os.getenv("QDRANT_COLLECTION") or "parcelpilot_docs").strip()


def get_qdrant_timeout_seconds() -> int:
    try:
        return int(os.getenv("QDRANT_TIMEOUT_SECONDS") or "10")
    except ValueError:
        return 10


def qdrant_enabled() -> bool:
    """Decide whether the Qdrant-backed retriever should participate.

    Disabled when USE_QDRANT=false, QDRANT_MODE=disable, or during tests.
    ``cloud`` mode additionally requires a configured URL; ``auto`` and
    ``memory`` always work because they can fall back to an in-process store.
    """
    if not _as_bool(os.getenv("USE_QDRANT"), default=True):
        return False
    if testing_mode_enabled():
        return False
    mode = get_qdrant_mode()
    if mode == "disable":
        return False
    if mode == "cloud":
        return bool(get_qdrant_url())
    return True


def agent_mode() -> str:
    """Which conversational engine handles messages: 'rules' or 'langchain'."""
    mode = (os.getenv("AGENT_MODE") or "rules").strip().lower()
    return mode if mode in {"rules", "langchain"} else "rules"


def retriever_label() -> str:
    """Human-readable description of the active retrieval strategy."""
    if not qdrant_enabled():
        return "keyword"
    mode = get_qdrant_mode()
    if get_qdrant_url() and mode in {"auto", "cloud"}:
        return "qdrant-cloud-hybrid" if mode == "cloud" else "qdrant-hybrid"
    if mode == "cloud":
        return "keyword (QDRANT_MODE=cloud without url)"
    return "qdrant-hybrid"