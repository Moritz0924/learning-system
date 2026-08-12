from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

from backend.app.domain.rag.chunking import (
    DEFAULT_CHUNK_POLICY,
    ChunkDraft,
    ChunkMetadataBuilder,
    ChunkType,
    ChunkerRegistry,
    normalize_chunk_text,
)
from backend.app.domain.rag.chunking.v3.config import (
    ChunkingExecutionConfig,
    ChunkingStrategy,
    DocumentParsingProfile,
    HybridChunkPolicy,
    SemanticChunkPolicy,
    SizeGuardPolicy,
    TokenizerIdentity,
    policy_fingerprint,
    chunking_strategy_from_env,
)
from backend.app.domain.rag.chunking.v3.errors import SemanticEmbeddingUnavailable
from backend.app.domain.rag.chunking.v3.pipeline import HybridChunkingPipeline
from backend.app.domain.rag.chunking.v3.relations import AdjacentRelationChecker
from backend.app.domain.rag.chunking.v3.semantic import SemanticChunker
from backend.app.domain.rag.chunking.v3.size_guard import SizeGuard
from backend.app.domain.rag.chunking.v3.structure import StructureAwareChunker
from backend.app.services.document_parsing.models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentFileType,
    DocumentParseResult,
    ProcessingMode,
    SourceElementType,
)
from backend.app.services.document_parsing.parser import DocumentParser
from backend.app.services.embeddings import EmbeddingUnavailable, build_embedding_client
from backend.app.services.token_counting import TiktokenTokenCounter


@dataclass(frozen=True)
class DocumentChunkingResult:
    chunks: list[dict]
    page_count: int
    block_count: int
    parser_version: str
    strategy: ChunkingStrategy
    execution_config: ChunkingExecutionConfig
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
    def __init__(self, *, execution_config: ChunkingExecutionConfig, embedding_client: object | None = None) -> None:
        self.execution_config = execution_config
        self.strategy = execution_config.strategy
        self.embedding_client = embedding_client or build_embedding_client()

    @classmethod
    def from_environment(cls, *, embedding_client: object | None = None) -> "DocumentChunkingService":
        strategy = chunking_strategy_from_env()
        policy = _policy_from_env()
        config = ChunkingExecutionConfig.from_policy(
            strategy=strategy,
            parser_profile=(
                DocumentParsingProfile.STRUCTURED_V3
                if strategy is ChunkingStrategy.HYBRID_V3
                else DocumentParsingProfile.LEGACY_V2
            ),
            policy=policy,
            tokenizer=TokenizerIdentity(policy.tokenizer_id),
        )
        return cls(execution_config=config, embedding_client=embedding_client)

    @classmethod
    def from_execution_config(cls, config: dict, *, embedding_client: object | None = None) -> "DocumentChunkingService":
        policy = HybridChunkPolicy.from_mapping(config["policy"]) if config.get("policy") else _policy_from_env()
        expected = ChunkingExecutionConfig.from_policy(
            strategy=config["strategy"],
            parser_profile=DocumentParsingProfile(config["parser_profile"]),
            policy=policy,
            tokenizer=TokenizerIdentity(config.get("tokenizer_id", policy.tokenizer_id)),
        )
        if expected.policy_fingerprint != config.get("policy_fingerprint"):
            from backend.app.domain.rag.chunking.v3.errors import HybridChunkingConfigurationError
            raise HybridChunkingConfigurationError("execution snapshot policy fingerprint does not match policy payload")
        return cls(execution_config=expected, embedding_client=embedding_client)

    def chunk_text(self, content: bytes, *, filename: str, mime_type: str, document_id: str) -> DocumentChunkingResult:
        if self.strategy is ChunkingStrategy.V2:
            return self._chunk_text_v2(content, filename=filename, mime_type=mime_type, document_id=document_id)
        text = content.decode("utf-8")
        blocks = _markdown_blocks(text, filename=filename, mime_type=mime_type)
        return self.chunk_parsed_document(
            DocumentParseResult(
                status="success", filename=filename,
                file_type=DocumentFileType.PDF if mime_type == "application/pdf" else DocumentFileType.IMAGE,
                mime_type=mime_type, content_sha256=hashlib.sha256(content).hexdigest(),
                parser_version="document-parser-v4", page_count=1, block_count=len(blocks),
                blocks=blocks, processing_time_ms=0,
            ), document_id=document_id,
        )

    def chunk_document(self, content: bytes, *, filename: str, mime_type: str, document_id: str, ocr_service=None) -> DocumentChunkingResult:
        if self.strategy is ChunkingStrategy.V2:
            raise ValueError("V2 document parsing is owned by the legacy ingestion path")
        parser = DocumentParser(ocr_service=ocr_service)
        result = asyncio.run(parser.parse_document(
            content=content, filename=filename, mime_type=mime_type,
            profile=DocumentParsingProfile.STRUCTURED_V3,
        ))
        return self.chunk_parsed_document(result, document_id=document_id)

    def chunk_parsed_document(self, parsed: DocumentParseResult, *, document_id: str) -> DocumentChunkingResult:
        if self.strategy is ChunkingStrategy.V2:
            raise ValueError("V2 parsed-document chunking is owned by the legacy ingestion path")
        policy = self.execution_config.policy or _policy_from_env()
        pipeline = HybridChunkingPipeline(
            structure_chunker=StructureAwareChunker(),
            semantic_chunker=SemanticChunker(
                encoder=_EncoderAdapter(self.embedding_client),
                relation_checker=AdjacentRelationChecker(),
                policy=policy.semantic,
                batch_size=policy.semantic_batch_size,
            ),
            size_guard=SizeGuard(
                token_counter=TiktokenTokenCounter(policy.tokenizer_id),
                policy=policy.size,
            ),
            policy=policy,
        )
        candidates = pipeline.chunk(parsed.blocks, document_id=document_id)
        chunks = [{"content": candidate.content, "metadata": {
            **dict(candidate.metadata),
            "policy_fingerprint": self.execution_config.policy_fingerprint,
            "tokenizer_id": self.execution_config.tokenizer_id,
        }} for candidate in candidates]
        return DocumentChunkingResult(
            chunks=chunks, page_count=parsed.page_count, block_count=parsed.block_count,
            parser_version=parsed.parser_version, strategy=self.strategy,
            execution_config=self.execution_config, embedding_client=self.embedding_client,
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
            embedding_client=self.embedding_client,
        )


