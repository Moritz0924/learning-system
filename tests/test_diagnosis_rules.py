from backend.app.services.curriculum import ensure_curriculum_seeded
from backend.app.services.diagnosis import build_baseline_diagnosis


def test_beginner_diagnosis_starts_at_python_foundations(db_session):
    curriculum = ensure_curriculum_seeded(db_session)

    result = build_baseline_diagnosis(
        db_session,
        curriculum_id=curriculum.id,
        self_assessment={
            "python_level": 1,
            "api_level": 0,
            "llm_level": 0,
            "rag_level": 0,
            "langgraph_level": 0,
        },
        submitted_answers={
            "questions": [
                {"node_code": "python_foundations", "is_correct": False},
                {"node_code": "fastapi_basics", "is_correct": False},
            ]
        },
    )

    assert result.entry_node_code == "python_foundations"
    assert "python_foundations" in [gap["node_code"] for gap in result.knowledge_gaps]
    assert result.initial_mastery["python_foundations"]["score"] < 70
    assert result.evidence_json["rule_version"] == "stage1-diagnosis-v1"


def test_intermediate_diagnosis_skips_mastered_prerequisites(db_session):
    curriculum = ensure_curriculum_seeded(db_session)

    result = build_baseline_diagnosis(
        db_session,
        curriculum_id=curriculum.id,
        self_assessment={
            "python_level": 5,
            "api_level": 4,
            "llm_level": 4,
            "rag_level": 1,
            "langgraph_level": 0,
        },
        submitted_answers={
            "questions": [
                {"node_code": "python_foundations", "is_correct": True},
                {"node_code": "fastapi_basics", "is_correct": True},
                {"node_code": "llm_api_basics", "is_correct": True},
                {"node_code": "rag_foundations", "is_correct": False},
            ]
        },
    )

    assert result.entry_node_code == "rag_foundations"
    assert result.initial_mastery["python_foundations"]["score"] >= 70
    assert result.initial_mastery["fastapi_basics"]["score"] >= 70
    assert result.initial_mastery["rag_foundations"]["score"] < 70
