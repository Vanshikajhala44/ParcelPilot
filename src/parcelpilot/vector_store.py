"""Qdrant-backed vector index over ingested document chunks.

Storage modes (QDRANT_MODE):
  - ``cloud``  : requires QDRANT_URL (e.g. Qdrant Cloud); no fallback allowed.
  - ``memory`` : ephemeral in-process store, zero infrastructure.
  - ``auto``   : use cloud when QDRANT_URL is set, otherwise memory (default).
  - ``disable``: never build a client (keyword-only retrieval).

The collection mirrors chunk payloads produced by ``DocumentIngestion.load_chunks()``
so authority/provenance metadata stays queryable. A fingerprint point stores a
hash of all chunk ids+texts; unchanged corpora are not re-uploaded on restart.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from .embeddings import MistralEmbeddings

logger = logging.getLogger(__name__)


class VectorStoreUnavailable(RuntimeError):
    """Raised when the Qdrant storage cannot be prepared or reached."""


_ID_NAMESPACE = uuid.UUID("6f6f1e46-8c14-5a9a-9d3e-6b0d2b7a1234")


def _chunk_point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"chunk::{chunk_id}"))


def _fingerprint_point_id() -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, "collection-fingerprint"))


def _chunks_fingerprint(chunks: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for chunk in sorted(chunks, key=lambda item: item.get("chunk_id", "")):
        digest.update(chunk.get("chunk_id", "").encode("utf-8"))
        digest.update(b"\x00")
        digest.update(chunk.get("text", "").encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


class VectorStore:
    """Manage and search the Qdrant collection of document chunks."""

    def __init__(
        self,
        chunks_provider: Callable[[], list[dict[str, Any]]],
        project_root: str | Path,
        embeddings: MistralEmbeddings | None = None,
    ):
        self.chunks_provider = chunks_provider
        self.project_root = Path(project_root)
        self.embeddings = embeddings or MistralEmbeddings()

        from . import settings

        self.mode = settings.get_qdrant_mode()
        self.collection = settings.get_qdrant_collection()
        url = settings.get_qdrant_url() if self.mode in {"auto", "cloud"} else None
        self.url = url if settings.qdrant_enabled() else None
        self.api_key = settings.get_qdrant_api_key()
        self.timeout_seconds = settings.get_qdrant_timeout_seconds()
        self.enabled = settings.qdrant_enabled()
        self.last_error: str | None = None

        self._client: Any = None
        self._indexed_chunk_count: int | None = None

    # ------------------------------------------------------------------ setup

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise VectorStoreUnavailable("qdrant-client is not installed") from exc

        try:
            if self.url:
                client = QdrantClient(
                    url=self.url,
                    api_key=self.api_key or None,
                    timeout=self.timeout_seconds,
                )
            else:
                client = QdrantClient(":memory:")
            client.get_collections()  # fail fast on unreachable clusters
        except Exception as exc:
            if self.mode != "auto":
                raise VectorStoreUnavailable(f"Qdrant unavailable: {exc}") from exc
            # auto mode: degrade gracefully instead of losing semantic search
            self.last_error = f"Qdrant at {self.url} unavailable ({exc})"
            logger.warning("%s; falling back to embedded in-memory store", self.last_error)
            self.url = None
            try:
                client = QdrantClient(":memory:")
                client.get_collections()
            except Exception as fallback_exc:
                raise VectorStoreUnavailable(
                    f"Qdrant memory store also unavailable: {fallback_exc}"
                ) from fallback_exc

        self._client = client
        return client

    def _stored_fingerprint(self, client: Any) -> str | None:
        try:
            points = client.retrieve(
                collection_name=self.collection,
                ids=[_fingerprint_point_id()],
                with_payload=True,
            )
            if points:
                payload = points[0].payload or {}
                value = payload.get("value") if isinstance(payload, dict) else None
                return str(value) if value else None
        except Exception:
            return None
        return None

    # ---- index management ---------------------------------------------------

    def ensure_index(self) -> int:
        """Guarantee the collection exists and matches current chunks.

        Returns the number of indexed chunks; raises VectorStoreUnavailable or
        EmbeddingError when prerequisites fail.
        """
        chunks = self.chunks_provider()
        if not chunks:
            return 0
        dimension = self.embeddings.ensure_dimension()
        fingerprint = _chunks_fingerprint(chunks)

        from qdrant_client.models import Distance, PointStruct, VectorParams

        client = self._connect()
        existing_names = {info.name for info in client.get_collections().collections}
        needs_rebuild = True

        if self.collection in existing_names:
            collection_info = client.get_collection(self.collection)
            params_vectors = getattr(getattr(collection_info.config, "params", None), "vectors", None)
            size = getattr(params_vectors, "size", None)
            if size == dimension and self._stored_fingerprint(client) == fingerprint:
                needs_rebuild = False
                logger.debug("Vector index already up to date (%s chunks)", len(chunks))

        if needs_rebuild:
            if self.collection in existing_names:
                client.delete_collection(self.collection)
            client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )

            texts = [chunk["text"] for chunk in chunks]
            vectors = self.embeddings.embed_texts(texts)
            points: list[PointStruct] = [
                PointStruct(
                    id=_chunk_point_id(chunk["chunk_id"]),
                    vector=vectors[index],
                    payload=dict(chunk),
                )
                for index, chunk in enumerate(chunks)
            ]
            points.append(
                PointStruct(
                    id=_fingerprint_point_id(),
                    vector=[0.0] * dimension,
                    payload={"kind": "fingerprint", "value": fingerprint},
                )
            )
            batch_size = 64
            for start in range(0, len(points), batch_size):
                client.upsert(
                    collection_name=self.collection,
                    points=points[start : start + batch_size],
                )
            logger.info(
                "Indexed %s chunks into Qdrant (%s mode)",
                len(chunks),
                "cloud" if self.url else "memory",
            )

        self._indexed_chunk_count = len(chunks)
        return len(chunks)

    # ---- search ---------------------------------------------------------------

    def search(self, query: str, top_k: int = 12) -> list[dict[str, Any]]:
        """Semantic nearest-neighbour search over indexed chunks.

        Returns chunk payload dicts (same shape as ``load_chunks`` output) plus
        a ``_vector_score`` key. Raises VectorStoreUnavailable / EmbeddingError.
        """
        if not self.enabled or not query or not query.strip():
            return []

        client = self._connect()
        self.ensure_index()

        query_vector = self.embeddings.embed_query(query.strip())
        limit = max(top_k, 12)

        if hasattr(client, "query_points"):
            response = client.query_points(
                collection_name=self.collection,
                query=query_vector,
                limit=limit,
                with_payload=True,
            )
            scored_points = response.points
        else:  # pragma: no cover - compatibility with older qdrant-client
            scored_points = client.search(
                collection_name=self.collection,
                query_vector=query_vector,
                limit=limit,
                with_payload=True,
            )

        hits: list[dict[str, Any]] = []
        for point in scored_points:
            payload = dict(point.payload or {})
            if payload.get("kind") == "fingerprint":
                continue
            score = getattr(point, "score", None)
            if score is None and isinstance(point, dict):
                score = point.get("score")
            payload["_vector_score"] = float(score or 0.0)
            hits.append(payload)
        return hits