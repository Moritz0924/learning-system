from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from backend.app import models
from backend.app.application.onboarding_service import OnboardingService, _GeneratedDiagnostic
from backend.app.application.planning_service import apply_plan_adjustment
from backend.app.models import (
    BaselineDiagnostic,
    Curriculum,
    KnowledgeNode,
    LearningGoal,
    LearningPlan,
    LearningStateSnapshot,
    LearningSession,
    MasteryRecord,
    PlanAdjustmentRecord,
    PlanTask,
)
from backend.app.application.config_service import RuntimeResolutionError
from backend.app.services.llm_gateway import EvaluationProviderError
from tests.conftest import register_user
from tests.diagnosis.helpers import initialize_payload


def _goal(topic: str = "Byzantine mosaics") -> dict:
    return {
        "title": f"Master {topic}",
        "target_outcome": f"Create and explain a complete portfolio about {topic}.",
        "deadline": (date.today() + timedelta(days=120)).isoformat(),
        "weekly_hours_target": 8,
        "learning_preferences": {
            "explanation_order": ["analogy", "principle"],
            "preferred_session_minutes": 45,
            "code_first": False,
        },
    }


def _draft_payload(*, request_id: str | None = None, topic: str = "Byzantine mosaics") -> dict:
    return {
        "request_id": request_id or str(uuid4()),
        "locale": "en-US",
        "goal": _goal(topic),
    }


def _diagnostic_json(topic: str = "Byzantine mosaics") -> str:
    return json.dumps(
        {
            "title": f"{topic} readiness check",
            "questions": [
                {
                    "question_id": f"q-{number}",
                    "skill_id": f"skill-{number}",
                    "prompt": f"{topic} question {number}?",
                    "options": [
                        {"option_id": "a", "label": f"{topic} answer A{number}"},
                        {"option_id": "b", "label": f"{topic} answer B{number}"},
                    ],
                    "correct_option_id": "a",
                }
                for number in range(1, 4)
            ],
        }
    )


def _diagnostic_with_repeated_skill(topic: str = "Byzantine mosaics") -> dict:
    diagnostic = json.loads(_diagnostic_json(topic))
    diagnostic["questions"][1]["skill_id"] = diagnostic["questions"][0]["skill_id"]
    return diagnostic


def _roadmap_json(topic: str = "Byzantine mosaics") -> str:
    return json.dumps(
        {
            "title": f"{topic} studio roadmap",
            "stages": [
                {
                    "stage_id": "foundation",
                    "title": f"{topic} foundations",
                    "objective": f"Recognize the core materials used in {topic}.",
                    "order": 1,
                    "nodes": [
                        {
                            "node_id": "materials",
                            "skill_id": "skill-1",
                            "title": f"{topic} materials",
                            "objective": f"Identify and compare materials used in {topic}.",
                            "order": 1,
                            "estimated_minutes": 45,
                            "due_day": 1,
                        }
                    ],
                },
                {
                    "stage_id": "practice",
                    "title": f"{topic} practice",
                    "objective": f"Produce a small study inspired by {topic}.",
                    "order": 2,
                    "nodes": [
                        {
                            "node_id": "studio-study",
                            "skill_id": "skill-2",
                            "title": f"{topic} studio study",
                            "objective": f"Complete and critique one {topic} study.",
                            "order": 1,
                            "estimated_minutes": 60,
                            "due_day": 2,
                        }
                    ],
                },
                {
                    "stage_id": "portfolio",
                    "title": f"{topic} portfolio",
                    "objective": f"Present a coherent {topic} portfolio.",
                    "order": 3,
                    "nodes": [
                        {
                            "node_id": "final-piece",
                            "skill_id": "skill-3",
                            "title": f"Final {topic} piece",
                            "objective": f"Deliver a documented final {topic} piece.",
                            "order": 1,
                            "estimated_minutes": 90,
                            "due_day": 3,
                        }
                    ],
                },
            ],
        }
    )


def _install_fake_runtime(
    monkeypatch, outputs: list[str], prompts: list[str] | None = None
) -> None:
    remaining = list(outputs)

    class FakeClient:
        def complete(self, **kwargs) -> str:
            assert kwargs["role"] == "planner"
            if prompts is not None:
                prompts.append(kwargs["prompt"])
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    class FakeResolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, capability: str):
            assert capability == "reasoning"
            return FakeClient()

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver", FakeResolver, raising=False
    )


def _create_draft(client, identity: dict, monkeypatch, *, topic: str = "Byzantine mosaics"):
    _install_fake_runtime(monkeypatch, [_diagnostic_json(topic), _roadmap_json(topic)])
    payload = _draft_payload(topic=topic)
    response = client.post(
        "/api/onboarding/dynamic-drafts", headers=identity["headers"], json=payload
    )
    assert response.status_code == 201, response.text
    return payload, response.json()


def _answers(draft: dict) -> list[dict]:
    return [
        {"question_id": question["question_id"], "selected_option_id": "a"}
        for question in draft["questions"]
    ]


def _changed_answers(draft: dict) -> list[dict]:
    answers = _answers(draft)
    answers[0] = {**answers[0], "selected_option_id": "b"}
    return answers


def _initialize_dynamic_goal(client, identity: dict, monkeypatch, *, topic: str = "Byzantine mosaics") -> dict:
    _, draft = _create_draft(client, identity, monkeypatch, topic=topic)
    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )
    assert initialized.status_code == 201, initialized.text
    return initialized.json()


