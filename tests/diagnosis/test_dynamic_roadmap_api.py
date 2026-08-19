from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from backend.app import models
from backend.app.application.onboarding_service import OnboardingService
from backend.app.application.planning_service import apply_plan_adjustment
from backend.app.models import (
    BaselineDiagnostic,
    Curriculum,
    KnowledgeNode,
    LearningGoal,
    LearningPlan,
    MasteryRecord,
    PlanAdjustmentRecord,
    PlanTask,
)
from backend.app.application.config_service import RuntimeResolutionError
from tests.conftest import register_user
from tests.diagnosis.helpers import initialize_payload


def _goal(topic: str = "Byzantine mosaics") -> dict:
    return {
        "title": f"Master {topic}",
        "target_outcome": f"Create and explain a complete portfolio about {topic}.",
        "deadline": "2026-12-31",
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


def _install_fake_runtime(monkeypatch, outputs: list[str]) -> None:
    remaining = list(outputs)

    class FakeClient:
        def complete(self, **kwargs) -> str:
            assert kwargs["role"] == "planner"
            return remaining.pop(0)

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


def test_model_unavailable_returns_scrubbed_error_without_plan(client, session_factory, monkeypatch) -> None:
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
        "code": "onboarding.dynamic_model_unavailable",
        "message": "Dynamic learning setup is unavailable.",
    }
    assert "credential" not in response.text.lower()
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(models.UserDiagnosticDraft)) == 0
        assert session.scalar(select(func.count()).select_from(LearningPlan)) == 0


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
    assert response.json()["detail"]["code"] == "onboarding.dynamic_model_invalid"
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
    assert response.json()["detail"]["code"] == "onboarding.dynamic_model_invalid"
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
    assert response.json()["detail"]["code"] == "onboarding.dynamic_model_invalid"


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
    assert response.json()["detail"]["code"] == "onboarding.dynamic_model_invalid"


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
