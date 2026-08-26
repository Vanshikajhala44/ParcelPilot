from __future__ import annotations

from dataclasses import dataclass, asdict
import logging
from pathlib import Path
import re
from typing import Any, Iterable

import fitz

from .vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class DocumentRecord:
    source: str
    title: str
    document_type: str
    status: str
    authority_rank: int
    authority_label: str
    customer_account: str | None = None
    account_id: str | None = None
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


class DocumentIngestion:
    """Small, metadata-aware document ingestion layer for the ParcelPilot assessment."""

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.doc_dir = self.project_root / "doc"
        self.documents: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []
        self.vector_store = VectorStore(
            lambda: self.load_chunks(), project_root=self.project_root
        )

    def _infer_document_type(self, filename: str) -> str:
        name = filename.lower()
        if "agreement" in name:
            return "customer_agreement"
        if "policy" in name:
            return "support_policy"
        if "sop" in name or "cancellation" in name:
            return "sop"
        if "guide" in name or "known" in name or "operations" in name:
            return "product_document"
        return "unknown"

    def _infer_status(self, filename: str, text: str) -> str:
        lower = text.lower()
        if "status: deprecated" in lower or "do not use for current requests" in lower:
            return "deprecated"
        if "status: current" in lower or "effective:" in lower and "current" in filename.lower():
            return "current"
        if "status: active" in lower:
            return "active"
        if "deprecated" in filename.lower():
            return "deprecated"
        if "current" in filename.lower():
            return "current"
        return "unknown"

    def _infer_authority(self, filename: str, text: str) -> tuple[int, str]:
        name = filename.lower()
        if "agreement" in name:
            return 1, "signed_customer_agreement"
        if "deprecated" in name or "status: deprecated" in text.lower():
            return 4, "deprecated_policy"
        if "current" in name and "policy" in name:
            return 2, "current_policy"
        if "sop" in name:
            return 2, "current_sop"
        if "guide" in name or "operations" in name or "known" in name:
            return 3, "current_product_document"
        if "policy" in name:
            return 2, "current_policy"
        return 5, "unknown"

    def _extract_account_id(self, text: str, filename: str) -> str | None:
        patterns = [
            r"Account:\s*([A-Z]{2,}-\d+)",
            r"account_id\s*[:=]\s*([A-Z]{2,}-\d+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).upper()

        if "northstar" in filename.lower():
            return "ACCT-001"
        if "lumenworks" in filename.lower():
            return "ACCT-002"
        return None

    def _extract_customer_account(self, text: str, filename: str) -> str | None:
        if "Northstar Logistics" in text:
            return "Northstar Logistics"
        if "LumenWorks" in text:
            return "LumenWorks"
        if "Beacon Retail" in text:
            return "Beacon Retail"
        if "Axis Labs" in text:
            return "Axis Labs"
        if "northstar" in filename.lower():
            return "Northstar Logistics"
        if "lumenworks" in filename.lower():
            return "LumenWorks"
        return None

    def _extract_pdf_text(self, path: Path) -> tuple[str, dict[str, Any]]:
        with fitz.open(str(path)) as pdf:
            merged_text = "\n".join(page.get_text("text") for page in pdf)
            metadata = pdf.metadata or {}
            return merged_text.strip(), metadata

    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
        if not text.strip():
            return []

        normalized = re.sub(r"\s+", " ", text).strip()
        sentences = re.split(r"(?<=[.!?])\s+", normalized)
        chunks: list[str] = []
        buffer: list[str] = []
        buffer_len = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            potential_len = buffer_len + len(sentence) + (1 if buffer else 0)
            if buffer and potential_len > chunk_size:
                chunk_text = " ".join(buffer).strip()
                if chunk_text:
                    chunks.append(chunk_text)
                if overlap > 0 and buffer:
                    tail = buffer[-max(1, len(buffer) // 2):]
                    buffer = list(tail)
                    buffer_len = sum(len(part) for part in buffer)
                else:
                    buffer = []
                    buffer_len = 0
            buffer.append(sentence)
            buffer_len = sum(len(part) for part in buffer)

        if buffer:
            chunk_text = " ".join(buffer).strip()
            if chunk_text:
                chunks.append(chunk_text)

        return chunks or [normalized[:chunk_size]]

    def load_documents(self) -> list[dict[str, Any]]:
        if not self.doc_dir.exists():
            raise FileNotFoundError(f"Document directory not found: {self.doc_dir}")

        documents: list[dict[str, Any]] = []
        for pdf_path in sorted(self.doc_dir.glob("*.pdf")):
            text, metadata = self._extract_pdf_text(pdf_path)
            doc_type = self._infer_document_type(pdf_path.name)
            status = self._infer_status(pdf_path.name, text)
            authority_rank, authority_label = self._infer_authority(pdf_path.name, text)
            customer_account = self._extract_customer_account(text, pdf_path.name)
            account_id = self._extract_account_id(text, pdf_path.name)

            record = {
                "source": str(pdf_path.relative_to(self.project_root)).replace("\\", "/"),
                "title": metadata.get("title") or pdf_path.stem,
                "document_type": doc_type,
                "doc_type": doc_type,
                "status": status,
                "authority_rank": authority_rank,
                "authority_label": authority_label,
                "customer_account": customer_account,
                "account_id": account_id,
                "text": text,
            }
            documents.append(record)

        self.documents = documents
        return self.documents

    def load_chunks(self, chunk_size: int = 500, overlap: int = 100) -> list[dict[str, Any]]:
        docs = self.load_documents()
        chunks: list[dict[str, Any]] = []

        for doc in docs:
            text = doc["text"]
            doc_chunks = self._chunk_text(text, chunk_size=chunk_size, overlap=overlap)
            for idx, chunk_text in enumerate(doc_chunks, start=1):
                chunks.append(
                    {
                        "chunk_id": f"{doc['source']}::chunk-{idx}",
                        "source": doc["source"],
                        "title": doc["title"],
                        "document_type": doc["document_type"],
                        "doc_type": doc["document_type"],
                        "status": doc["status"],
                        "authority_rank": doc["authority_rank"],
                        "authority_label": doc["authority_label"],
                        "customer_account": doc["customer_account"],
                        "account_id": doc["account_id"],
                        "text": chunk_text,
                    }
                )

        self.chunks = chunks
        return self.chunks

    def search_documents(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Hybrid document search: deterministic keyword scoring merged with
        optional Mistral/Qdrant semantic similarity, re-ranked so that higher
        authority sources win ties (contract > policy > SOP > deprecated)."""
        if not query or not query.strip():
            return []

        q = query.strip().lower()
        chunks = self.load_chunks()

        keyword_scored: dict[str, float] = {}
        chunk_by_id: dict[str, dict[str, Any]] = {}
        for chunk in chunks:
            text = chunk["text"].lower()
            score = 0.0
            for token in re.findall(r"[a-z0-9]+", q):
                if token in text:
                    score += 3.0
            if q in text:
                score += 5.0
            if score > 0:
                keyword_scored[chunk["chunk_id"]] = score
            chunk_by_id[chunk["chunk_id"]] = chunk

        vector_scores: dict[str, float] = {}
        if self.vector_store.enabled:
            try:
                for hit in self.vector_store.search(q, top_k=max(top_k * 3, 12)):
                    hit_id = hit.get("chunk_id")
                    if hit_id:
                        vector_scores[hit_id] = float(hit.get("_vector_score", 0.0))
                        chunk_by_id.setdefault(hit_id, hit)
            except Exception as exc:
                self.vector_store.last_error = str(exc)
                logger.warning("Vector retrieval failed; falling back to keywords: %s", exc)

        candidate_ids = set(keyword_scored) | set(vector_scores)
        max_keyword = max(keyword_scored.values(), default=0.0) or 1.0
        max_vector = max(vector_scores.values(), default=0.0) or 1.0

        ranked: list[tuple[float, float, int, dict[str, Any]]] = []
        for chunk_id in candidate_ids:
            chunk = chunk_by_id.get(chunk_id, {})
            keyword_norm = keyword_scored.get(chunk_id, 0.0) / max_keyword
            vector_norm = vector_scores.get(chunk_id, 0.0) / max_vector
            hybrid_score = (0.65 * vector_norm) + (0.35 * keyword_norm)
            authority_penality = -int(chunk.get("authority_rank") or 99)
            ranked.append((hybrid_score, vector_norm, authority_penality, chunk))

        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)

        results: list[dict[str, Any]] = []
        for _, _, _, chunk in ranked[:top_k]:
            results.append(
                {
                    "source": chunk.get("source", ""),
                    "title": chunk.get("title", ""),
                    "document_type": chunk.get("document_type", ""),
                    "doc_type": chunk.get("document_type", ""),
                    "status": chunk.get("status", ""),
                    "authority_rank": chunk.get("authority_rank", 99),
                    "authority_label": chunk.get("authority_label", ""),
                    "customer_account": chunk.get("customer_account"),
                    "account_id": chunk.get("account_id"),
                    "matched_excerpt": chunk.get("text", ""),
                }
            )

        return results