def _create_reassess_draft(
    client,
    identity: dict,
    monkeypatch,
    goal_id: str,
    *,
    topic: str = "Byzantine mosaics",
    prompts: list[str] | None = None,
) -> dict:
    _install_fake_runtime(
        monkeypatch,
        [_diagnostic_json(topic), _roadmap_json(topic)],
        prompts=prompts,
    )
    response = client.post(
        "/api/onboarding/reassess-drafts",
        headers=identity["headers"],
        json={"request_id": str(uuid4()), "goal_id": goal_id, "locale": "en-US"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _apply_reassess(client, identity: dict, goal_id: str, draft: dict, *, request_id: str | None = None, answers: list[dict] | None = None):
    return client.post(
        "/api/onboarding/reassess-from-draft",
        headers=identity["headers"],
        json={
            "request_id": request_id or str(uuid4()),
            "goal_id": goal_id,
            "draft_id": draft["draft_id"],
            "knowledge_answers": answers or _answers(draft),
        },
    )


def test_dynamic_reassess_keeps_goal_id_replaces_plan_and_updates_existing_snapshot(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="reassess-same-goal@example.com")
    initial = _initialize_dynamic_goal(client, identity, monkeypatch)
    goal_id = initial["goal"]["goal_id"]
    old_plan_id = initial["diagnosis"]["active_plan_id"]
    with session_factory() as session:
        old_snapshot = session.scalar(
            select(LearningStateSnapshot).where(LearningStateSnapshot.goal_id == goal_id)
        )
        assert old_snapshot is not None
        old_snapshot_id = old_snapshot.id

    prompts: list[str] = []
    draft = _create_reassess_draft(
        client,
        identity,
        monkeypatch,
        goal_id,
        topic="Mughal miniature painting",
        prompts=prompts,
    )
    assert "Master Byzantine mosaics" in prompts[0]
    assert "Mughal miniature painting" not in prompts[0]
    applied = _apply_reassess(client, identity, goal_id, draft)

    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["goal"]["goal_id"] == goal_id
    assert body["replayed"] is False
    assert body["diagnosis"]["active_plan_id"] != old_plan_id
    assert body["diagnosis"]["active_plan_version"] == 2
    with session_factory() as session:
        old_plan = session.get(LearningPlan, old_plan_id)
        snapshot = session.scalar(
            select(LearningStateSnapshot).where(LearningStateSnapshot.goal_id == goal_id)
        )
        new_plan = session.get(LearningPlan, body["diagnosis"]["active_plan_id"])
        draft_row = session.get(models.UserDiagnosticDraft, draft["draft_id"])
    assert old_plan.status == "replaced"
    assert snapshot.id == old_snapshot_id
    assert snapshot.active_plan_id == new_plan.id
    assert snapshot.baseline_diagnostic_id == body["diagnosis"]["baseline_diagnostic_id"]
    assert new_plan.goal_id == goal_id
    assert new_plan.version == 2
    assert new_plan.generated_by == "dynamic_planner"
    assert draft_row.consumed_at is not None


def test_dynamic_reassess_replays_same_request_and_rejects_payload_conflict(
    client, monkeypatch
) -> None:
    identity = register_user(client, email="reassess-idempotent@example.com")
    initial = _initialize_dynamic_goal(client, identity, monkeypatch)
    goal_id = initial["goal"]["goal_id"]
    draft = _create_reassess_draft(client, identity, monkeypatch, goal_id)
    request_id = str(uuid4())

    first = _apply_reassess(client, identity, goal_id, draft, request_id=request_id)
    replay = _apply_reassess(client, identity, goal_id, draft, request_id=request_id)
    conflict = _apply_reassess(
        client,
        identity,
        goal_id,
        draft,
        request_id=request_id,
        answers=_changed_answers(draft),
    )

    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["diagnosis"]["active_plan_id"] == first.json()["diagnosis"]["active_plan_id"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "onboarding.request_conflict"


def test_dynamic_reassess_rolls_back_plan_snapshot_and_draft_on_workspace_failure(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="reassess-rollback@example.com")
    initial = _initialize_dynamic_goal(client, identity, monkeypatch)
    goal_id = initial["goal"]["goal_id"]
    old_plan_id = initial["diagnosis"]["active_plan_id"]
    with session_factory() as session:
        old_snapshot = session.scalar(
            select(LearningStateSnapshot).where(LearningStateSnapshot.goal_id == goal_id)
        )
        assert old_snapshot is not None
        old_snapshot_id = old_snapshot.id

    draft = _create_reassess_draft(client, identity, monkeypatch, goal_id)

    def fail_workspace(*args, **kwargs):
        raise RuntimeError("injected reassessment workspace failure")

    monkeypatch.setattr(OnboardingService, "_create_dynamic_workspace", fail_workspace)
    with pytest.raises(RuntimeError, match="injected reassessment workspace failure"):
        _apply_reassess(client, identity, goal_id, draft)

    with session_factory() as session:
        old_plan = session.get(LearningPlan, old_plan_id)
        snapshot = session.scalar(
            select(LearningStateSnapshot).where(LearningStateSnapshot.goal_id == goal_id)
        )
        draft_row = session.get(models.UserDiagnosticDraft, draft["draft_id"])
    assert old_plan.status == "active"
    assert snapshot.id == old_snapshot_id
    assert snapshot.active_plan_id == old_plan_id
    assert draft_row.consumed_at is None


def test_dynamic_reassess_rejects_starting_a_task_from_the_replaced_plan(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="reassess-replaced-task@example.com")
    initial = _initialize_dynamic_goal(client, identity, monkeypatch)
    goal_id = initial["goal"]["goal_id"]
    old_plan_id = initial["diagnosis"]["active_plan_id"]
    draft = _create_reassess_draft(client, identity, monkeypatch, goal_id)
    applied = _apply_reassess(client, identity, goal_id, draft)
    assert applied.status_code == 200, applied.text
    with session_factory() as session:
        old_task = session.scalar(select(PlanTask).where(PlanTask.plan_id == old_plan_id))
        assert old_task is not None

    started = client.post(f"/api/tasks/{old_task.id}/start", headers=identity["headers"], json={})

    assert started.status_code == 409
    assert started.json()["detail"]["code"] == "task.not_active_plan"


def test_dynamic_reassess_rejects_foreign_goal_and_active_learning_session(
    client, session_factory, monkeypatch
) -> None:
    owner = register_user(client, email="reassess-owner@example.com")
    other = register_user(client, email="reassess-other@example.com")
    initial = _initialize_dynamic_goal(client, owner, monkeypatch)
    goal_id = initial["goal"]["goal_id"]

    foreign = client.post(
        "/api/onboarding/reassess-drafts",
        headers=other["headers"],
        json={"request_id": str(uuid4()), "goal_id": goal_id, "locale": "en-US"},
    )
    assert foreign.status_code == 404

    draft = _create_reassess_draft(client, owner, monkeypatch, goal_id)
    with session_factory() as session:
        task = session.scalar(
            select(PlanTask).where(PlanTask.plan_id == initial["diagnosis"]["active_plan_id"])
        )
        assert task is not None
        task.status = "active"
        session.add(
            LearningSession(
                id=f"session-{uuid4()}",
                user_id=owner["user_id"],
                goal_id=goal_id,
                plan_id=task.plan_id,
                task_id=task.id,
                status="active",
                evidence_json={},
            )
        )
        session.commit()

    blocked = _apply_reassess(client, owner, goal_id, draft)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "onboarding.active_learning_session"


def test_generated_diagnostic_allows_multiple_questions_for_one_skill() -> None:
    generated = _GeneratedDiagnostic.model_validate(_diagnostic_with_repeated_skill())

    assert [question.skill_id for question in generated.questions] == [
        "skill-1",
        "skill-1",
        "skill-3",
    ]


def test_generated_diagnostic_still_rejects_duplicate_question_ids() -> None:
    diagnostic = json.loads(_diagnostic_json())
    diagnostic["questions"][1]["question_id"] = diagnostic["questions"][0]["question_id"]

    with pytest.raises(ValidationError):
        _GeneratedDiagnostic.model_validate(diagnostic)


@pytest.mark.parametrize("invalid_case", ["duplicate_option_id", "missing_correct_option"])
def test_generated_diagnostic_keeps_deterministic_option_validation(invalid_case: str) -> None:
    diagnostic = json.loads(_diagnostic_json())
    question = diagnostic["questions"][0]
    if invalid_case == "duplicate_option_id":
        question["options"][1]["option_id"] = question["options"][0]["option_id"]
    else:
        question["correct_option_id"] = "missing"

    with pytest.raises(ValidationError):
        _GeneratedDiagnostic.model_validate(diagnostic)


def test_dynamic_draft_public_contract_hides_scoring_and_is_user_private(
    client, monkeypatch
) -> None:
    owner = register_user(client, email="roadmap-owner@example.com")
    other = register_user(client, email="roadmap-other@example.com")
    payload, draft = _create_draft(client, owner, monkeypatch)

    assert set(draft) == {"draft_id", "expires_at", "title", "questions"}
    assert len(draft["questions"]) == 3
    assert set(draft["questions"][0]) == {"question_id", "prompt", "options"}
    assert set(draft["questions"][0]["options"][0]) == {"option_id", "label"}
    assert "correct" not in json.dumps(draft).lower()

    stolen = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=other["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )
    assert stolen.status_code == 404

    replay = client.post(
        "/api/onboarding/dynamic-drafts", headers=owner["headers"], json=payload
    )
    assert replay.status_code == 201
    assert replay.json() == draft


def test_live_existing_draft_replays_after_goal_deadline_without_runtime(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-live-expired-replay@example.com")
    request_id = str(uuid4())
    draft_id = f"draft-{uuid4()}"
    diagnostic = json.loads(_diagnostic_json())
    expired_goal = _goal()
    expired_goal["deadline"] = (date.today() - timedelta(days=1)).isoformat()
    public_questions = [
        {
            "question_id": question["question_id"],
            "prompt": question["prompt"],
            "options": question["options"],
        }
        for question in diagnostic["questions"]
    ]
    with session_factory() as session:
        session.add(
            models.UserDiagnosticDraft(
                id=draft_id,
                user_id=identity["user_id"],
                request_id=request_id,
                locale="en-US",
                goal_input=expired_goal,
                title=diagnostic["title"],
                public_questions=public_questions,
                scoring_key={
                    question["question_id"]: {
                        "correct_option_id": question["correct_option_id"],
                        "skill_id": question["skill_id"],
                    }
                    for question in diagnostic["questions"]
                },
                expires_at=datetime.utcnow() + timedelta(minutes=30),
            )
        )
        session.commit()

    class UnexpectedResolver:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("live draft replay must not resolve a runtime")

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver",
        UnexpectedResolver,
        raising=False,
    )
    payload = _draft_payload(request_id=request_id)
    payload["goal"] = expired_goal

    response = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=payload,
    )

    assert response.status_code == 201, response.text
    assert response.json()["draft_id"] == draft_id
    assert response.json()["questions"] == public_questions


def test_repeated_skill_questions_flow_independently_into_roadmap(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-repeated-skill@example.com")
    diagnostic = _diagnostic_with_repeated_skill()
    roadmap = json.loads(_roadmap_json())
    roadmap["stages"][1]["nodes"][0]["skill_id"] = "skill-1"
    outputs = iter([json.dumps(diagnostic), json.dumps(roadmap)])
    prompts: list[str] = []

    class FakeClient:
        last_completion_metadata = {"model": "repeated-skill-model", "finish_reason": "stop"}

        def complete(self, **kwargs) -> str:
            prompts.append(kwargs["prompt"])
            return next(outputs)

    class FakeResolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, capability: str):
            assert capability == "reasoning"
            return FakeClient()

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver", FakeResolver, raising=False
    )

    created = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )

    assert created.status_code == 201, created.text
    draft = created.json()
    assert "correct_option_id" not in json.dumps(draft)
    with session_factory() as session:
        stored = session.get(models.UserDiagnosticDraft, draft["draft_id"])
        assert stored.scoring_key["q-1"]["skill_id"] == "skill-1"
        assert stored.scoring_key["q-2"]["skill_id"] == "skill-1"

    answers = _answers(draft)
    answers[1]["selected_option_id"] = "b"
    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": answers,
        },
    )

    assert initialized.status_code == 201, initialized.text
    assert "Questions may share the same skill_id" in prompts[0]
    assert "different scenarios, perspectives, applications, or common misconceptions" in prompts[0]
    assert 'Required diagnostic skill IDs: ["skill-1", "skill-3"]' in prompts[1]
    assert '"skill_id":"skill-1","correct":1,"question_count":2' in prompts[1]
    assert f"today={date.today().isoformat()}" in prompts[1]
    assert f"deadline={_goal()['deadline']}" in prompts[1]
    assert "weekly_minutes=480" in prompts[1]
    assert "3-8 ordered learning stages" in prompts[1]
    assert "1-6 ordered task nodes per stage" in prompts[1]
    assert "node_id,skill_id,title" in prompts[1]
    scored_answers = json.loads(prompts[1].split("Diagnostic results: ", 1)[1])
    assert scored_answers[:2] == [
        {"question_id": "q-1", "skill_id": "skill-1", "correct": True},
        {"question_id": "q-2", "skill_id": "skill-1", "correct": False},
    ]
    body = initialized.json()
    assert "correct_option_id" not in json.dumps(body)
    assert "selected_option_id" not in json.dumps(body)

    with session_factory() as session:
        rows = list(
            session.execute(
                select(MasteryRecord, KnowledgeNode)
                .join(KnowledgeNode, KnowledgeNode.id == MasteryRecord.knowledge_node_id)
                .order_by(KnowledgeNode.sequence)
            )
        )
        first, second, third = rows
        for mastery, node in (first, second):
            assert mastery.mastery_score == 50.0
            assert mastery.confidence == pytest.approx(0.7)
            assert mastery.evidence_count == 2
            assert mastery.source_breakdown["diagnostic"] == {
                "skill_id": "skill-1",
                "question_ids": ["q-1", "q-2"],
                "question_count": 2,
            }
            assert node.metadata_json["skill_id"] == "skill-1"
        assert third[0].mastery_score == 100.0
        assert third[0].confidence == pytest.approx(0.6)
        assert third[0].evidence_count == 1
        diagnostic_row = session.scalar(select(BaselineDiagnostic))
        assert diagnostic_row.score_breakdown == {"score": 66.67, "question_count": 3}


