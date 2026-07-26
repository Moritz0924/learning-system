from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db import Base, enable_sqlite_foreign_keys
from backend.app.services.embeddings import DeterministicEmbeddingClient
from evals.adapters.mock_clients import MockJsonLlmClient
from evals.models import PromptVariant
from evals.runner.corpus_seed import seed_evaluation_corpus
from evals.runner.dataset_loader import load_and_validate_dataset
from evals.runner.gold_chunk_map import build_gold_chunk_map


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evals" / "corpus" / "learning_qa_v1"
DATASET = ROOT / "evals" / "datasets" / "learning_qa_v1.jsonl"


def test_real_phase2_engine_runs_through_timed_rag_and_mock_llm_adapters() -> None:
    from evals.runner.engine_factory import EvaluationEngineFactory
    from evals.runner.evaluation_runner import EvaluationRunner

    engine = create_engine("sqlite+pysqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    session = Session(engine)
    embedding = DeterministicEmbeddingClient()
    seed_evaluation_corpus(session, corpus_dir=CORPUS, embedding_client=embedding, reset=False)
    dataset = load_and_validate_dataset(DATASET, corpus_dir=CORPUS).cases
    case = next(item for item in dataset if item.case_id == "rag-direct-001")
    mapping = build_gold_chunk_map(DATASET, corpus_dir=CORPUS)
    prompt = PromptVariant(
        name="candidate",
        content="candidate",
        sha256=hashlib.sha256(b"candidate").hexdigest(),
    )
    factory = EvaluationEngineFactory(
        session=session,
        embedding_client=embedding,
        llm_client_factory=lambda case, prompt: MockJsonLlmClient(scenario="valid"),
        retrieval_limit=5,
        generation_context_k=5,
    )
    runner = EvaluationRunner(
        engine_builder=factory.build,
        gold_map=mapping,
        corpus_document_ids={"eval-doc-rag-001", "eval-doc-rag-002", "eval-doc-rag-003", "eval-doc-rag-004", "eval-doc-rag-005"},
        metric_cutoffs=[1, 3, 5],
        retrieval_config_hash="r" * 64,
        run_mode="mock_smoke",
    )

    result = runner.run_case(case, prompt_variant=prompt, repeat_index=0)

    assert result.execution.status == "completed"
    assert result.execution.degraded_mode_used is False
    assert result.retrieval.retrieved_chunk_ids
    assert result.answer.format_followed is True
    assert result.answer.citation_reference_validity_rate == 1.0
