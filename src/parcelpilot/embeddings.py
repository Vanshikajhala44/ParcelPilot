"""Embedding provider for semantic retrieval.

Uses the Mistral embeddings API (``mistral-embed``) so no extra model downloads
or local inference dependencies are required. Follows the project convention of
optional integrations: callers must handle :class:`EmbeddingError` and fall
back to keyword-only retrieval when Mistral is unconfigured or unreachable.
"""
from __future__ import annotations

import math
import time
from typing import Any


class EmbeddingError(RuntimeError):
    """Raised when an embedding provider cannot produce vectors."""


class MistralEmbeddings:
    """Thin, fault-tolerant embedding client backed by ``mistral-embed``."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        from .llm_config import get_mistral_api_key, get_mistral_embed_model

        self.api_key = api_key or get_mistral_api_key()
        self.model = model or get_mistral_embed_model()
        self._client: Any = None
        self._dimension: int | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def dimension(self) -> int | None:
        return self._dimension

    def _get_client(self) -> Any:
        if self._client is None:
            mistral_cls = None
            last_error: Exception | None = None
            try:
                from mistralai import Mistral as mistral_cls  # type: ignore[no-redef]
            except ImportError as exc:
                last_error = exc
            if mistral_cls is None:
                try:
                    # Some installed layouts expose the SDK under mistralai.client.
                    from mistralai.client import Mistral as mistral_cls  # type: ignore[no-redef]
                except ImportError as exc:
                    last_error = exc
            if mistral_cls is None:  # pragma: no cover - depends on env
                raise EmbeddingError(f"mistralai package is not importable: {last_error}")
            self._client = mistral_cls(api_key=self.api_key)
        return self._client

    def ensure_dimension(self) -> int:
        """Make sure we know the embedding width by probing once."""
        if self._dimension is None:
            self.embed_query("__parcel_pilot_dimension_probe__")
        assert self._dimension is not None
        return self._dimension

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def _request_embeddings(self, client: Any, texts: list[str]) -> Any:
        """Call the embeddings endpoint tolerating SDK signature variations."""
        rejection_notes: list[str] = []
        for payload_key in ("input", "inputs"):
            try:
                return client.embeddings.create(
                    model=self.model, **{payload_key: texts}
                )
            except TypeError as exc:
                # Wrong kwarg name for this installed SDK version: try next.
                rejection_notes.append(f"'{payload_key}' rejected ({exc})")
        raise TypeError(
            "embeddings.create rejected all known parameter names: "
            + " | ".join(rejection_notes)
        )

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts with retry/backoff; vectors are normalized."""
        if not texts:
            return []
        if not self.available:
            raise EmbeddingError("No Mistral API key configured for embeddings")

        client = self._get_client()
        response: Any = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._request_embeddings(client, texts)
                break
            except TypeError as exc:
                # Signature mismatch will not heal by retrying.
                raise EmbeddingError(f"Mistral embedding call invalid: {exc}") from exc
            except Exception as exc:  # network / rate-limit errors
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        if response is None:
            raise EmbeddingError(f"Mistral embedding request failed: {last_error}")

        data = getattr(response, "data", None) or []
        vectors: list[list[float]] = []
        for item in data:
            embedding = getattr(item, "embedding", None)
            if embedding is None and isinstance(item, dict):
                embedding = item.get("embedding")
            if embedding is None:
                raise EmbeddingError("Mistral embedding response contained no vector")
            vectors.append(self._normalize([float(value) for value in embedding]))

        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Expected {len(texts)} embeddings from Mistral, received {len(vectors)}"
            )
        self._dimension = len(vectors[0])
        return vectors

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0:
            return vector
        return [value / norm for value in vector]