def test_dynamic_onboarding_writes_safe_mastery_memory_and_proposes_low_skill_adjustment(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-mastery-trace@example.com")
    diagnostic = _diagnostic_with_repeated_skill()
    roadmap = json.loads(_roadmap_json())
    roadmap["stages"][1]["nodes"][0]["skill_id"] = "skill-1"
    _install_fake_runtime(monkeypatch, [json.dumps(diagnostic), json.dumps(roadmap)])

    draft = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    ).json()
    answers = _answers(draft)
    answers[1]["selected_option_id"] = "b"
    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": answers,
        },
    )

    assert initialized.status_code == 201, initialized.text
    body = initialized.json()
    trace = body["state"]["latest_plan_adjustment"]["evidence_json"]["diagnostic_trace"]
    assert trace["draft_id"] == draft["draft_id"]
    assert trace["request_id"]
    assert trace["skills"] == [
        {"skill_id": "skill-1", "question_count": 2, "correct_count": 1, "score": 50.0},
    ]
    assert "correct_option_id" not in json.dumps(trace)
    assert "selected_option_id" not in json.dumps(trace)
    assert body["state"]["latest_plan_adjustment"]["status"] == "proposed"
    assert body["state"]["latest_plan_adjustment"]["requires_confirmation"] is True

    with session_factory() as session:
        memories = list(session.scalars(select(models.Memory).order_by(models.Memory.id)))
        assert len(memories) == 3
        for memory in memories:
            assert memory.memory_type == "mastery_summary"
            assert memory.source_kind == "mastery_record"
            assert memory.source_metadata["draft_id"] == draft["draft_id"]
            assert memory.source_metadata["request_id"]
            assert memory.source_metadata["knowledge_node_id"] == memory.content_json["knowledge_node_id"]
            assert "correct_option_id" not in json.dumps(memory.source_metadata)
            assert "selected_option_id" not in json.dumps(memory.source_metadata)
        mastery_rows = list(session.scalars(select(MasteryRecord)))
        for mastery in mastery_rows:
            stored_trace = mastery.source_breakdown["trace"]
            assert stored_trace["goal_id"] == body["goal"]["goal_id"]
            assert stored_trace["draft_id"] == draft["draft_id"]
            assert stored_trace["knowledge_node_id"] == mastery.knowledge_node_id
            assert "correct_option_id" not in json.dumps(stored_trace)
            assert "selected_option_id" not in json.dumps(stored_trace)
        adjustment = session.scalar(select(PlanAdjustmentRecord))
        assert adjustment is not None
        assert adjustment.decision == "remediate"
        assert adjustment.status == "proposed"
        assert adjustment.requires_confirmation is True

    with session_factory() as session:
        applied = apply_plan_adjustment(
            session,
            adjustment_id=body["state"]["latest_plan_adjustment"]["adjustment_id"],
            user_id=identity["user_id"],
            goal_id=body["goal"]["goal_id"],
            locale="en-US",
        )
        review_nodes = {
            item["knowledge_node_id"]
            for item in applied["created_tasks"]
            if item["task_type"] == "review"
        }
        assert len(review_nodes) == 2
        assert applied["status"] == "applied"