def _policy_from_env() -> HybridChunkPolicy:
    semantic = SemanticChunkPolicy(
        window_size=_positive_env("HYBRID_CHUNK_SEMANTIC_WINDOW", 2),
        min_boundary_samples=_positive_env("HYBRID_CHUNK_MIN_BOUNDARY_SAMPLES", 5),
        mad_multiplier=_float_env("HYBRID_CHUNK_MAD_MULTIPLIER", 1.5),
        max_semantic_units=_positive_env("HYBRID_CHUNK_MAX_SEMANTIC_UNITS", 10000),
    )
    size = SizeGuardPolicy(
        min_tokens=_positive_env("HYBRID_CHUNK_MIN_TOKENS", 120),
        target_tokens=_positive_env("HYBRID_CHUNK_TARGET_TOKENS", 320),
        max_tokens=_positive_env("HYBRID_CHUNK_MAX_TOKENS", 512),
    )
    return HybridChunkPolicy(
        semantic=semantic,
        size=size,
        semantic_batch_size=_positive_env("HYBRID_CHUNK_SEMANTIC_BATCH_SIZE", 64),
        tokenizer_id=os.getenv("HYBRID_CHUNK_TOKEN_ENCODING", "cl100k_base").strip() or "cl100k_base",
    )


def _markdown_blocks(content: str, *, filename: str, mime_type: str) -> list[DocumentBlock]:
    lines = content.splitlines()
    blocks: list[DocumentBlock] = []
    index = 0
    heading_level = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("#"):
            prefix, text = line.split(" ", 1) if " " in line else (line, line.lstrip("#"))
            heading_level = len(prefix)
            blocks.append(_text_block(filename, mime_type, index + 1, text.strip(), DocumentBlockType.HEADING, heading_level))
            index += 1
            continue
        if line.startswith("```"):
            code = [line]
            index += 1
            while index < len(lines):
                code.append(lines[index])
                if lines[index].strip() == "```":
                    index += 1
                    break
                index += 1
            blocks.append(_text_block(filename, mime_type, index, "\n".join(code), DocumentBlockType.CODE))
            continue
        if index + 1 < len(lines) and "|" in line and "---" in lines[index + 1]:
            table = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table.append(lines[index])
                index += 1
            blocks.append(_text_block(filename, mime_type, index, "\n".join(table), DocumentBlockType.TABLE))
            continue
        paragraph = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not lines[index].lstrip().startswith("#"):
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append(_text_block(filename, mime_type, index, "\n".join(paragraph), DocumentBlockType.PARAGRAPH))
    return blocks


def _text_block(filename: str, mime_type: str, index: int, text: str, block_type: DocumentBlockType, heading_level: int | None = None) -> DocumentBlock:
    return DocumentBlock(
        filename=filename,
        file_type=DocumentFileType.PDF if mime_type == "application/pdf" else DocumentFileType.IMAGE,
        page_number=1,
        block_index=index,
        text=text,
        processing_mode=ProcessingMode.PDF_TEXT,
        source_element=SourceElementType.PDF_TEXT_LAYER,
        block_type=block_type,
        heading_level=heading_level,
        reading_order=index,
        structure_confidence=1.0,
    )


def _positive_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
