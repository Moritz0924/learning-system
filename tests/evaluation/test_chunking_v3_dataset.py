from __future__ import annotations

import json
from pathlib import Path

from evals.chunking_v3 import (
    ChunkingDocument,
    validate_template_leakage,
)
from evals.chunking_v3_dataset import build_fixture_bundle, dataset_asset_payloads


ROOT = Path(__file__).resolve().parents[2]


def test_ablation_v2_has_required_split_type_language_query_and_structure_coverage() -> None:
    bundle = build_fixture_bundle()
    dataset = bundle.dataset
    documents = dataset.documents

    assert dataset.dataset_version == "chunking-v3-ablation-v2"
    assert len(documents) == 30
    assert len(dataset.queries) == 120
    assert sum(document.split == "development" for document in documents) == 20
    assert sum(document.split == "test" for document in documents) == 10
    assert {
        (source_type, split): sum(
            document.source_type == source_type and document.split == split
            for document in documents
        )
        for source_type in ("markdown", "pdf", "pptx", "text")
        for split in ("development", "test")
    } == {
        ("markdown", "development"): 7,
        ("markdown", "test"): 3,
        ("pdf", "development"): 7,
        ("pdf", "test"): 3,
        ("pptx", "development"): 3,
        ("pptx", "test"): 2,
        ("text", "development"): 3,
        ("text", "test"): 2,
    }
    assert {document.language for document in documents} == {"zh", "en", "mixed"}
    assert {query.query_type for query in dataset.queries} >= {
        "single_evidence", "multi_evidence", "cross_paragraph", "cross_page",
        "table", "code", "heading_scoped", "distractor", "repeated_evidence",
    }
    assert all(len(bundle.sources[document.document_id].split("\n\n")) >= 10 for document in documents)
    assert "长中文段落" in bundle.sources["ablation-v2-005"]
    assert len(bundle.sources["ablation-v2-005"]) > 2_000
    assert len(bundle.sources["ablation-v2-006"].split("| oversized evidence |", 1)[1]) > 1_000
    assert "```python\n" + "x" * 1000 in bundle.sources["ablation-v2-007"]


def test_ablation_v2_has_no_cross_split_template_leakage() -> None:
    bundle = build_fixture_bundle()

    assert validate_template_leakage(bundle.dataset.documents, bundle.sources) == []


def test_template_leakage_validator_rejects_near_duplicate_cross_split_sources() -> None:
    documents = (
        ChunkingDocument("dev", "dev.md", "development", "markdown", "a" * 64, "en", "family-a"),
        ChunkingDocument("test", "test.md", "test", "markdown", "b" * 64, "en", "family-a"),
    )
    sources = {
        "dev": "Unique prefix retrieval evidence calibration and boundary behavior.",
        "test": "Unique prefix retrieval evidence calibration and boundary behavior.",
    }

    errors = validate_template_leakage(documents, sources)

    assert any("template family" in error for error in errors)
    assert any("fingerprint" in error for error in errors)


def test_checked_in_ablation_v2_manifest_and_gold_match_the_deterministic_builder() -> None:
    manifest, gold = dataset_asset_payloads()

    assert json.loads((ROOT / "evals/datasets/chunking_v3_ablation_v2_manifest.json").read_text(encoding="utf-8")) == manifest
    assert json.loads((ROOT / "evals/datasets/chunking_v3_ablation_v2_gold.json").read_text(encoding="utf-8")) == gold


def test_historical_v1_asset_is_explicitly_smoke_only() -> None:
    legacy = json.loads((ROOT / "evals/datasets/chunking_v3_v1_manifest.json").read_text(encoding="utf-8"))

    assert legacy["classification"] == "synthetic_smoke_benchmark"
    assert legacy["promotion_eligible"] is False


def test_ablation_v2_sources_are_loadable_by_the_current_fixture_parser() -> None:
    from evals.chunking_v3_runner import _fixture_blocks
    from backend.app.services.document_parsing.models import DocumentParsingProfile

    bundle = build_fixture_bundle()
    for document in bundle.dataset.documents:
        blocks = _fixture_blocks(
            document.filename,
            bundle.sources[document.document_id],
            profile=DocumentParsingProfile.STRUCTURED_V3,
        )

        assert blocks, document.document_id