def test_dynamic_onboarding_respects_learning_result_memory_privacy(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-mastery-privacy@example.com")
    with session_factory() as session:
        profile = session.get(models.LearnerProfile, identity["user_id"])
        assert profile is not None
        profile.privacy_settings = {
            "long_term_memory": {
                "enabled": True,
                "allow_explicit_user": True,
                "allow_system_inference": False,
                "allow_learning_results": False,
            }
        }
        session.commit()
    _install_fake_runtime(monkeypatch, [_diagnostic_json(), _roadmap_json()])
    draft = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    ).json()

    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )

    assert initialized.status_code == 201, initialized.text
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.Memory)) == 0


def test_expired_goal_deadline_avoids_runtime_and_provider(client, session_factory, monkeypatch) -> None:
    identity = register_user(client, email="roadmap-expired-goal@example.com")

    class UnexpectedResolver:
        def __init__(self, *args, **kwargs) -> None:
            raise AssertionError("expired deadlines must be rejected before runtime resolution")

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver",
        UnexpectedResolver,
        raising=False,
    )
    payload = _draft_payload()
    payload["goal"]["deadline"] = (date.today() - timedelta(days=1)).isoformat()

    response = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "onboarding.deadline_expired",
        "message": "The learning goal deadline has already passed.",
    }
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.UserDiagnosticDraft)) == 0


def test_expired_and_invalid_answers_do_not_consume_draft(client, session_factory, monkeypatch) -> None:
    identity = register_user(client, email="roadmap-invalid@example.com")
    _, draft = _create_draft(client, identity, monkeypatch)

    invalid = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": [
                {"question_id": draft["questions"][0]["question_id"], "selected_option_id": "forged"}
            ],
        },
    )
    assert invalid.status_code == 422
    with session_factory() as session:
        stored = session.get(models.UserDiagnosticDraft, draft["draft_id"])
        assert stored.consumed_at is None
        stored.expires_at = datetime.utcnow() - timedelta(seconds=1)
        session.commit()

    expired = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )
    assert expired.status_code == 410
    with session_factory() as session:
        assert session.get(models.UserDiagnosticDraft, draft["draft_id"]).consumed_at is None
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 0


def test_missing_reasoning_configuration_returns_safe_actionable_error(
    client, session_factory, monkeypatch, caplog
) -> None:
    identity = register_user(client, email="roadmap-unavailable@example.com")

    class FailingResolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, capability: str):
            raise RuntimeResolutionError("runtime.credential_missing")

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver", FailingResolver, raising=False
    )
    response = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "onboarding.dynamic_configuration_invalid",
        "message": "Reasoning model configuration is unavailable.",
    }
    assert "credential" not in response.text.lower()
    assert any(
        getattr(record, "error_code", None) == "runtime.credential_missing"
        and getattr(record, "operation", None) == "diagnostic"
        for record in caplog.records
    )
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.UserDiagnosticDraft)) == 0
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 0


def test_dynamic_diagnostic_repairs_invalid_output_once(client, monkeypatch) -> None:
    """Removing JSON mode or the single repair attempt must fail this end-user workflow."""
    identity = register_user(client, email="roadmap-repair@example.com")
    outputs = iter(["not-json", _diagnostic_json()])
    calls: list[dict] = []

    class FakeClient:
        last_completion_metadata = {
            "model": "repair-model",
            "finish_reason": "stop",
        }

        def complete(self, **kwargs) -> str:
            calls.append(kwargs)
            return next(outputs)

    class FakeResolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, capability: str):
            assert capability == "reasoning"
            return FakeClient()

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver", FakeResolver, raising=False
    )

    response = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )

    assert response.status_code == 201, response.text
    assert len(calls) == 2
    assert all(call["json_output"] is True for call in calls)
    assert "previous response was invalid" in calls[1]["prompt"].lower()


def test_dynamic_diagnostic_rejects_output_after_one_repair_without_persistence(
    client, session_factory, monkeypatch
) -> None:
    """Accepting repeated invalid output or retrying forever must fail this bounded workflow."""
    identity = register_user(client, email="roadmap-invalid-output@example.com")
    _install_fake_runtime(monkeypatch, ["not-json", '{"still":"invalid"}'])

    response = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "onboarding.dynamic_output_invalid"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.UserDiagnosticDraft)) == 0
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 0


