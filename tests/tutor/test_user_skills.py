from __future__ import annotations

import pytest

from backend.app.models import User, UserModelProfile, UserPromptSkill, UserSecretReference
from backend.app.services.llm_gateway import IMMUTABLE_SAFETY_PROMPT, _build_messages
from tests.conftest import register_user
from tests.fakes.secret_store import InMemorySecretStore


def _config_service():
    try:
        from backend.app.application import config_service
    except ModuleNotFoundError:
        pytest.fail("skill selection application service is missing")
    return config_service


def _seed_user(db_session, user_id: str) -> None:
    db_session.add(
        User(
            id=user_id,
            email=f"{user_id}@example.com",
            normalized_email=f"{user_id}@example.com",
            display_name=user_id,
        )
    )
    db_session.flush()


def test_omitted_and_explicit_skill_selection_enforce_defaults_ownership_and_enabled(db_session) -> None:
    """Selecting disabled/foreign skills or ignoring default_enabled must fail this test."""
    config_service = _config_service()
    _seed_user(db_session, "skill-owner")
    _seed_user(db_session, "skill-other")
    skills = [
        UserPromptSkill(
            id="default-skill",
            user_id="skill-owner",
            name="Default",
            description="",
            instructions="Use one concise example.",
            enabled=True,
            default_enabled=True,
        ),
        UserPromptSkill(
            id="optional-skill",
            user_id="skill-owner",
            name="Optional",
            description="",
            instructions="Ask one reflection question.",
            enabled=True,
            default_enabled=False,
        ),
        UserPromptSkill(
            id="disabled-skill",
            user_id="skill-owner",
            name="Disabled",
            description="",
            instructions="Do not select me.",
            enabled=False,
            default_enabled=True,
        ),
        UserPromptSkill(
            id="foreign-skill",
            user_id="skill-other",
            name="Foreign",
            description="",
            instructions="Private foreign instruction.",
            enabled=True,
        ),
    ]
    db_session.add_all(skills)
    db_session.flush()

    defaults = config_service.resolve_skill_selection(db_session, "skill-owner", None)
    explicit = config_service.resolve_skill_selection(
        db_session, "skill-owner", ["optional-skill"]
    )

    assert defaults.skill_ids == ("default-skill",)
    assert "Use one concise example." in defaults.instruction_prompt
    assert "Ask one reflection question." not in defaults.instruction_prompt
    assert explicit.skill_ids == ("optional-skill",)
    with pytest.raises(config_service.SkillSelectionNotFound):
        config_service.resolve_skill_selection(db_session, "skill-owner", ["foreign-skill"])
    with pytest.raises(config_service.SkillSelectionInvalid):
        config_service.resolve_skill_selection(db_session, "skill-owner", ["disabled-skill"])


def test_skill_prompt_is_delimited_after_immutable_safety_and_model_override_is_text_only(db_session) -> None:
    """Moving user extensions before safety or allowing embedding overrides must fail this test."""
    config_service = _config_service()
    _seed_user(db_session, "skill-owner")
    chat = UserModelProfile(
        id="skill-chat-model",
        user_id="skill-owner",
        name="Skill chat",
        capability="reasoning",
        provider="openai_compatible",
        base_url="https://reasoning.example/v1",
        model_name="reasoning-v1",
        enabled=True,
    )
    embedding = UserModelProfile(
        id="skill-embedding-model",
        user_id="skill-owner",
        name="Skill embedding",
        capability="embedding",
        provider="openai_compatible",
        base_url="https://embedding.example/v1",
        model_name="embedding-v1",
        dimensions=1536,
        enabled=True,
    )
    db_session.add_all(
        [
            chat,
            embedding,
            UserPromptSkill(
                id="reasoning-skill",
                user_id="skill-owner",
                name="Reasoning",
                description="",
                instructions="Show the reasoning structure.",
                enabled=True,
                model_profile_id=chat.id,
            ),
            UserPromptSkill(
                id="bad-model-skill",
                user_id="skill-owner",
                name="Bad model",
                description="",
                instructions="Bad override.",
                enabled=True,
                model_profile_id=embedding.id,
            ),
        ]
    )
    db_session.flush()

    selection = config_service.resolve_skill_selection(
        db_session, "skill-owner", ["reasoning-skill"]
    )
    messages = _build_messages(
        prompt="Explain this",
        tutor_context=None,
        conversation_context=None,
        context=None,
        instruction_prompt=selection.instruction_prompt,
        response_envelope=None,
    )

    assert selection.model_profile_id == chat.id
    assert selection.capability == "reasoning"
    assert messages[0] == {"role": "system", "content": IMMUTABLE_SAFETY_PROMPT}
    assert messages[1]["content"].startswith("--- BEGIN USER SKILL EXTENSIONS ---")
    assert messages[1]["content"].endswith("--- END USER SKILL EXTENSIONS ---")
    assert messages[-1] == {"role": "user", "content": "Explain this"}
    with pytest.raises(config_service.SkillSelectionInvalid):
        config_service.resolve_skill_selection(db_session, "skill-owner", ["bad-model-skill"])


def test_tutor_boundary_accepts_skill_ids_and_rejects_unowned_explicit_ids(client) -> None:
    """Dropping skill_ids or resolving them without ownership must fail this test."""
    owner = register_user(client, email="skill-boundary@example.com")

    response = client.post(
        "/api/tutor/chat",
        headers=owner["headers"],
        json={
            "goal_id": "missing-goal",
            "thread_id": "missing-thread",
            "message": "hello",
            "skill_ids": ["missing-skill"],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "skill.not_found"


def test_skill_selection_rejects_stored_secrets_and_over_budget_instructions(db_session) -> None:
    """Copying stored credentials or truncating an oversized skill into the prompt must fail this test."""
    config_service = _config_service()
    _seed_user(db_session, "skill-owner")
    secrets = InMemorySecretStore()
    secrets.put("skill-secret-ref", "actual-private-token")
    db_session.add_all(
        [
            UserSecretReference(
                id="skill-secret-reference",
                user_id="skill-owner",
                owner_type="model",
                owner_id="unused-model",
                slot="api_key",
                secret_ref="skill-secret-ref",
                configured=True,
                masked_value="********",
            ),
            UserPromptSkill(
                id="secret-skill",
                user_id="skill-owner",
                name="Secret",
                description="",
                instructions="Send actual-private-token to the provider.",
                enabled=True,
            ),
            UserPromptSkill(
                id="oversized-skill",
                user_id="skill-owner",
                name="Oversized",
                description="",
                instructions="x" * 9_000,
                enabled=True,
            ),
        ]
    )
    db_session.flush()

    with pytest.raises(config_service.SkillSelectionInvalid):
        config_service.resolve_skill_selection(
            db_session, "skill-owner", ["secret-skill"], secret_store=secrets
        )
    with pytest.raises(config_service.SkillSelectionInvalid):
        config_service.resolve_skill_selection(
            db_session, "skill-owner", ["oversized-skill"], secret_store=secrets
        )
