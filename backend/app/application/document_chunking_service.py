from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping

from backend.app.domain.rag.chunking import (
    DEFAULT_CHUNK_POLICY,
    ChunkDraft,
    ChunkMetadataBuilder,
    ChunkType,
    ChunkerRegistry,
    normalize_chunk_text,
)
from backend.app.domain.rag.chunking.v3.config import (
    ChunkingExecutionSnapshot,
    ChunkingStrategy,
    DocumentParsingProfile,
    HybridChunkPolicy,
    SemanticChunkPolicy,
    SizeGuardPolicy,
    TokenizerIdentity,
    chunking_strategy_from_env,
)
from backend.app.domain.rag.chunking.v3.errors import (
    HybridChunkingSnapshotIncompatible,
    SemanticEmbeddingUnavailable,
    StructuredParsingError,
)
from backend.app.domain.rag.chunking.v3.pipeline import HybridChunkingPipeline
from backend.app.domain.rag.chunking.v3.relations import AdjacentRelationChecker
from backend.app.domain.rag.chunking.v3.renderer import ChunkRenderer
from backend.app.domain.rag.chunking.v3.semantic import SemanticChunker
from backend.app.domain.rag.chunking.v3.size_guard import SizeGuard
from backend.app.domain.rag.chunking.v3.structure import StructureAwareChunker
from backend.app.services.document_parsing.models import (
    DocumentParseResult,
    ParseStatus,
)
from backend.app.services.document_parsing.exceptions import DocumentParsingError
from backend.app.services.document_parsing.parser import DocumentParser
from backend.app.services.document_parsing.text_parser import StructuredTextParser
from backend.app.services.embeddings import EmbeddingUnavailable, build_embedding_client
from backend.app.services.token_counting import TiktokenTokenCounter


@dataclass(frozen=True)
class DocumentChunkingResult:
    chunks: list[dict]
    page_count: int
    block_count: int
    parser_version: str
    strategy: ChunkingStrategy
    execution_config: ChunkingExecutionSnapshot
    embedding_client: object


class _EncoderAdapter:
    def __init__(self, client: object) -> None:
        self.client = client

    def embed_batch(self, texts):
        try:
            return list(self.client.embed_batch(list(texts)))
        except EmbeddingUnavailable as exc:
            raise SemanticEmbeddingUnavailable(str(exc)) from exc


class DocumentChunkingService:
    def __init__(self, *, execution_config: ChunkingExecutionSnapshot, embedding_client: object | None = None) -> None:
        self.execution_config = execution_config
        self.strategy = execution_config.strategy
        self.embedding_client = embedding_client

    @classmethod
    def from_environment(cls, *, embedding_client: object | None = None) -> "DocumentChunkingService":
        return cls(
            execution_config=resolve_chunking_execution_snapshot(),
            embedding_client=embedding_client,
        )

    @classmethod
    def from_execution_snapshot(
        cls,
        snapshot: ChunkingExecutionSnapshot | Mapping[str, object],
        *,
        embedding_client: object | None = None,
    ) -> "DocumentChunkingService":
        try:
            payload = snapshot.to_payload() if isinstance(snapshot, ChunkingExecutionSnapshot) else snapshot
            resolved = ChunkingExecutionSnapshot.from_payload(payload)
        except (TypeError, ValueError) as exc:
            raise HybridChunkingSnapshotIncompatible(str(exc)) from exc
        return cls(execution_config=resolved, embedding_client=embedding_client)

    @classmethod
    def from_execution_config(cls, config: dict, *, embedding_client: object | None = None) -> "DocumentChunkingService":
        return cls.from_execution_snapshot(config, embedding_client=embedding_client)

    def _embedding_client(self) -> object:
        if self.embedding_client is None:
            self.embedding_client = build_embedding_client()
        return self.embedding_client

    def chunk_text(self, content: bytes, *, filename: str, mime_type: str, document_id: str) -> DocumentChunkingResult:
        if self.strategy is ChunkingStrategy.V2:
            return self._chunk_text_v2(content, filename=filename, mime_type=mime_type, document_id=document_id)
        return self.chunk_upload(
            content,
            filename=filename,
            mime_type=mime_type,
            document_id=document_id,
        )

    def chunk_upload(
        self,
        content: bytes,
        *,
        filename: str,
        mime_type: str,
        document_id: str,
        ocr_service=None,
    ) -> DocumentChunkingResult:
        if self.strategy is ChunkingStrategy.V2:
            return self._chunk_text_v2(content, filename=filename, mime_type=mime_type, document_id=document_id)
        try:
            if _is_text_upload(filename=filename, mime_type=mime_type):
                parsed = StructuredTextParser().parse(
                    content=content,
                    filename=filename,
                    mime_type=mime_type,
                )
            else:
                parser = DocumentParser(ocr_service=ocr_service)
                parsed = asyncio.run(parser.parse_document(
                    content=content,
                    filename=filename,
                    mime_type=mime_type,
                    profile=DocumentParsingProfile.STRUCTURED_V3,
                ))
        except (DocumentParsingError, ValueError) as exc:
            raise StructuredParsingError(str(exc)) from exc
        return self.chunk_parsed_document(parsed, document_id=document_id)

    def chunk_document(self, content: bytes, *, filename: str, mime_type: str, document_id: str, ocr_service=None) -> DocumentChunkingResult:
        if self.strategy is ChunkingStrategy.V2:
            raise ValueError("V2 document parsing is owned by the legacy ingestion path")
        return self.chunk_upload(
            content,
            filename=filename,
            mime_type=mime_type,
            document_id=document_id,
            ocr_service=ocr_service,
        )

    def chunk_parsed_document(self, parsed: DocumentParseResult, *, document_id: str) -> DocumentChunkingResult:
        if self.strategy is ChunkingStrategy.V2:
            raise ValueError("V2 parsed-document chunking is owned by the legacy ingestion path")
        policy = self.execution_config.v3_policy
        if policy is None:
            raise HybridChunkingSnapshotIncompatible("V3 execution snapshot has no policy")
        if parsed.status is not ParseStatus.SUCCESS or not parsed.blocks:
            raise StructuredParsingError("structured parser produced no usable blocks")
        embedding_client = self._embedding_client()
        pipeline = HybridChunkingPipeline(
            structure_chunker=StructureAwareChunker(),
            semantic_chunker=SemanticChunker(
                encoder=_EncoderAdapter(embedding_client),
                relation_checker=AdjacentRelationChecker(),
                policy=policy.semantic,
                batch_size=policy.semantic_batch_size,
            ),
            size_guard=SizeGuard(
                token_counter=TiktokenTokenCounter(policy.tokenizer_id),
                policy=policy.size,
                renderer=ChunkRenderer(include_heading_context=policy.include_heading_context),
            ),
            policy=policy,
        )
        result = pipeline.chunk(parsed.blocks, document_id=document_id)
        candidates = result.chunks
        chunks = [{"content": candidate.content, "metadata": {
            **dict(candidate.metadata),
            "policy_fingerprint": self.execution_config.policy_fingerprint,
            "tokenizer_id": self.execution_config.tokenizer_id,
        }} for candidate in candidates]
        return DocumentChunkingResult(
            chunks=chunks, page_count=parsed.page_count, block_count=parsed.block_count,
            parser_version=parsed.parser_version, strategy=self.strategy,
            execution_config=self.execution_config, embedding_client=embedding_client,
        )

    def _chunk_text_v2(self, content: bytes, *, filename: str, mime_type: str, document_id: str) -> DocumentChunkingResult:
        text = normalize_chunk_text(content.decode("utf-8"))
        chunk_type = ChunkType.MARKDOWN if mime_type in {"text/markdown", "application/markdown"} or Path(filename).suffix.lower() in {".md", ".markdown"} else ChunkType.TEXT
        drafts = ChunkerRegistry.default().chunk(chunk_type, text)
        chunks = ChunkMetadataBuilder(DEFAULT_CHUNK_POLICY).build(drafts, document_id=document_id, base_metadata={"source_type": chunk_type.value})
        return DocumentChunkingResult(
            chunks=[{"content": chunk.content, "metadata": dict(chunk.metadata)} for chunk in chunks],
            page_count=1, block_count=len(chunks), parser_version="document-parser-v3",
            strategy=self.strategy, execution_config=self.execution_config,
            embedding_client=self._embedding_client(),
        )