def test_dynamic_provider_failure_returns_retryable_code_and_safe_structured_log(
    client, monkeypatch, caplog
) -> None:
    """Collapsing provider outages into config errors or logging private data must fail this test."""
    identity = register_user(client, email="roadmap-provider@example.com")
    topic = "DO_NOT_LOG_PROMPT_SENTINEL"

    class FailingClient:
        last_completion_metadata = {
            "model": "safe-model",
            "finish_reason": None,
        }

        def complete(self, **kwargs) -> str:
            raise EvaluationProviderError(
                "DO_NOT_LOG_PROVIDER_BODY",
                error_code="provider_http_429",
                request_latency_ms=12.5,
                total_latency_ms=13.0,
                retry_count=1,
            )

    class FakeResolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, capability: str):
            assert capability == "reasoning"
            return FailingClient()

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver", FakeResolver, raising=False
    )

    response = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(topic=topic),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "onboarding.dynamic_provider_unavailable"
    assert topic not in caplog.text
    assert "DO_NOT_LOG_PROVIDER_BODY" not in caplog.text
    assert any(
        getattr(record, "error_code", None) == "provider_http_429"
        and getattr(record, "retry_count", None) == 1
        and getattr(record, "operation", None) == "diagnostic"
        for record in caplog.records
    )


def test_dynamic_diagnostic_repairs_invalid_provider_response_once(
    client, monkeypatch
) -> None:
    """Treating a malformed 200 provider response as an outage must fail this repair test."""
    identity = register_user(client, email="roadmap-provider-invalid@example.com")
    calls = 0

    class RepairingClient:
        last_completion_metadata = {"model": "repair-model", "finish_reason": None}

        def complete(self, **kwargs) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise EvaluationProviderError(
                    "private malformed response",
                    error_code="provider_response_invalid",
                    request_latency_ms=1,
                    total_latency_ms=1,
                    retry_count=0,
                )
            return _diagnostic_json()

    class FakeResolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, capability: str):
            return RepairingClient()

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver", FakeResolver, raising=False
    )

    response = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )

    assert response.status_code == 201, response.text
    assert calls == 2


def test_repeated_incomplete_provider_output_is_not_mislabeled_as_an_outage(
    client, session_factory, monkeypatch
) -> None:
    """Repeated truncation must exhaust one repair and surface an output error without persistence."""
    identity = register_user(client, email="roadmap-provider-incomplete@example.com")
    calls = 0

    class IncompleteClient:
        last_completion_metadata = {"model": "truncated-model", "finish_reason": "length"}

        def complete(self, **kwargs) -> str:
            nonlocal calls
            calls += 1
            raise EvaluationProviderError(
                "private truncated response",
                error_code="provider_response_incomplete",
                request_latency_ms=1,
                total_latency_ms=1,
                retry_count=0,
            )

    class FakeResolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, capability: str):
            return IncompleteClient()

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver", FakeResolver, raising=False
    )

    response = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "onboarding.dynamic_output_invalid"
    assert calls == 2
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.UserDiagnosticDraft)) == 0


def test_malformed_roadmap_is_rejected_without_consuming_draft(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-malformed@example.com")
    _install_fake_runtime(monkeypatch, [_diagnostic_json(), '{"title":"bad","stages":[]}'])
    created = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )
    assert created.status_code == 201, created.text
    draft = created.json()

    response = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "onboarding.dynamic_output_invalid"
    with session_factory() as session:
        assert session.get(models.UserDiagnosticDraft, draft["draft_id"]).consumed_at is None
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 0


def test_roadmap_repairs_business_infeasibility_once(client, monkeypatch) -> None:
    """Rejecting a repairable schedule before the repair attempt must fail this workflow test."""
    identity = register_user(client, email="roadmap-feasibility-repair@example.com")
    invalid_roadmap = json.loads(_roadmap_json())
    invalid_roadmap["stages"][0]["nodes"][0]["due_day"] = 3
    invalid_roadmap["stages"][1]["nodes"][0]["due_day"] = 2
    invalid_roadmap["stages"][2]["nodes"][0]["due_day"] = 1
    outputs = iter([_diagnostic_json(), json.dumps(invalid_roadmap), _roadmap_json()])
    prompts: list[str] = []

    class RepairClient:
        def complete(self, **kwargs) -> str:
            prompts.append(kwargs["prompt"])
            return next(outputs)

    class RepairResolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, capability: str):
            assert capability == "reasoning"
            return RepairClient()

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver",
        RepairResolver,
        raising=False,
    )
    created = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )
    draft = created.json()

    response = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["state"]["roadmap"]["stages"][0]["stage_id"] == "foundation"
    assert "reason_code=order" in prompts[2]
    assert '"due_day_min":1' in prompts[2]
    assert f'"deadline":"{_goal()["deadline"]}"' in prompts[2]
    assert '"weekly_minutes":480' in prompts[2]


def test_roadmap_repairs_unknown_skill_once(client, monkeypatch) -> None:
    identity = register_user(client, email="roadmap-unknown-skill-repair@example.com")
    invalid_roadmap = json.loads(_roadmap_json())
    invalid_roadmap["stages"][0]["nodes"][0]["skill_id"] = "unknown-private-skill"
    outputs = iter([_diagnostic_json(), json.dumps(invalid_roadmap), _roadmap_json()])
    prompts: list[str] = []

    class RepairClient:
        def complete(self, **kwargs) -> str:
            prompts.append(kwargs["prompt"])
            return next(outputs)

    class RepairResolver:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def resolve(self, capability: str):
            return RepairClient()

    monkeypatch.setattr(
        "backend.app.application.onboarding_service.RuntimeResolver",
        RepairResolver,
        raising=False,
    )
    draft = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    ).json()

    response = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )

    assert response.status_code == 201, response.text
    assert "reason_code=unknown_skill" in prompts[2]
    assert '"allowed_skill_ids":["skill-1","skill-2","skill-3"]' in prompts[2]


@pytest.mark.parametrize(
    ("invalid_case", "reason_code", "constraint_fragment", "error_code"),
    [
        ("deadline", "deadline", '"received_due_day_max"', "onboarding.dynamic_roadmap_infeasible"),
        ("weekly_budget", "weekly_budget", '"received_weekly_minutes"', "onboarding.dynamic_roadmap_infeasible"),
        ("skill_coverage", "skill_coverage", '"missing_skill_ids":["skill-2","skill-3"]', "onboarding.dynamic_output_invalid"),
    ],
)
def test_exhausted_roadmap_feasibility_branch_rolls_back(
    invalid_case,
    reason_code,
    constraint_fragment,
    error_code,
    client,
    session_factory,
    monkeypatch,
) -> None:
    identity = register_user(
        client, email=f"roadmap-{invalid_case}-exhausted@example.com"
    )
    roadmap = json.loads(_roadmap_json())
    if invalid_case == "deadline":
        due_day_max = (date.fromisoformat(_goal()["deadline"]) - date.today()).days + 1
        roadmap["stages"][2]["nodes"][0]["due_day"] = due_day_max + 1
    elif invalid_case == "weekly_budget":
        for stage in roadmap["stages"]:
            stage["nodes"][0]["due_day"] = 1
            stage["nodes"][0]["estimated_minutes"] = 240
    else:
        for stage in roadmap["stages"]:
            stage["nodes"][0]["skill_id"] = "skill-1"
    prompts: list[str] = []
    _install_fake_runtime(
        monkeypatch,
        [_diagnostic_json(), json.dumps(roadmap)],
        prompts,
    )
    draft = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    ).json()

    response = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == error_code
    assert f"reason_code={reason_code}" in prompts[2]
    assert constraint_fragment in prompts[2]
    with session_factory() as session:
        assert session.get(models.UserDiagnosticDraft, draft["draft_id"]).consumed_at is None
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 0


