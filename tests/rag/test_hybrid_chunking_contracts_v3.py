from __future__ import annotations

import pytest


def test_legacy_document_block_keeps_v2_defaults() -> None:
    from backend.app.services.document_parsing.models import (
        DocumentBlock,
        DocumentBlockType,
        DocumentFileType,
        ProcessingMode,
        SourceElementType,
    )

    block = DocumentBlock(
        filename="notes.txt",
        file_type=DocumentFileType.PDF,
        page_number=1,
        block_index=1,
        text="legacy text",
        processing_mode=ProcessingMode.PDF_TEXT,
        source_element=SourceElementType.PDF_TEXT_LAYER,
    )

    assert block.block_type is DocumentBlockType.UNKNOWN
    assert block.heading_level is None
    assert block.bbox is None
    assert block.source_char_start is None
    assert block.source_char_end is None


def test_structured_document_block_validates_structure_fields() -> None:
    from backend.app.services.document_parsing.models import (
        BoundingBox,
        BlockStyleSignals,
        DocumentBlock,
        DocumentBlockType,
        DocumentFileType,
        ProcessingMode,
        SourceElementType,
    )

    block = DocumentBlock(
        filename="lesson.pdf",
        file_type=DocumentFileType.PDF,
        page_number=2,
        block_index=3,
        text="Heading",
        processing_mode=ProcessingMode.PDF_TEXT,
        source_element=SourceElementType.PDF_TEXT_LAYER,
        block_type=DocumentBlockType.HEADING,
        heading_level=2,
        bbox=BoundingBox(x0=1, y0=2, x1=100, y1=20),
        reading_order=4,
        structure_confidence=0.9,
        style_signals=BlockStyleSignals(font_size=18, is_bold=True),
        source_char_start=10,
        source_char_end=17,
    )

    assert block.block_type.value == "heading"
    assert block.bbox.x1 == 100
    assert block.source_char_end - block.source_char_start == 7


def test_hybrid_policy_fingerprint_changes_when_policy_changes() -> None:
    from backend.app.domain.rag.chunking.v3.config import (
        HybridChunkPolicy,
        policy_fingerprint,
    )

    first = HybridChunkPolicy()
    second = HybridChunkPolicy(
        size=first.size.__class__(
            min_tokens=first.size.min_tokens,
            target_tokens=first.size.target_tokens,
            max_tokens=first.size.max_tokens + 1,
        )
    )

    assert policy_fingerprint(first) != policy_fingerprint(second)
    assert len(policy_fingerprint(first)) == 12


def test_feature_flag_defaults_to_v3_and_keeps_v2_opt_out(monkeypatch) -> None:
    from backend.app.domain.rag.chunking.v3.config import (
        ChunkingStrategy,
        chunking_strategy_from_env,
    )
    from backend.app.core.runtime_config import missing_runtime_configuration

    monkeypatch.delenv("FEATURE_HYBRID_CHUNKING_V3", raising=False)
    assert chunking_strategy_from_env() is ChunkingStrategy.HYBRID_V3

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "false")
    assert chunking_strategy_from_env() is ChunkingStrategy.V2

    monkeypatch.setenv("FEATURE_HYBRID_CHUNKING_V3", "true")
    monkeypatch.setenv("HYBRID_CHUNK_MAX_TOKENS", "0")
    assert "HYBRID_CHUNK_MAX_TOKENS must be a positive integer" in missing_runtime_configuration()


def test_execution_config_is_frozen_and_contains_policy_identity() -> None:
    from backend.app.domain.rag.chunking.v3.config import (
        ChunkingExecutionConfig,
        DocumentParsingProfile,
        HybridChunkPolicy,
        TokenizerIdentity,
    )

    config = ChunkingExecutionConfig.from_policy(
        strategy="hybrid_v3",
        parser_profile=DocumentParsingProfile.STRUCTURED_V3,
        policy=HybridChunkPolicy(),
        tokenizer=TokenizerIdentity("cl100k_base"),
    )

    assert config.strategy.value == "hybrid_v3"
    assert config.parser_profile.value == "structured_v3"
    assert config.tokenizer_id == "cl100k_base"
    with pytest.raises(AttributeError):
        config.strategy = "v2"


def test_tiktoken_counter_uses_cl100k_base() -> None:
    from backend.app.services.token_counting import TiktokenTokenCounter

    counter = TiktokenTokenCounter("cl100k_base")

    assert counter.count("中文 mixed text") == len(counter.encoder.encode("中文 mixed text"))
