from __future__ import annotations

from uuid import UUID, uuid4

from backend.app.infrastructure.diagnosis.template_repository import DiagnosticTemplateRepository


def initialize_payload(
    *,
    request_id: UUID | None = None,
    template_version: str = "ai_app_dev_v1",
    all_correct: bool = True,
) -> dict:
    template = DiagnosticTemplateRepository().load(
        domain="ai_app_dev", template_version="ai_app_dev_v1"
    ).template
    knowledge_answers = []
    for question in template.questions:
        if all_correct:
            option_id = question.correct_option_id
        else:
            option_id = next(
                option.option_id
                for option in question.options
                if option.option_id != question.correct_option_id
            )
        knowledge_answers.append(
            {"question_id": question.question_id, "selected_option_id": option_id}
        )
    return {
        "request_id": str(request_id or uuid4()),
        "template_version": template_version,
        "goal": {
            "title": "Build a production AI tutor",
            "target_outcome": "Ship an authenticated AI tutor with reliable retrieval and evaluation.",
            "deadline": "2026-10-31",
            "weekly_hours_target": 8,
            "learning_preferences": {
                "explanation_order": ["analogy", "principle", "engineering"],
                "preferred_session_minutes": 45,
                "code_first": True,
            },
        },
        "self_assessment_answers": [
            {"dimension_code": dimension.code, "level": 2}
            for dimension in template.self_assessment_dimensions
        ],
        "knowledge_answers": knowledge_answers,
    }