def test_roadmap_rejects_deadlines_that_move_backwards_between_stages(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-ordering@example.com")
    roadmap = json.loads(_roadmap_json())
    roadmap["stages"][0]["nodes"][0]["due_day"] = 3
    roadmap["stages"][1]["nodes"][0]["due_day"] = 2
    roadmap["stages"][2]["nodes"][0]["due_day"] = 1
    _install_fake_runtime(monkeypatch, [_diagnostic_json(), json.dumps(roadmap)])
    created = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )
    assert created.status_code == 201
    draft = created.json()

    response = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "onboarding.dynamic_output_invalid"
    with session_factory() as session:
        assert session.get(models.UserDiagnosticDraft, draft["draft_id"]).consumed_at is None
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 0


def test_roadmap_rejects_coerced_numeric_fields(client, monkeypatch) -> None:
    identity = register_user(client, email="roadmap-strict-json@example.com")
    roadmap = json.loads(_roadmap_json())
    roadmap["stages"][0]["nodes"][0]["estimated_minutes"] = "45"
    _install_fake_runtime(monkeypatch, [_diagnostic_json(), json.dumps(roadmap)])
    draft = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    ).json()

    response = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "onboarding.dynamic_output_invalid"


def test_diagnostic_rejects_normalized_duplicate_option_labels(client, monkeypatch) -> None:
    identity = register_user(client, email="roadmap-duplicate-labels@example.com")
    diagnostic = json.loads(_diagnostic_json())
    diagnostic["questions"][0]["options"] = [
        {"option_id": "a", "label": "Same answer"},
        {"option_id": "b", "label": "  ＳＡＭＥ   ANSWER  "},
    ]
    _install_fake_runtime(monkeypatch, [json.dumps(diagnostic)])

    response = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "onboarding.dynamic_output_invalid"


def test_initialize_dynamic_topic_builds_linked_private_roadmap_and_replays_success(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-success@example.com")
    _, draft = _create_draft(client, identity, monkeypatch, topic="Byzantine mosaics")
    request_id = str(uuid4())
    payload = {
        "request_id": request_id,
        "draft_id": draft["draft_id"],
        "knowledge_answers": _answers(draft),
    }

    first = client.post(
        "/api/onboarding/initialize-from-draft", headers=identity["headers"], json=payload
    )
    replay = client.post(
        "/api/onboarding/initialize-from-draft", headers=identity["headers"], json=payload
    )

    assert first.status_code == 201, first.text
    assert replay.status_code == 201, replay.text
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is True
    assert replay.json()["goal"] == first.json()["goal"]
    roadmap = first.json()["state"]["roadmap"]
    assert roadmap["title"] == "Byzantine mosaics studio roadmap"
    assert roadmap["locale"] == "en-US"
    assert [stage["status"] for stage in roadmap["stages"]] == ["current", "locked", "locked"]
    assert roadmap["stages"][0]["nodes"][0]["status"] == "current"
    assert "AI tutor" not in json.dumps(roadmap)

    with session_factory() as session:
        plan = session.scalar(select(LearningPlan))
        curriculum = session.get(Curriculum, plan.curriculum_id)
        tasks = list(session.scalars(select(PlanTask).order_by(PlanTask.scheduled_day)))
        draft_row = session.get(models.UserDiagnosticDraft, draft["draft_id"])
        assert curriculum.owner_user_id == identity["user_id"]
        assert len(tasks) == 3
        assert all(task.knowledge_node_id and task.knowledge_node_code for task in tasks)
        assert [task.payload["stage_order"] for task in tasks] == [1, 2, 3]
        assert [task.priority for task in tasks] == [1, 2, 3]
        assert all(task.payload["locale"] == "en-US" for task in tasks)
        assert plan.valid_to == tasks[-1].scheduled_date
        assert draft_row.consumed_at is not None
        assert session.scalar(select(func.count()).select_from(LearningGoal)) == 1
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 1


def test_dynamic_replay_rejects_changed_answers_but_accepts_canonical_reordering(
    client, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-replay-conflict@example.com")
    _, draft = _create_draft(client, identity, monkeypatch)
    request_id = str(uuid4())
    payload = {
        "request_id": request_id,
        "draft_id": draft["draft_id"],
        "knowledge_answers": _answers(draft),
    }
    first = client.post(
        "/api/onboarding/initialize-from-draft", headers=identity["headers"], json=payload
    )
    assert first.status_code == 201

    conflict = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={**payload, "knowledge_answers": _changed_answers(draft)},
    )
    replay = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={**payload, "knowledge_answers": list(reversed(_answers(draft)))},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "onboarding.request_conflict"
    assert replay.status_code == 201
    assert replay.json()["replayed"] is True


def test_integrity_recovery_rejects_changed_dynamic_answers(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-recovery-conflict@example.com")
    _, draft = _create_draft(client, identity, monkeypatch)
    request_id = str(uuid4())
    original_payload = {
        "request_id": request_id,
        "draft_id": draft["draft_id"],
        "knowledge_answers": _answers(draft),
    }
    first = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json=original_payload,
    )
    assert first.status_code == 201
    with session_factory() as session:
        session.get(models.UserDiagnosticDraft, draft["draft_id"]).consumed_at = None
        session.commit()

    _install_fake_runtime(monkeypatch, [_roadmap_json()])
    original_find = OnboardingService._find_existing_diagnostic
    calls = 0

    def miss_initial_lookup(self, *, user_id: str, request_id: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return original_find(self, user_id=user_id, request_id=request_id)

    monkeypatch.setattr(OnboardingService, "_find_existing_diagnostic", miss_initial_lookup)
    conflict = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={**original_payload, "knowledge_answers": _changed_answers(draft)},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "onboarding.request_conflict"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(LearningGoal)) == 1
        assert session.scalar(select(func.count()).select_from(BaselineDiagnostic)) == 1
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 1


def test_interleaved_unique_requests_publish_one_workspace_for_one_draft(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-consumption-cas@example.com")
    _install_fake_runtime(
        monkeypatch,
        [_diagnostic_json(), _roadmap_json(), _roadmap_json()],
    )
    created = client.post(
        "/api/onboarding/dynamic-drafts",
        headers=identity["headers"],
        json=_draft_payload(),
    )
    assert created.status_code == 201
    draft = created.json()
    nested_responses = []
    interleaved = False
    original_create = OnboardingService._create_dynamic_workspace

    def interleave_second_request(self, **kwargs):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            nested_responses.append(
                client.post(
                    "/api/onboarding/initialize-from-draft",
                    headers=identity["headers"],
                    json={
                        "request_id": str(uuid4()),
                        "draft_id": draft["draft_id"],
                        "knowledge_answers": _answers(draft),
                    },
                )
            )
        return original_create(self, **kwargs)

    monkeypatch.setattr(
        OnboardingService, "_create_dynamic_workspace", interleave_second_request
    )
    outer = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    )

    assert nested_responses[0].status_code == 201
    assert outer.status_code == 409
    assert outer.json()["detail"]["code"] == "onboarding.draft_consumed"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(LearningGoal)) == 1
        assert session.scalar(select(func.count()).select_from(BaselineDiagnostic)) == 1
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 1
        assert session.scalar(select(func.count()).select_from(Curriculum)) == 1


