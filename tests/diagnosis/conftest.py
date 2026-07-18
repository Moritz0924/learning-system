from __future__ import annotations

import pytest

from backend.app.domain.diagnosis.contracts import DiagnosticTemplate


@pytest.fixture
def diagnostic_template() -> DiagnosticTemplate:
    return DiagnosticTemplate.model_validate(
        {
            "template_version": "test_v1",
            "domain": "test",
            "title": "Test diagnostic",
            "self_assessment_dimensions": [
                {
                    "code": "python",
                    "title": "Python",
                    "description": "Python confidence",
                    "related_node_codes": ["python_foundations"],
                },
                {
                    "code": "rag",
                    "title": "RAG",
                    "description": "RAG confidence",
                    "related_node_codes": ["rag_foundations"],
                },
            ],
            "questions": [
                {
                    "question_id": "python-1",
                    "node_code": "python_foundations",
                    "question_type": "single_choice",
                    "prompt": "Which value is immutable?",
                    "options": [
                        {"option_id": "a", "label": "list"},
                        {"option_id": "b", "label": "tuple"},
                    ],
                    "correct_option_id": "b",
                    "weight": 1,
                    "difficulty": 1,
                },
                {
                    "question_id": "python-2",
                    "node_code": "python_foundations",
                    "question_type": "single_choice",
                    "prompt": "Which keyword defines a function?",
                    "options": [
                        {"option_id": "a", "label": "def"},
                        {"option_id": "b", "label": "func"},
                    ],
                    "correct_option_id": "a",
                    "weight": 3,
                    "difficulty": 1,
                },
                {
                    "question_id": "api-1",
                    "node_code": "fastapi_basics",
                    "question_type": "single_choice",
                    "prompt": "Which object defines a FastAPI application?",
                    "options": [
                        {"option_id": "a", "label": "FastAPI"},
                        {"option_id": "b", "label": "Router"},
                    ],
                    "correct_option_id": "a",
                    "weight": 1,
                    "difficulty": 2,
                },
            ],
        }
    )
