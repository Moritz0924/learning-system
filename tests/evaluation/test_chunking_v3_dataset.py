from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from evals.chunking_v3 import (
    ChunkingDocument,
    validate_template_leakage,
)
from evals.chunking_v3_dataset import build_fixture_bundle, dataset_asset_payloads


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("filename", "mime_type"),
    (
        ("legacy.md", "text/markdown"),
        ("legacy.txt", "text/plain"),
        ("legacy.pdf", "application/pdf"),
        (
            "legacy.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ),
)
def test_variant_a_matches_real_production_legacy_ingestion(
    filename: str,
    mime_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.application.document_service as document_service
    import evals.chunking_v3_runner as runner
    from backend.app.application.document_service import _parse_document_content

    source = """# Legacy ingestion

First paragraph keeps production normalization and routing.

Second paragraph preserves parser metadata for the evaluation baseline.

Third paragraph makes the fixture representative across two pages or slides.
"""
    suffix = Path(filename).suffix.lower()
    content_bytes = (
        source.encode("utf-8")
        if suffix in {".md", ".txt"}
        else runner._materialize_fixture_bytes(suffix, source)[0]
    )
    monkeypatch.setattr(
        runner,
        "_materialize_fixture_bytes",
        lambda requested_suffix, requested_text: (content_bytes, mime_type),
    )
    monkeypatch.setattr(
        document_service,
        "build_embedding_client",
        lambda: pytest.fail("Variant A chunking must not initialize an embedding provider"),
    )
    production = _parse_document_content(
        content_bytes,
        filename=filename,
        mime_type=mime_type,
        document_id="persisted-document-id",
    )

    document = ChunkingDocument(
        document_id="persisted-document-id",
        filename=filename,
        split="development",
        source_type=suffix.lstrip("."),
        source_sha256="a" * 64,
    )
    evaluation = runner.build_variant_index(
        ((document, source),),
        variant="A",
    )

    assert [(chunk.content, chunk.metadata) for chunk in evaluation.chunks] == [
        (chunk["content"], chunk["metadata"])
        for chunk in production.chunks
    ]


def test_variant_a_direct_chunk_call_uses_the_stable_production_default_document_id() -> None:
    from backend.app.application.document_service import _parse_document_content
    from backend.app.domain.rag.chunking.v3.config import HybridChunkPolicy
    from evals.chunking_v3_runner import chunk_document

    source = "# Stable default\n\nProduction derives an unpersisted document identity."
    production = _parse_document_content(
        source.encode("utf-8"),
        filename="stable.md",
        mime_type="text/markdown",
    )
    evaluation = chunk_document(
        source,
        filename="stable.md",
        variant="A",
        policy=HybridChunkPolicy(),
    )

    assert evaluation == [
        (chunk["content"], chunk["metadata"])
        for chunk in production.chunks
    ]


@pytest.mark.parametrize("filename", ("parser-output.txt", "parser-output.md"))
def test_variant_p_preserves_structured_text_parser_output_exactly(filename: str) -> None:
    from backend.app.domain.rag.chunking.v3.config import HybridChunkPolicy
    from backend.app.services.document_parsing.models import DocumentParsingProfile
    from backend.app.services.token_counting import TiktokenTokenCounter
    from evals.chunking_v3_runner import _fixture_blocks, chunk_document

    policy = HybridChunkPolicy()
    counter = TiktokenTokenCounter(policy.tokenizer_id)
    oversized = "LONG " + "长段落证据🙂Unicode " * 1_100 + " END"
    source = (
        "  前导空格🙂\n\n"
        "Parser block keeps café e\u0301 and 汉字.\n"
        "    indented_code = 'Unicode 🚀'\n\n\n"
        + oversized
        + "\n\n尾部 Unicode 文本  \n"
    )
    parser_text = "\n\n".join(
        block.text
        for block in _fixture_blocks(
            filename,
            source,
            profile=DocumentParsingProfile.STRUCTURED_V3,
        )
    )
    assert counter.count(parser_text) > 2_100
    assert "    indented_code = 'Unicode 🚀'" in parser_text
    assert "汉字" in parser_text

    chunks = chunk_document(
        source,
        filename=filename,
        variant="P",
        policy=policy,
    )

    assert len(chunks) > 1
    assert "".join(content for content, _ in chunks) == parser_text
    assert all(counter.count(content) <= policy.size.max_tokens for content, _ in chunks)


@pytest.mark.parametrize("filename", ("parser-output.pdf", "parser-output.pptx"))
def test_variant_p_chunks_structured_binary_parser_output_not_the_scaffold(filename: str) -> None:
    from backend.app.domain.rag.chunking.v3.config import HybridChunkPolicy
    from backend.app.services.document_parsing.models import DocumentParsingProfile
    from backend.app.services.token_counting import TiktokenTokenCounter
    from evals.chunking_v3_runner import _fixture_blocks, chunk_document

    policy = HybridChunkPolicy()
    counter = TiktokenTokenCounter(policy.tokenizer_id)
    source = """# Binary parser heading
First parsed body line.
Second parsed body line.
Second page or slide title.
Third parsed body line.
Fourth parsed body line.
"""
    parser_text = "\n\n".join(
        block.text
        for block in _fixture_blocks(
            filename,
            source,
            profile=DocumentParsingProfile.STRUCTURED_V3,
        )
    )
    assert parser_text != source

    chunks = chunk_document(
        source,
        filename=filename,
        variant="P",
        policy=policy,
    )

    assert "".join(content for content, _ in chunks) == parser_text
    assert all(counter.count(content) <= policy.size.max_tokens for content, _ in chunks)


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


def test_synthetic_template_families_are_shared_and_split_safe() -> None:
    documents = build_fixture_bundle().dataset.documents
    family_counts = Counter(document.template_family for document in documents)
    family_splits: dict[str | None, set[str]] = defaultdict(set)
    for document in documents:
        family_splits[document.template_family].add(document.split)

    assert None not in family_counts
    assert all(count >= 2 for count in family_counts.values())
    assert all(len(splits) == 1 for splits in family_splits.values())


def test_development_and_test_use_structurally_distinct_source_and_query_scaffolds() -> None:
    bundle = build_fixture_bundle()
    development = next(document for document in bundle.dataset.documents if document.split == "development")
    test = next(document for document in bundle.dataset.documents if document.split == "test")
    anchors = {anchor.document_id: [] for anchor in bundle.dataset.anchors}
    for anchor in bundle.dataset.anchors:
        anchors[anchor.document_id].append(anchor)
    development_primary, development_support = anchors[development.document_id]
    test_primary, test_support = anchors[test.document_id]
    development_source = bundle.sources[development.document_id]
    test_source = bundle.sources[test.document_id]

    assert development_source.index(development_primary.normalized_text) < development_source.index(
        development_support.normalized_text
    )
    assert test_source.index(test_support.normalized_text) < test_source.index(
        test_primary.normalized_text
    )
    development_queries = [query.query for query in bundle.dataset.queries if query.document_id == development.document_id]
    test_queries = [query.query for query in bundle.dataset.queries if query.document_id == test.document_id]
    assert all(query.startswith("Calibrate") for query in development_queries)
    assert all(query.startswith("Held-out") for query in test_queries)


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


def test_variant_b_runs_structure_and_size_without_constructing_an_invalid_semantic_policy() -> None:
    from backend.app.domain.rag.chunking.v3.config import HybridChunkPolicy
    from evals.chunking_v3_runner import chunk_document

    diagnostics = {
        "semantic_regions": 0,
        "adaptive_threshold_regions": 0,
        "fixed_threshold_regions": 0,
        "candidate_boundaries": 0,
        "selected_boundaries": 0,
        "relation_adjusted_boundaries": 0,
        "tiny_merges": 0,
        "hard_fallbacks": 0,
    }

    chunks = chunk_document(
        "# Heading\n\nFirst structural unit.\n\nSecond structural unit.",
        filename="variant-b.md",
        variant="B",
        policy=HybridChunkPolicy(),
        diagnostics=diagnostics,
    )

    assert chunks
    assert all(metadata["chunking_strategy"] == "B" for _, metadata in chunks)
    assert diagnostics["candidate_boundaries"] == 0


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
    assert artifact["offline"] is True
    assert artifact["promotion_eligible"] is False


def test_offline_report_jsons_keep_payloads_and_mark_them_non_promotable(tmp_path: Path) -> None:
    output_dir = tmp_path / "reports"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "write-chunking-v3-reports.py"),
            "--isolation-dev",
            str(ROOT / "evals" / "results" / "chunking-v3-ablation-v2-dev.json"),
            "--isolation-test",
            str(ROOT / "evals" / "results" / "chunking-v3-ablation-v2-test.json"),
            "--production-test",
            str(ROOT / "evals" / "results" / "chunking-v3-production-test.json"),
            "--performance",
            str(ROOT / "evals" / "results" / "chunking-v3-performance.json"),
            "--output-dir",
            str(output_dir),
        ],
        cwd=tmp_path,
        check=True,
    )

    expected_payload_keys = {
        "chunking-v3-paired-per-query.json": "per_query",
        "chunking-v3-bootstrap-ci.json": "paired_bootstrap",
        "chunking-v3-bootstrap-ci-report.json": "paired_bootstrap",
    }
    for filename, source_key in expected_payload_keys.items():
        payload = json.loads((output_dir / filename).read_text(encoding="utf-8"))
        assert payload["offline"] is True
        assert payload["promotion_eligible"] is False
        assert payload[source_key]


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
        "production_freeze_sha": "6831ec999567817c6574d9f23786b3c3b964c383",
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
