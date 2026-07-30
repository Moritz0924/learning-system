"""Deterministic Gold Chunk Map generation for the production Chunk V2 policy."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

from backend.app.application.document_index_service import (
    document_index_build_key,
    embedding_client_identity,
)
from backend.app.domain.rag.chunking import (
    DEFAULT_CHUNK_POLICY,
    Chunk,
    ChunkMetadataBuilder,
    ChunkType,
    ChunkerRegistry,
    persisted_chunk_ids,
)
from backend.app.domain.rag.chunking.normalization import normalize_chunk_text
from backend.app.infrastructure.persistence.repositories.document_index_repository import (
    deterministic_index_version_id,
)
from backend.app.services.embeddings import DeterministicEmbeddingClient
from evals.models import (
    CorpusManifest,
    GoldChunkMap,
    GoldChunkMapCase,
    GoldEvidenceGroup,
    LearningQaEvaluationCase,
)
from evals.runner.gold_chunk_map import (
    GoldChunkMappingError,
    compute_corpus_hash,
    gold_chunk_map_json,
)
from evals.runner.hashing import canonical_text_sha256


CHUNKING_V2_VERSION = "chunking-v2"
EVALUATION_V2_CHUNKER_VERSION = "evaluation-corpus-v1:chunking-v2"


def compute_chunking_v2_config_hash() -> str:
    payload = {
        "chunk_schema_version": "v2",
        "chunker_version": CHUNKING_V2_VERSION,
        "policy": asdict(DEFAULT_CHUNK_POLICY),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_corpus_chunks_v2(
    corpus_dir: Path,
    *,
    embedding_client: object | None = None,
) -> tuple[CorpusManifest, dict[str, list[Chunk]]]:
    manifest = _load_manifest(corpus_dir)
    client = embedding_client or DeterministicEmbeddingClient()
    registry = ChunkerRegistry.default()
    metadata_builder = ChunkMetadataBuilder(DEFAULT_CHUNK_POLICY)
    chunks_by_document: dict[str, list[Chunk]] = {}
    for item in manifest.documents:
        source = (corpus_dir / item.filename).read_text(encoding="utf-8")
        chunk_type = (
            ChunkType.MARKDOWN if item.source_type == "markdown" else ChunkType.TEXT
        )
        drafts = registry.chunk(
            chunk_type,
            source,
            metadata={"source_type": item.source_type},
        )
        draft_chunks = metadata_builder.build(
            drafts,
            document_id=item.document_id,
            base_metadata={"chunking_config_hash": compute_chunking_v2_config_hash()},
        )
        index_version_id = evaluation_v2_index_version_id(
            item,
            embedding_client=client,
        )
        provider, model, dimensions = embedding_client_identity(client)
        chunk_ids = persisted_chunk_ids(
            index_version_id=index_version_id,
            content_hashes=[chunk.content_hash for chunk in draft_chunks],
        )
        chunks_by_document[item.document_id] = [
            replace(
                chunk,
                chunk_id=chunk_ids[offset],
                previous_chunk_id=chunk_ids[offset - 1] if offset else None,
                next_chunk_id=(
                    chunk_ids[offset + 1] if offset + 1 < len(chunk_ids) else None
                ),
                metadata={
                    **dict(chunk.metadata),
                    "chunk_id": chunk_ids[offset],
                    "previous_chunk_id": chunk_ids[offset - 1] if offset else None,
                    "next_chunk_id": (
                        chunk_ids[offset + 1]
                        if offset + 1 < len(chunk_ids)
                        else None
                    ),
                    "index_version_id": index_version_id,
                    "embedding_provider": provider,
                    "embedding_model": model,
                    "embedding_dimensions": dimensions,
                },
            )
            for offset, chunk in enumerate(draft_chunks)
        ]
    return manifest, chunks_by_document


def evaluation_v2_index_build_key(
    document,
    *,
    embedding_client: object,
) -> str:
    provider, model, dimensions = embedding_client_identity(embedding_client)
    return document_index_build_key(
        document_sha256=document.sha256,
        chunker_version=EVALUATION_V2_CHUNKER_VERSION,
        embedding_provider=provider,
        embedding_model=model,
        embedding_dimensions=dimensions,
    )


def evaluation_v2_index_version_id(
    document,
    *,
    embedding_client: object,
) -> str:
    provider, _, _ = embedding_client_identity(embedding_client)
    return deterministic_index_version_id(
        document_id=document.document_id,
        build_key=evaluation_v2_index_build_key(
            document,
            embedding_client=embedding_client,
        ),
        embedding_provider=provider,
    )


def build_gold_chunk_map_v2(
    dataset_path: Path,
    *,
    corpus_dir: Path,
    embedding_client: object | None = None,
) -> GoldChunkMap:
    manifest, chunks_by_document = build_corpus_chunks_v2(
        corpus_dir,
        embedding_client=embedding_client,
    )
    cases = _load_cases(dataset_path)
    mapped_cases: dict[str, GoldChunkMapCase] = {}
    for case in cases:
        if not case.is_answerable:
            continue
        groups: list[GoldEvidenceGroup] = []
        for evidence in case.gold_evidence_spans:
            matching = _matching_chunk_ids(
                evidence.text,
                chunks_by_document.get(evidence.document_id, []),
            )
            if not matching:
                raise GoldChunkMappingError(
                    f"{case.case_id}/{evidence.evidence_id}: evidence not found in "
                    "full corpus Chunk V2 chunks"
                )
            groups.append(
                GoldEvidenceGroup(
                    evidence_id=evidence.evidence_id,
                    document_id=evidence.document_id,
                    acceptable_chunk_ids=matching,
                )
            )
        mapped_cases[case.case_id] = GoldChunkMapCase(evidence_groups=groups)
    return GoldChunkMap(
        dataset_version=cases[0].dataset_version,
        corpus_hash=compute_corpus_hash(manifest),
        chunking_config_hash=compute_chunking_v2_config_hash(),
        cases=mapped_cases,
    )


def validate_gold_chunk_map_v2(
    mapping: GoldChunkMap,
    *,
    corpus_dir: Path,
) -> list[str]:
    manifest = _load_manifest(corpus_dir)
    errors: list[str] = []
    if mapping.corpus_hash != compute_corpus_hash(manifest):
        errors.append("corpus_hash mismatch")
    if mapping.chunking_config_hash != compute_chunking_v2_config_hash():
        errors.append("chunking_config_hash mismatch")
    return errors


def gold_chunk_map_v2_json(mapping: GoldChunkMap) -> str:
    return gold_chunk_map_json(mapping)


def _load_manifest(corpus_dir: Path) -> CorpusManifest:
    manifest = CorpusManifest.model_validate_json(
        (corpus_dir / "manifest.json").read_text(encoding="utf-8")
    )
    for item in manifest.documents:
        actual = canonical_text_sha256(corpus_dir / item.filename)
        if actual != item.sha256:
            raise GoldChunkMappingError(f"corpus hash mismatch for {item.document_id}")
    return manifest


def _load_cases(dataset_path: Path) -> list[LearningQaEvaluationCase]:
    cases = [
        LearningQaEvaluationCase.model_validate_json(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cases:
        raise GoldChunkMappingError("evaluation dataset is empty")
    return cases


def _matching_chunk_ids(evidence_text: str, chunks: list[Chunk]) -> set[str]:
    evidence = _normalized_flat(evidence_text)
    return {
        chunk.chunk_id
        for chunk in chunks
        if evidence in _normalized_flat(chunk.content)
    }


def _normalized_flat(value: str) -> str:
    return " ".join(normalize_chunk_text(value).split())
