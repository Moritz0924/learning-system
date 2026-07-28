"""Deterministic Gold Evidence to full-corpus Chunk mapping."""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path

from adaptive_tutor.phase2.rag import split_text
from backend.app.application.document_service import _normalize_text
from evals.models import (
    CorpusManifest,
    GoldChunkMap,
    GoldChunkMapCase,
    GoldEvidenceGroup,
    LearningQaEvaluationCase,
)
from evals.runner.hashing import canonical_text_sha256


class GoldChunkMappingError(ValueError):
    pass


@dataclass(frozen=True)
class CorpusChunk:
    chunk_id: str
    document_id: str
    chunk_index: int
    content: str
    start: int
    end: int


def deterministic_chunk_id(document_id: str, chunk_index: int, content: str) -> str:
    payload = f"{document_id}|{chunk_index}|{content}".encode("utf-8")
    return "eval-chunk-" + hashlib.sha256(payload).hexdigest()[:32]


def compute_corpus_hash(manifest: CorpusManifest) -> str:
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_chunking_config_hash(*, max_chars: int) -> str:
    payload = json.dumps(
        {
            "max_chars": max_chars,
            "split_text_sha256": hashlib.sha256(inspect.getsource(split_text).encode("utf-8")).hexdigest(),
            "normalize_text_sha256": hashlib.sha256(inspect.getsource(_normalize_text).encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_manifest(corpus_dir: Path) -> CorpusManifest:
    manifest = CorpusManifest.model_validate_json((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest.documents:
        actual = canonical_text_sha256(corpus_dir / item.filename)
        if actual != item.sha256:
            raise GoldChunkMappingError(f"corpus hash mismatch for {item.document_id}")
    return manifest


def build_corpus_chunks(corpus_dir: Path, *, max_chars: int = 500) -> tuple[CorpusManifest, dict[str, list[CorpusChunk]]]:
    manifest = _load_manifest(corpus_dir)
    chunks_by_document: dict[str, list[CorpusChunk]] = {}
    for item in manifest.documents:
        source = (corpus_dir / item.filename).read_text(encoding="utf-8")
        normalized = _normalize_text(source)
        contents = split_text(normalized, max_chars=max_chars)
        offset = 0
        document_chunks: list[CorpusChunk] = []
        for index, content in enumerate(contents, start=1):
            start = offset
            end = start + len(content)
            document_chunks.append(
                CorpusChunk(
                    chunk_id=deterministic_chunk_id(item.document_id, index, content),
                    document_id=item.document_id,
                    chunk_index=index,
                    content=content,
                    start=start,
                    end=end,
                )
            )
            offset = end + 1
        chunks_by_document[item.document_id] = document_chunks
    return manifest, chunks_by_document


def _normalized_flat(text: str) -> str:
    return " ".join(_normalize_text(text).split())


def _matching_chunk_ids(evidence_text: str, chunks: list[CorpusChunk]) -> set[str]:
    document_text = " ".join(chunk.content for chunk in chunks)
    evidence = _normalized_flat(evidence_text)
    starts: list[int] = []
    cursor = 0
    while True:
        found = document_text.find(evidence, cursor)
        if found < 0:
            break
        starts.append(found)
        cursor = found + 1
    if not starts:
        return set()
    matches: set[str] = set()
    for start in starts:
        end = start + len(evidence)
        matches.update(
            chunk.chunk_id
            for chunk in chunks
            if chunk.start < end and chunk.end > start
        )
    return matches


def _load_cases(dataset_path: Path) -> list[LearningQaEvaluationCase]:
    return [
        LearningQaEvaluationCase.model_validate_json(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_gold_chunk_map(
    dataset_path: Path,
    *,
    corpus_dir: Path,
    max_chars: int = 500,
) -> GoldChunkMap:
    manifest, chunks_by_document = build_corpus_chunks(corpus_dir, max_chars=max_chars)
    cases: dict[str, GoldChunkMapCase] = {}
    for case in _load_cases(dataset_path):
        if not case.is_answerable:
            continue
        groups: list[GoldEvidenceGroup] = []
        for evidence in case.gold_evidence_spans:
            chunks = chunks_by_document.get(evidence.document_id, [])
            matching = _matching_chunk_ids(evidence.text, chunks)
            if not matching:
                raise GoldChunkMappingError(
                    f"{case.case_id}/{evidence.evidence_id}: evidence not found in full corpus chunks"
                )
            groups.append(
                GoldEvidenceGroup(
                    evidence_id=evidence.evidence_id,
                    document_id=evidence.document_id,
                    acceptable_chunk_ids=matching,
                )
            )
        cases[case.case_id] = GoldChunkMapCase(evidence_groups=groups)
    return GoldChunkMap(
        dataset_version=_load_cases(dataset_path)[0].dataset_version,
        corpus_hash=compute_corpus_hash(manifest),
        chunking_config_hash=compute_chunking_config_hash(max_chars=max_chars),
        cases=cases,
    )


def validate_gold_chunk_map(
    mapping: GoldChunkMap,
    *,
    corpus_dir: Path,
    max_chars: int = 500,
) -> list[str]:
    manifest = _load_manifest(corpus_dir)
    errors: list[str] = []
    if mapping.corpus_hash != compute_corpus_hash(manifest):
        errors.append("corpus_hash mismatch")
    if mapping.chunking_config_hash != compute_chunking_config_hash(max_chars=max_chars):
        errors.append("chunking_config_hash mismatch")
    return errors


def gold_chunk_map_json(mapping: GoldChunkMap) -> str:
    """Serialize sets and case keys in a stable order across Python processes."""
    payload = {
        "dataset_version": mapping.dataset_version,
        "corpus_hash": mapping.corpus_hash,
        "chunking_config_hash": mapping.chunking_config_hash,
        "cases": {
            case_id: {
                "evidence_groups": [
                    {
                        "evidence_id": group.evidence_id,
                        "document_id": group.document_id,
                        "acceptable_chunk_ids": sorted(group.acceptable_chunk_ids),
                    }
                    for group in mapping.cases[case_id].evidence_groups
                ]
            }
            for case_id in sorted(mapping.cases)
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
