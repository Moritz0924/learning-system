from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.domain.rag.chunking.v3.config import (
    ChunkingExecutionSnapshot,
    HybridChunkPolicy,
    TokenizerIdentity,
)
from backend.app.services.embeddings import build_embedding_client
from evals.chunking_v3 import (
    ChunkingQuery,
    canonical_dataset_hash,
    canonical_gold_hash,
    paired_bootstrap,
)
from evals.chunking_v3_dataset import build_fixture_bundle
from evals.chunking_v3_runner import build_variant_index, evaluate_query
from evals.runner.chunking_v3_provider import (
    PHASE1_TOP_N,
    PRODUCTION_FREEZE_SHA,
    assert_no_candidate_is_active,
    evaluate_provider_query,
    require_provider_backed_isolation,
    seed_provider_variant_index,
)


VARIANTS = ("A", "P", "B", "C", "D", "E")
METRIC = "evidence_ndcg"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the leakage-safe Hybrid Chunking V3 ablation.")
    parser.add_argument("--phase", choices=("isolation", "production"), default="isolation")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--dataset", default="chunking-v3-ablation-v2")
    parser.add_argument("--split", choices=("development", "test"))
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--baseline", default="A")
    parser.add_argument("--candidate", default="E")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.dataset != "chunking-v3-ablation-v2":
        parser.error("only the checked-in chunking-v3-ablation-v2 fixture is promotion-candidate data; chunking-v3-v1 is smoke-only")
    if not args.offline and not args.allow_remote:
        parser.error("remote evaluation requires --allow-remote")
    if args.phase == "production" and args.candidate not in VARIANTS:
        parser.error("candidate must be one of A/P/B/C/D/E")

    bundle = build_fixture_bundle()
    split = args.split or ("development" if args.phase == "isolation" else "test")
    documents = tuple(document for document in bundle.dataset.documents if document.split == split)
    queries = tuple(query for query in bundle.dataset.queries if query.split == split)
    source_documents = tuple((document, bundle.sources[document.document_id]) for document in documents)

    variants = (args.baseline, args.candidate) if args.phase == "production" else tuple(args.variants)
    threshold = None
    calibration = None
    if "D" in variants:
        if split == "development":
            threshold, calibration = _calibrate_threshold(bundle, source_documents)
        else:
            calibration = _load_calibration()
            threshold = float(calibration["threshold"])

    if args.offline:
        indexes = {
            variant: build_variant_index(
                source_documents,
                variant=variant,
                fixed_threshold=threshold if variant == "D" else None,
            )
            for variant in variants
        }
        per_query = {
            query.query_id: {
                variant: evaluate_query(indexes[variant], query, anchors=bundle.dataset.anchors)
                for variant in variants
            }
            for query in queries
        }
        provider_metadata = None
    else:
        config, database_url = require_provider_backed_isolation(allow_remote=args.allow_remote)
        engine = create_engine(database_url, pool_pre_ping=True)
        session = Session(engine)
        embedding_client = build_embedding_client()
        try:
            indexes = {
                variant: seed_provider_variant_index(
                    session,
                    documents=source_documents,
                    variant=variant,
                    embedding_client=embedding_client,
                    fixed_threshold=threshold if variant == "D" else None,
                )
                for variant in variants
            }
            assert_no_candidate_is_active(session, indexes.values())
            per_query = {
                query.query_id: {
                    variant: evaluate_provider_query(
                        session,
                        index=indexes[variant],
                        query=query,
                        anchors=bundle.dataset.anchors,
                        embedding_client=embedding_client,
                        top_n=PHASE1_TOP_N,
                    )
                    for variant in variants
                }
                for query in queries
            }
            provider_metadata = {
                "evaluation_database_url_configured": bool(config.database_url),
                "embedding_provider_identity": getattr(embedding_client, "provider_identity", None),
                "embedding_model": getattr(embedding_client, "model", None),
                "embedding_dimensions": getattr(embedding_client, "dimensions", None),
            }
        finally:
            session.close()
            engine.dispose()
    output = _build_output(
        bundle=bundle,
        split=split,
        phase=args.phase,
        variants=variants,
        indexes=indexes,
        queries=queries,
        per_query=per_query,
        calibration=calibration,
        offline=args.offline,
        provider_metadata=provider_metadata,
    )
    output_path = args.output or Path("evals/results") / f"chunking-v3-{args.phase}-{split}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    report_path = output_path.with_suffix(".md")
    report_path.write_text(_markdown_report(output), encoding="utf-8", newline="\n")
    print(json.dumps({"json": str(output_path), "markdown": str(report_path), "promotion_eligible": output["manifest"]["promotion_eligible"]}, ensure_ascii=False, sort_keys=True))
    return 0