def test_pdf_and_pptx_fixture_materialization_has_real_cross_page_or_slide_sources() -> None:
    from backend.app.services.document_parsing.models import DocumentParsingProfile
    from evals.chunking_v3_runner import _fixture_blocks

    bundle = build_fixture_bundle()
    for document in bundle.dataset.documents:
        if document.source_type not in {"pdf", "pptx"}:
            continue
        blocks = _fixture_blocks(
            document.filename,
            bundle.sources[document.document_id],
            profile=DocumentParsingProfile.STRUCTURED_V3,
        )
        assert {block.page_number for block in blocks} == {1, 2}, document.document_id


def test_variant_index_exposes_semantic_activation_diagnostics() -> None:
    from evals.chunking_v3_runner import build_variant_index

    bundle = build_fixture_bundle()
    document = next(item for item in bundle.dataset.documents if item.document_id == "ablation-v2-004")
    index = build_variant_index(
        ((document, bundle.sources[document.document_id]),),
        variant="D",
        fixed_threshold=0.0,
    )

    assert index.diagnostics["semantic_regions"] > 0
    assert index.diagnostics["candidate_boundaries"] > 0
    assert index.diagnostics["fixed_threshold_regions"] > 0


def test_test_ablation_uses_a_complete_development_only_d_threshold_artifact() -> None:
    import importlib.util

    module_path = ROOT / "scripts/run-chunking-v3-ablation.py"
    spec = importlib.util.spec_from_file_location("chunking_v3_ablation", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bundle = build_fixture_bundle()
    development_documents = tuple(
        (document, bundle.sources[document.document_id])
        for document in bundle.dataset.documents
        if document.split == "development"
    )
    _, artifact = module._calibrate_threshold(bundle, development_documents)
    loaded = module._load_calibration()

    assert loaded == artifact
    assert artifact["dataset_version"] == "chunking-v3-ablation-v2"
    assert artifact["dev_dataset_hash"] != bundle.dataset.dataset_hash
    assert artifact["dev_query_hash"] != module._query_hash(
        tuple(query for query in bundle.dataset.queries if query.split == "test")
    )


def test_provider_production_candidate_must_come_from_a_compatible_formal_dev_manifest(
    tmp_path: Path,
) -> None:
    import importlib.util

    module_path = ROOT / "scripts/run-chunking-v3-ablation.py"
    spec = importlib.util.spec_from_file_location("chunking_v3_ablation_selection", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = build_fixture_bundle()

    class Provider:
        provider_identity = "provider-eval"
        model = "model-eval"
        dimensions = 3

    manifest = {
        "dataset_version": bundle.dataset.dataset_version,
        "dataset_hash": bundle.dataset.dataset_hash,
        "gold_hash": bundle.dataset.gold_hash,
        "query_hash": module._query_hash(
            tuple(query for query in bundle.dataset.queries if query.split == "development")
        ),
        "production_freeze_sha": "76ae7800ae75ca9873f3b28ce4be1eb751433c60",
        "split": "development",
        "phase": "isolation",
        "retrieval_mode": "vector_only",
        "top_n": 20,
        "parser_implementation_version": "document-parser-v4.1",
        "chunking_implementation_version": "hybrid-chunking-v3.1",
        "embedding_provider_identity": "provider-eval",
        "embedding_model": "model-eval",
        "embedding_dimensions": 3,
        "promotion_eligible": True,
        "offline": False,
        "variants": ["A", "C", "E"],
    }
    metrics = {
        "A": {"fixed_k": {"5": {"evidence_ndcg": 0.2, "evidence_recall": 0.5, "context_density": 0.1}}},
        "C": {"fixed_k": {"5": {"evidence_ndcg": 0.6, "evidence_recall": 0.7, "context_density": 0.2}}},
        "E": {"fixed_k": {"5": {"evidence_ndcg": 0.5, "evidence_recall": 0.9, "context_density": 0.3}}},
    }
    path = tmp_path / "formal-dev.json"
    path.write_text(json.dumps({"manifest": manifest, "per_query": {"q-1": metrics}}), encoding="utf-8")

    assert module._select_best_from_dev_result(path, bundle=bundle, embedding_client=Provider()) == "C"
    manifest["offline"] = True
    path.write_text(json.dumps({"manifest": manifest, "per_query": {"q-1": metrics}}), encoding="utf-8")
    import pytest
    with pytest.raises(RuntimeError, match="provider-backed"):
        module._select_best_from_dev_result(path, bundle=bundle, embedding_client=Provider())
