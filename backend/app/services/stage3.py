from __future__ import annotations

# Compatibility facade for older imports. Runtime code imports the split modules directly.
from backend.app.application.assessment_service import create_assessment, create_phase_assessment, submit_assessment
from backend.app.application.document_service import create_document_record, list_document_records, process_document_upload, process_document_upload_event
from backend.app.application.learning_activity_service import load_learning_activity_summary, load_plan_adjustment
from backend.app.application.learning_service import complete_task, start_task
from backend.app.application.planning_service import apply_plan_adjustment, request_replan
from backend.app.application.tutor_service import answer_tutor_question
from backend.app.core.exceptions import DocumentProcessingUnavailable, PlanApplicationConflict
from backend.app.infrastructure.persistence.repositories.assessment_repository import SQLAlchemyAssessmentRepository
from backend.app.infrastructure.persistence.repositories.audit_repository import SQLAlchemyAuditSink
from backend.app.infrastructure.persistence.repositories.plan_repository import SQLAlchemyPlanRepository
from backend.app.infrastructure.persistence.repositories.rag_repository import SQLAlchemyRagRepository
from backend.app.infrastructure.persistence.repositories.state_repository import SQLAlchemyStateRepository
from backend.app.services.embeddings import DeterministicEmbeddingClient

__all__ = [
    'DocumentProcessingUnavailable',
    'PlanApplicationConflict',
    'SQLAlchemyAssessmentRepository',
    'SQLAlchemyAuditSink',
    'SQLAlchemyPlanRepository',
    'SQLAlchemyRagRepository',
    'SQLAlchemyStateRepository',
    'DeterministicEmbeddingClient',
    'answer_tutor_question',
    'apply_plan_adjustment',
    'complete_task',
    'create_assessment',
    'create_document_record',
    'create_phase_assessment',
    'list_document_records',
    'load_learning_activity_summary',
    'load_plan_adjustment',
    'process_document_upload_event',
    'process_document_upload',
    'request_replan',
    'start_task',
    'submit_assessment',
]