def _calibrate_threshold(bundle, source_documents):
    development_items = tuple(
        document for document in bundle.dataset.documents if document.split == "development"
    )
    development_documents = tuple(
        (document, bundle.sources[document.document_id]) for document in development_items
    )
    development_queries = tuple(query for query in bundle.dataset.queries if query.split == "development")
    development_ids = {document.document_id for document in development_items}
    development_anchors = tuple(
        anchor for anchor in bundle.dataset.anchors if anchor.document_id in development_ids
    )
    development_boundaries = {
        document_id: boundaries
        for document_id, boundaries in bundle.dataset.topic_boundaries.items()
        if document_id in development_ids
    }
    baseline = build_variant_index(development_documents, variant="A")
    baseline_results = {
        query.query_id: evaluate_query(baseline, query, anchors=bundle.dataset.anchors)
        for query in development_queries
    }
    baseline_floor = _mean_metric(baseline_results, "fixed_k", "5", "evidence_recall")
    candidates = [round(value / 100, 2) for value in range(20, 51, 5)]
    scored = []
    for threshold in candidates:
        index = build_variant_index(development_documents, variant="D", fixed_threshold=threshold)
        results = {
            query.query_id: evaluate_query(index, query, anchors=bundle.dataset.anchors)
            for query in development_queries
        }
        recall = _mean_metric(results, "fixed_k", "5", "evidence_recall")
        evidence_ndcg = _mean_metric(results, "fixed_k", "5", "evidence_ndcg")
        scored.append({
            "threshold": threshold,
            "recall_at_5": recall,
            "evidence_ndcg_at_5": evidence_ndcg,
        })
    eligible = [item for item in scored if item["recall_at_5"] >= baseline_floor]
    selected = max(
        eligible or scored,
        key=lambda item: (item["evidence_ndcg_at_5"], -item["threshold"]),
    )
    artifact = {
        "threshold": selected["threshold"],
        "dataset_version": bundle.dataset.dataset_version,
        "dev_dataset_hash": canonical_dataset_hash(development_items, development_queries),
        "dev_gold_hash": canonical_gold_hash(development_anchors, development_boundaries),
        "dev_query_hash": _query_hash(development_queries),
        "calibration_run_id": "chunking-v3-ablation-v2-offline-dev",
        "created_from": "development-only",
        "baseline_recall_floor_at_5": baseline_floor,
        "candidates": scored,
    }
    artifact_path = Path("evals/generated/chunking_v3_d_fixed_threshold.json")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return selected["threshold"], artifact


def _load_calibration() -> dict:
    artifact_path = Path("evals/generated/chunking_v3_d_fixed_threshold.json")
    if not artifact_path.exists():
        raise RuntimeError("D calibration artifact is required for Test; run Dev calibration first")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    required = {"threshold", "dataset_version", "dev_dataset_hash", "dev_gold_hash", "dev_query_hash"}
    if not required.issubset(artifact):
        raise RuntimeError("D calibration artifact is incomplete; rerun Dev calibration")
    if artifact["dataset_version"] != "chunking-v3-ablation-v2":
        raise RuntimeError("D calibration artifact belongs to a different dataset; rerun Dev calibration")
    return artifact