def test_legacy_plan_has_null_roadmap(client) -> None:
    identity = register_user(client, email="legacy-roadmap@example.com")
    initialized = client.post(
        "/api/onboarding/initialize",
        headers=identity["headers"],
        json=initialize_payload(),
    )
    assert initialized.status_code == 201
    assert initialized.json()["state"]["roadmap"] is None


def test_plan_adjustment_keeps_private_curriculum_and_roadmap_metadata(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-replan@example.com")
    _, draft = _create_draft(client, identity, monkeypatch, topic="Ceramic glazing")
    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    ).json()
    goal_id = initialized["goal"]["goal_id"]
    old_plan_id = initialized["diagnosis"]["active_plan_id"]

    with session_factory() as session:
        old_plan = session.get(LearningPlan, old_plan_id)
        curriculum_id = old_plan.curriculum_id
        adjustment = PlanAdjustmentRecord(
            id="adjustment-private-roadmap",
            user_id=identity["user_id"],
            goal_id=goal_id,
            previous_plan_id=old_plan_id,
            trigger_type="manual",
            decision="reduce",
            evidence_json={},
            before_snapshot={},
            after_snapshot={},
            plan_patch={"load_multiplier": 0.8},
            change_summary={},
            rationale_json={},
            status="proposed",
            base_plan_version=1,
        )
        session.add(adjustment)
        session.commit()
        applied = apply_plan_adjustment(
            session,
            adjustment_id=adjustment.id,
            user_id=identity["user_id"],
            goal_id=goal_id,
            locale="en-US",
        )
        new_plan = session.get(LearningPlan, applied["new_plan_id"])
        assert new_plan.curriculum_id == curriculum_id

    state = client.get(
        "/api/state/current", headers=identity["headers"], params={"goal_id": goal_id}
    )
    assert state.status_code == 200
    assert state.json()["roadmap"]["title"] == "Ceramic glazing studio roadmap"
    assert state.json()["roadmap"]["plan_version"] == 2


def test_roadmap_keeps_completed_curriculum_nodes_and_mastery_progress_after_replan(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-history-projection@example.com")
    _, draft = _create_draft(client, identity, monkeypatch, topic="Woodblock printing")
    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    ).json()
    goal_id = initialized["goal"]["goal_id"]
    old_plan_id = initialized["diagnosis"]["active_plan_id"]
    initial_node_ids = [
        node["node_id"]
        for stage in initialized["state"]["roadmap"]["stages"]
        for node in stage["nodes"]
    ]

    with session_factory() as session:
        tasks = list(
            session.scalars(
                select(PlanTask)
                .where(PlanTask.plan_id == old_plan_id)
                .order_by(PlanTask.scheduled_day)
            )
        )
        tasks[0].status = "completed"
        second_mastery = session.scalar(
            select(MasteryRecord).where(
                MasteryRecord.user_id == identity["user_id"],
                MasteryRecord.goal_id == goal_id,
                MasteryRecord.knowledge_node_id == tasks[1].knowledge_node_id,
            )
        )
        second_mastery.mastery_score = 42
        session.add(
            PlanAdjustmentRecord(
                id="adjustment-roadmap-history",
                user_id=identity["user_id"],
                goal_id=goal_id,
                previous_plan_id=old_plan_id,
                trigger_type="manual",
                decision="reduce",
                evidence_json={},
                before_snapshot={},
                after_snapshot={},
                plan_patch={"load_multiplier": 0.8},
                change_summary={},
                rationale_json={},
                status="proposed",
                base_plan_version=1,
            )
        )
        session.commit()
        apply_plan_adjustment(
            session,
            adjustment_id="adjustment-roadmap-history",
            user_id=identity["user_id"],
            goal_id=goal_id,
            locale="en-US",
        )

    roadmap = client.get(
        "/api/state/current",
        headers=identity["headers"],
        params={"goal_id": goal_id},
    ).json()["roadmap"]
    nodes = [node for stage in roadmap["stages"] for node in stage["nodes"]]
    assert [node["node_id"] for node in nodes] == initial_node_ids
    assert len({node["node_id"] for node in nodes}) == 3
    assert [stage["status"] for stage in roadmap["stages"]] == [
        "completed",
        "current",
        "locked",
    ]
    assert nodes[0]["status"] == "completed"
    assert nodes[1]["status"] == "current"
    assert nodes[1]["progress"] == 0.42


def test_dynamic_reduce_clones_preserve_generated_copy_and_metadata(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-reduce-copy@example.com")
    _, draft = _create_draft(client, identity, monkeypatch, topic="Marquetry")
    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    ).json()
    goal_id = initialized["goal"]["goal_id"]
    old_plan_id = initialized["diagnosis"]["active_plan_id"]
    with session_factory() as session:
        sources = list(
            session.scalars(select(PlanTask).where(PlanTask.plan_id == old_plan_id))
        )
        nodes = {
            node.id: node
            for node in session.scalars(
                select(KnowledgeNode).where(
                    KnowledgeNode.id.in_([task.knowledge_node_id for task in sources])
                )
            )
        }
        for source in sources:
            source.title = "stale task title"
            source.objective = "stale task objective"
            source.payload = {
                **source.payload,
                "locale": "stale-locale",
                "stage_id": "stale-stage",
                "node_id": "stale-node",
            }
        session.add(
            PlanAdjustmentRecord(
                id="adjustment-dynamic-reduce-copy",
                user_id=identity["user_id"],
                goal_id=goal_id,
                previous_plan_id=old_plan_id,
                trigger_type="manual",
                decision="reduce",
                evidence_json={},
                before_snapshot={},
                after_snapshot={},
                plan_patch={"load_multiplier": 0.8},
                change_summary={},
                rationale_json={},
                status="proposed",
                base_plan_version=1,
            )
        )
        session.commit()
        applied = apply_plan_adjustment(
            session,
            adjustment_id="adjustment-dynamic-reduce-copy",
            user_id=identity["user_id"],
            goal_id=goal_id,
            locale="zh-CN",
        )
        clones = list(
            session.scalars(select(PlanTask).where(PlanTask.plan_id == applied["new_plan_id"]))
        )
        for clone in clones:
            node = nodes[clone.knowledge_node_id]
            assert clone.title == node.title
            assert clone.objective == node.metadata_json["objective"]
            for key in ("locale", "stage_id", "stage_order", "node_id", "node_order"):
                assert clone.payload[key] == node.metadata_json[key]


