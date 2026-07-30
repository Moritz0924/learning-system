"""Build real Phase2TutorEngine instances wired through evaluation adapters."""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from adaptive_tutor.phase2.assessment import build_assessment_draft
from adaptive_tutor.phase2.engine import Phase2TutorEngine
from adaptive_tutor.phase2.ports import Phase2Dependencies
from backend.app.application.memory_context_service import build_tutor_context
from backend.app.application.memory_gate_service import decide_memory_candidates
from backend.app.infrastructure.persistence.repositories.assessment_repository import SQLAlchemyAssessmentRepository
from backend.app.infrastructure.persistence.repositories.audit_repository import SQLAlchemyAuditSink
from backend.app.infrastructure.persistence.repositories.plan_repository import SQLAlchemyPlanRepository
from backend.app.infrastructure.persistence.repositories.rag_repository import SQLAlchemyRagRepository
from backend.app.infrastructure.persistence.repositories.state_repository import SQLAlchemyStateRepository
from backend.app.services.ocr import build_ocr_client
from evals.adapters.rag_adapter import EvaluationRagAdapter
from evals.models import LearningQaEvaluationCase, PromptVariant
from evals.runner.corpus_seed import EVALUATION_GOAL_ID, EVALUATION_USER_ID
from evals.runner.evaluation_runner import EngineExecutionContext


LlmClientFactory = Callable[[LearningQaEvaluationCase, PromptVariant], object]


class EvaluationEngineFactory:
    def __init__(
        self,
        *,
        session: Session,
        embedding_client: object,
        llm_client_factory: LlmClientFactory,
        retrieval_limit: int,
        generation_context_k: int,
        index_schema: str = "legacy-v1",
        allowed_document_ids: set[str] | None = None,
    ) -> None:
        self.session = session
        self.embedding_client = embedding_client
        self.llm_client_factory = llm_client_factory
        self.retrieval_limit = retrieval_limit
        self.generation_context_k = generation_context_k
        self.index_schema = index_schema
        self.allowed_document_ids = allowed_document_ids

    def build(
        self,
        case: LearningQaEvaluationCase,
        prompt_variant: PromptVariant,
    ) -> EngineExecutionContext:
        repository = SQLAlchemyRagRepository(
            self.session,
            self.embedding_client,
            allowed_document_ids=self.allowed_document_ids,
        )
        rag_adapter = EvaluationRagAdapter(
            repository,
            retrieval_limit=self.retrieval_limit,
            generation_context_k=self.generation_context_k,
            index_schema=self.index_schema,
        )
        llm_client = self.llm_client_factory(case, prompt_variant)
        dependencies = Phase2Dependencies(
            state_repository=SQLAlchemyStateRepository(self.session),
            rag_repository=rag_adapter,
            assessment_repository=SQLAlchemyAssessmentRepository(
                self.session,
                EVALUATION_USER_ID,
                EVALUATION_GOAL_ID,
            ),
            plan_repository=SQLAlchemyPlanRepository(self.session),
            audit_sink=SQLAlchemyAuditSink(self.session),
            llm_client=llm_client,
            embedding_client=self.embedding_client,
            ocr_client=build_ocr_client(),
            assessment_factory=build_assessment_draft,
            tutor_context_factory=build_tutor_context,
            memory_gate=decide_memory_candidates,
        )
        engine = Phase2TutorEngine(dependencies)
        return EngineExecutionContext(
            engine=engine,
            retrieval_trace=lambda: rag_adapter.last_trace,
            llm_trace=lambda: getattr(llm_client, "last_trace", None),
        )