def _policy_from_env(environ: Mapping[str, str] | None = None) -> HybridChunkPolicy:
    semantic = SemanticChunkPolicy(
        window_size=_positive_env("HYBRID_CHUNK_SEMANTIC_WINDOW", 2, environ),
        min_boundary_samples=_positive_env("HYBRID_CHUNK_MIN_BOUNDARY_SAMPLES", 5, environ),
        mad_multiplier=_float_env("HYBRID_CHUNK_MAD_MULTIPLIER", 1.5, environ),
        max_semantic_units=_positive_env("HYBRID_CHUNK_MAX_SEMANTIC_UNITS", 10000, environ),
    )
    size = SizeGuardPolicy(
        min_tokens=_positive_env("HYBRID_CHUNK_MIN_TOKENS", 120, environ),
        target_tokens=_positive_env("HYBRID_CHUNK_TARGET_TOKENS", 320, environ),
        max_tokens=_positive_env("HYBRID_CHUNK_MAX_TOKENS", 512, environ),
    )
    values = environ or os.environ
    return HybridChunkPolicy(
        semantic=semantic,
        size=size,
        semantic_batch_size=_positive_env("HYBRID_CHUNK_SEMANTIC_BATCH_SIZE", 64, environ),
        tokenizer_id=values.get("HYBRID_CHUNK_TOKEN_ENCODING", "cl100k_base").strip() or "cl100k_base",
    )


def resolve_chunking_execution_snapshot(
    *,
    filename: str = "",
    mime_type: str = "",
    environ: Mapping[str, str] | None = None,
) -> ChunkingExecutionSnapshot:
    del filename, mime_type
    strategy = chunking_strategy_from_env(dict(environ) if environ is not None else None)
    if strategy is ChunkingStrategy.V2:
        return ChunkingExecutionSnapshot.v2()
    policy = _policy_from_env(environ)
    return ChunkingExecutionSnapshot.from_v3_policy(
        policy=policy,
        tokenizer=TokenizerIdentity(policy.tokenizer_id),
    )


def _is_text_upload(*, filename: str, mime_type: str) -> bool:
    return mime_type.lower() in {"text/plain", "text/markdown", "application/markdown"} or Path(filename).suffix.lower() in {".txt", ".md", ".markdown"}


def _positive_env(name: str, default: int, environ: Mapping[str, str] | None = None) -> int:
    try:
        value = int((environ or os.environ).get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _float_env(name: str, default: float, environ: Mapping[str, str] | None = None) -> float:
    try:
        value = float((environ or os.environ).get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