def test_dynamic_remediate_task_preserves_generated_node_copy_and_metadata(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-remediate-copy@example.com")
    _, draft = _create_draft(client, identity, monkeypatch, topic="Bookbinding")
    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    ).json()
    goal_id = initialized["goal"]["goal_id"]
    old_plan_id = initialized["diagnosis"]["active_plan_id"]
    with session_factory() as session:
        node = session.scalar(
            select(KnowledgeNode)
            .where(KnowledgeNode.curriculum_id == session.get(LearningPlan, old_plan_id).curriculum_id)
            .order_by(KnowledgeNode.sequence)
        )
        session.add(
            PlanAdjustmentRecord(
                id="adjustment-dynamic-remediate-copy",
                user_id=identity["user_id"],
                goal_id=goal_id,
                previous_plan_id=old_plan_id,
                trigger_type="manual",
                decision="remediate",
                evidence_json={
                    "observer_signals": {
                        "low_mastery_nodes": [{"knowledge_node_id": node.id}]
                    }
                },
                before_snapshot={},
                after_snapshot={},
                plan_patch={"review_task_count": 1},
                change_summary={},
                rationale_json={},
                status="proposed",
                base_plan_version=1,
            )
        )
        session.commit()
        applied = apply_plan_adjustment(
            session,
            adjustment_id="adjustment-dynamic-remediate-copy",
            user_id=identity["user_id"],
            goal_id=goal_id,
            locale="zh-CN",
        )
        review = session.scalar(
            select(PlanTask).where(
                PlanTask.plan_id == applied["new_plan_id"],
                PlanTask.task_type == "review",
            )
        )
        assert review.title == node.title
        assert review.objective == node.metadata_json["objective"]
        for key in ("locale", "stage_id", "stage_order", "node_id", "node_order"):
            assert review.payload[key] == node.metadata_json[key]


def test_dynamic_advance_task_preserves_generated_node_copy_and_metadata(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-advance-copy@example.com")
    _, draft = _create_draft(client, identity, monkeypatch, topic="Etching")
    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    ).json()
    goal_id = initialized["goal"]["goal_id"]
    old_plan_id = initialized["diagnosis"]["active_plan_id"]
    with session_factory() as session:
        session.add(
            PlanAdjustmentRecord(
                id="adjustment-dynamic-advance-copy",
                user_id=identity["user_id"],
                goal_id=goal_id,
                previous_plan_id=old_plan_id,
                trigger_type="manual",
                decision="advance",
                evidence_json={},
                before_snapshot={},
                after_snapshot={},
                plan_patch={"unlock_next_nodes": True},
                change_summary={},
                rationale_json={},
                status="proposed",
                base_plan_version=1,
            )
        )
        session.commit()
        applied = apply_plan_adjustment(
            session,
            adjustment_id="adjustment-dynamic-advance-copy",
            user_id=identity["user_id"],
            goal_id=goal_id,
            locale="zh-CN",
        )
        practice = session.scalar(
            select(PlanTask).where(
                PlanTask.plan_id == applied["new_plan_id"],
                PlanTask.task_type == "practice",
            )
        )
        node = session.get(KnowledgeNode, practice.knowledge_node_id)
        assert practice.title == node.title
        assert practice.objective == node.metadata_json["objective"]
        for key in ("locale", "stage_id", "stage_order", "node_id", "node_order"):
            assert practice.payload[key] == node.metadata_json[key]


def test_active_advance_revisit_takes_precedence_over_historical_completion(
    client, session_factory, monkeypatch
) -> None:
    identity = register_user(client, email="roadmap-active-revisit@example.com")
    _, draft = _create_draft(client, identity, monkeypatch, topic="Stone carving")
    initialized = client.post(
        "/api/onboarding/initialize-from-draft",
        headers=identity["headers"],
        json={
            "request_id": str(uuid4()),
            "draft_id": draft["draft_id"],
            "knowledge_answers": _answers(draft),
        },
    ).json()
    goal_id = initialized["goal"]["goal_id"]
    old_plan_id = initialized["diagnosis"]["active_plan_id"]

    with session_factory() as session:
        old_tasks = list(
            session.scalars(
                select(PlanTask)
                .where(PlanTask.plan_id == old_plan_id)
                .order_by(PlanTask.scheduled_day)
            )
        )
        for task in old_tasks:
            task.status = "completed"
        revisited_mastery = session.scalar(
            select(MasteryRecord).where(
                MasteryRecord.user_id == identity["user_id"],
                MasteryRecord.goal_id == goal_id,
                MasteryRecord.knowledge_node_id == old_tasks[-1].knowledge_node_id,
            )
        )
        revisited_mastery.mastery_score = 35
        session.add(
            PlanAdjustmentRecord(
                id="adjustment-active-revisit",
                user_id=identity["user_id"],
                goal_id=goal_id,
                previous_plan_id=old_plan_id,
                trigger_type="manual",
                decision="advance",
                evidence_json={},
                before_snapshot={},
                after_snapshot={},
                plan_patch={"unlock_next_nodes": True},
                change_summary={},
                rationale_json={},
                status="proposed",
                base_plan_version=1,
            )
        )
        session.commit()
        applied = apply_plan_adjustment(
            session,
            adjustment_id="adjustment-active-revisit",
            user_id=identity["user_id"],
            goal_id=goal_id,
            locale="en-US",
        )
        practice = session.scalar(
            select(PlanTask).where(
                PlanTask.plan_id == applied["new_plan_id"],
                PlanTask.task_type == "practice",
            )
        )
        practice_id = practice.id
        revisited_node_id = practice.knowledge_node_id

    roadmap = client.get(
        "/api/state/current",
        headers=identity["headers"],
        params={"goal_id": goal_id},
    ).json()["roadmap"]
    nodes = [node for stage in roadmap["stages"] for node in stage["nodes"]]
    revisited = next(node for node in nodes if node["knowledge_node_id"] == revisited_node_id)

    assert [stage["status"] for stage in roadmap["stages"]] == [
        "completed",
        "completed",
        "current",
    ]
    assert [node["status"] for node in nodes[:-1]] == ["completed", "completed"]
    assert revisited["task_id"] == practice_id
    assert revisited["status"] == "current"
    assert revisited["progress"] == 0.35
