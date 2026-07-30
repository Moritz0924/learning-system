"""Deterministic Gold Chunk Map generation for the production Chunk V2 policy."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from backend.app.domain.rag.chunking import (
    DEFAULT_CHUNK_POLICY,
    Chunk,
    ChunkMetadataBuilder,
    ChunkType,
    ChunkerRegistry,
)
from backend.app.domain.rag.chunking.normalization import normalize_chunk_text
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
) -> tuple[CorpusManifest, dict[str, list[Chunk]]]:
    manifest = _load_manifest(corpus_dir)
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
        chunks_by_document[item.document_id] = metadata_builder.build(
            drafts,
            document_id=item.document_id,
            base_metadata={"chunking_config_hash": compute_chunking_v2_config_hash()},
        )
    return manifest, chunks_by_document


def build_gold_chunk_map_v2(
    dataset_path: Path,
    *,
    corpus_dir: Path,
) -> GoldChunkMap:
    manifest, chunks_by_document = build_corpus_chunks_v2(corpus_dir)
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