def _build_output(*, bundle, split, phase, variants, indexes, queries, per_query, calibration, offline, provider_metadata):
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False,
    ).stdout.strip()
    index_manifest = {
        variant: {
            "index_identity": _index_identity(index),
            "chunk_count": len(index.chunks) if offline else index.chunk_count,
            "embedding_provider_identity": index.provider_identity if offline else provider_metadata["embedding_provider_identity"],
            "embedding_model": index.model if offline else provider_metadata["embedding_model"],
            "embedding_dimensions": index.dimensions if offline else provider_metadata["embedding_dimensions"],
            "semantic_activation_diagnostics": index.diagnostics,
            **({"index_version_ids": list(index.index_version_ids)} if not offline else {}),
        }
        for variant, index in indexes.items()
    }
    paired = {}
    if len(variants) >= 2:
        baseline = variants[0]
        requested_pairs = (
            ("B", "A"), ("C", "B"), ("E", "C"), ("E", "D"), ("E", "A"),
        )
        for candidate, control in requested_pairs:
            if candidate in variants and control in variants:
                paired[f"{candidate}_minus_{control}"] = _paired_deltas(per_query, control, candidate)
        for candidate in variants[1:]:
            key = f"{candidate}_minus_{baseline}"
            if key not in paired:
                paired[key] = _paired_deltas(per_query, baseline, candidate)
    policy = HybridChunkPolicy()
    snapshot = ChunkingExecutionSnapshot.from_v3_policy(
        policy=policy,
        tokenizer=TokenizerIdentity(policy.tokenizer_id),
    )
    return {
        "manifest": {
            "git_sha": git_sha,
            "dataset_version": bundle.dataset.dataset_version,
            "dataset_hash": bundle.dataset.dataset_hash,
            "gold_hash": bundle.dataset.gold_hash,
            "query_hash": _query_hash(queries),
            "production_freeze_sha": PRODUCTION_FREEZE_SHA,
            "embedding_provider_identity": "deterministic:sha256-v1" if offline else provider_metadata["embedding_provider_identity"],
            "embedding_model": "deterministic-sha256-v1" if offline else provider_metadata["embedding_model"],
            "embedding_dimensions": 1536 if offline else provider_metadata["embedding_dimensions"],
            "tokenizer_id": snapshot.tokenizer_id,
            "tokenizer": snapshot.tokenizer_id,
            "parser_implementation_version": snapshot.parser_implementation_version,
            "chunking_implementation_version": snapshot.chunking_implementation_version,
            "policy_fingerprint": snapshot.policy_fingerprint,
            "variants": list(variants),
            "split": split,
            "phase": phase,
            "retrieval_mode": "vector_only",
            "top_n": PHASE1_TOP_N,
            "fixed_k": [1, 3, 5, 10],
            "fixed_token_budgets": [512, 1024, 2048],
            "offline": offline,
            "promotion_eligible": not offline,
        },
        "index_manifest": index_manifest,
        "fixed_threshold_calibration": calibration,
        "per_query": per_query,
        "paired_bootstrap": paired,
    }


def _paired_deltas(per_query, baseline: str, candidate: str):
    deltas = []
    for result in per_query.values():
        candidate_value = result[candidate]["fixed_k"]["5"][METRIC]
        baseline_value = result[baseline]["fixed_k"]["5"][METRIC]
        deltas.append(candidate_value - baseline_value)
    return paired_bootstrap(deltas, resamples=1000, seed=20260812)


def _mean_metric(results, group: str, cutoff: str, metric: str) -> float:
    values = [result[group][cutoff][metric] for result in results.values()]
    return sum(values) / len(values) if values else 0.0


def _query_hash(queries: Iterable[ChunkingQuery]) -> str:
    import hashlib
    payload = "\n".join(f"{query.query_id}|{query.document_id}|{query.query}|{','.join(query.gold_evidence_anchors)}" for query in queries)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _index_identity(index) -> str:
    import hashlib
    payload = "\n".join(f"{chunk.chunk_id}|{chunk.content}" for chunk in index.chunks)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _markdown_report(output: dict) -> str:
    manifest = output["manifest"]
    lines = [
        "# Hybrid Chunking V3 Ablation",
        "",
        f"- Phase: `{manifest['phase']}`",
        f"- Split: `{manifest['split']}`",
        f"- Variants: `{', '.join(manifest['variants'])}`",
        f"- Retrieval: `{manifest['retrieval_mode']}`, top_n=`{manifest['top_n']}`",
        f"- Promotion eligible: `{manifest['promotion_eligible']}`",
        "",
        "Offline outputs are algorithm and runner checks only; they are not Promotion Evidence.",
        "",
    ]
    if output.get("fixed_threshold_calibration"):
        calibration = output["fixed_threshold_calibration"]
        lines.extend([f"D fixed threshold: `{calibration['threshold']}` (development-only)", ""])
    lines.extend(["## Paired bootstrap", "", "```json", json.dumps(output["paired_bootstrap"], ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
