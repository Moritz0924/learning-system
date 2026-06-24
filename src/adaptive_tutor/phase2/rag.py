from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from math import sqrt
from uuid import uuid4

from .ports import EmbeddingClient
from .schemas import RetrievedChunk


@dataclass
class DocumentRecord:
    document_id: str
    owner_user_id: str | None
    corpus_type: str
    filename: str
    mime_type: str
    parse_status: str
    sha256: str
    source_url: str | None
    trusted_level: int


@dataclass
class StoredChunk:
    chunk_id: str
    document_id: str
    content: str
    embedding: list[float]
    citation_label: str
    trusted_level: int
    metadata: dict
    source_title: str | None = None
    source_url: str | None = None


@dataclass
class InMemoryRagRepository:
    embedding_client: EmbeddingClient
    documents: dict[str, DocumentRecord] = field(default_factory=dict)
    chunks: list[StoredChunk] = field(default_factory=list)

    def add_document(
        self,
        *,
        filename: str,
        content: str,
        corpus_type: str,
        owner_user_id: str | None = None,
        trusted_level: int = 3,
        source_url: str | None = None,
        source_type: str = "markdown",
    ) -> DocumentRecord:
        document = DocumentRecord(
            document_id=f"doc-{uuid4()}",
            owner_user_id=owner_user_id,
            corpus_type=corpus_type,
            filename=filename,
            mime_type="text/markdown" if source_type == "markdown" else "text/plain",
            parse_status="success",
            sha256=sha256(content.encode("utf-8")).hexdigest(),
            source_url=source_url,
            trusted_level=trusted_level,
        )
        self.documents[document.document_id] = document
        for index, chunk_content in enumerate(split_text(content), start=1):
            self.chunks.append(
                StoredChunk(
                    chunk_id=f"chunk-{uuid4()}",
                    document_id=document.document_id,
                    content=chunk_content,
                    embedding=self.embedding_client.embed(chunk_content),
                    citation_label=f"{filename} chunk {index}",
                    trusted_level=trusted_level,
                    metadata={
                        "chunk_index": index,
                        "source_type": source_type,
                        "untrusted_input": corpus_type != "curated",
                    },
                    source_title=filename,
                    source_url=source_url,
                )
            )
        return document

    def retrieve(self, query: str, *, top_k: int = 5, user_id: str | None = None) -> list[RetrievedChunk]:
        visible_chunks = [
            chunk
            for chunk in self.chunks
            if self._is_visible(chunk.document_id, user_id=user_id)
        ]
        query_embedding = self.embedding_client.embed(query)
        ranked = sorted(
            visible_chunks,
            key=lambda chunk: cosine_similarity(query_embedding, chunk.embedding),
            reverse=True,
        )
        return [
            RetrievedChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                content=chunk.content,
                citation_label=chunk.citation_label,
                source_title=chunk.source_title,
                source_url=chunk.source_url,
                trusted_level=chunk.trusted_level,
                metadata=chunk.metadata,
            )
            for chunk in ranked[:top_k]
        ]

    def _is_visible(self, document_id: str, *, user_id: str | None) -> bool:
        document = self.documents.get(document_id)
        if document is None or document.parse_status != "success":
            return False
        if document.corpus_type == "curated":
            return True
        return bool(user_id and document.owner_user_id == user_id)


def ingest_markdown_document(
    repository: InMemoryRagRepository,
    *,
    filename: str,
    content: str,
    corpus_type: str,
    owner_user_id: str | None = None,
    trusted_level: int = 3,
    source_url: str | None = None,
    source_type: str = "markdown",
) -> DocumentRecord:
    normalized = "\n".join(
        line.strip("# ").strip()
        for line in content.splitlines()
        if line.strip()
    )
    return repository.add_document(
        filename=filename,
        content=normalized,
        corpus_type=corpus_type,
        owner_user_id=owner_user_id,
        trusted_level=trusted_level,
        source_url=source_url,
        source_type=source_type,
    )


def split_text(content: str, *, max_chars: int = 500) -> list[str]:
    words = content.split()
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for word in words:
        if current and current_size + len(word) + 1 > max_chars:
            chunks.append(" ".join(current))
            current = []
            current_size = 0
        current.append(word)
        current_size += len(word) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks or [content]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sqrt(sum(a * a for a in left)) or 1.0
    right_norm = sqrt(sum(b * b for b in right)) or 1.0
    return dot / (left_norm * right_norm)
