from __future__ import annotations

import json

from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.mocks import build_mock_phase2_dependencies
from adaptive_tutor.phase2.rag import ingest_markdown_document
from adaptive_tutor.phase2.schemas import TutorRunRequest
from adaptive_tutor.tutor.t3_contracts import GroundingStatus
from backend.app.application.serialization import _run_result_to_dict


class StructuredTeacher:
    def complete(self, *, role, prompt, tutor_context=None, conversation_context=None, context=None, **kwargs):
        chunk = (context or [])[0]
        return json.dumps(
            {
                "answer": "RAG retrieves evidence.",
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "text": "RAG retrieves evidence.",
                        "citation_refs": [{"chunk_id": chunk.chunk_id, "document_id": chunk.document_id}],
                    }
                ],
                "citations": [{"chunk_id": chunk.chunk_id, "document_id": chunk.document_id}],
            }
        )


def test_structured_teacher_returns_only_validated_public_citations(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_STRUCTURED_ANSWER_V2", "true")
    monkeypatch.setenv("FEATURE_GROUNDING_V2", "true")
    dependencies = build_mock_phase2_dependencies()
    dependencies.llm_client = StructuredTeacher()
    ingest_markdown_document(
        dependencies.rag_repository,
        filename="course.md",
        content="# RAG\nRAG retrieves evidence before generation.",
        corpus_type="curated",
    )

    result = Phase2TutorEngine(dependencies).run(
        TutorRunRequest(
            trigger_type="chat",
            user_id="user-1",
            goal_id="goal-1",
            thread_id="t3-structured-thread",
            user_message="How does RAG work?",
        )
    )

    assert result.grounding_status == GroundingStatus.SEMANTIC_UNVERIFIED.value
    assert len(result.public_citations) == 1
    assert len(result.citations) == 1
    assert result.public_citations[0].citation_id == "c1"
    serialized = _run_result_to_dict(result)
    assert serialized["grounding_status"] == GroundingStatus.SEMANTIC_UNVERIFIED.value
    assert serialized["public_citations"][0]["citation_id"] == "c1"
    assert "chunk_id" not in serialized["citations"][0]
