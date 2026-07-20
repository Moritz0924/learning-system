from __future__ import annotations

import pytest

from backend.app.application.memory_privacy_service import (
    LONG_TERM_MEMORY_PRIVACY_KEY,
    MemoryPrivacyConfigurationError,
    MemoryPrivacyService,
)
from backend.app.domain.memory import MemoryPrivacySettings
from backend.app.models import LearnerProfile

from .helpers import add_memory_scope


def test_missing_long_term_memory_privacy_uses_contract_defaults(db_session) -> None:
    user_id, _ = add_memory_scope(db_session)
    db_session.add(LearnerProfile(user_id=user_id, privacy_settings={"data_scope": "v1"}))
    db_session.flush()

    settings = MemoryPrivacyService(db_session).get(user_id=user_id)

    assert settings == MemoryPrivacySettings()


def test_privacy_update_preserves_unrelated_json_keys(db_session) -> None:
    user_id, _ = add_memory_scope(db_session)
    profile = LearnerProfile(
        user_id=user_id,
        privacy_settings={"data_scope": "v1", "analytics": {"enabled": False}},
    )
    db_session.add(profile)
    db_session.flush()
    settings = MemoryPrivacySettings(
        enabled=False,
        allow_explicit_user=False,
        allow_system_inference=True,
        allow_learning_results=False,
    )

    updated = MemoryPrivacyService(db_session).update(user_id=user_id, settings=settings)

    assert updated == settings
    assert profile.privacy_settings == {
        "data_scope": "v1",
        "analytics": {"enabled": False},
        LONG_TERM_MEMORY_PRIVACY_KEY: settings.model_dump(),
    }
    assert db_session.in_transaction()


@pytest.mark.parametrize(
    "stored",
    [
        "enabled",
        {"enabled": "yes"},
        {"unknown": True},
        {"enabled": True, "allow_explicit_user": None},
    ],
)
def test_malformed_stored_privacy_fails_closed(db_session, stored) -> None:
    user_id, _ = add_memory_scope(db_session)
    db_session.add(
        LearnerProfile(
            user_id=user_id,
            privacy_settings={LONG_TERM_MEMORY_PRIVACY_KEY: stored},
        )
    )
    db_session.flush()

    with pytest.raises(MemoryPrivacyConfigurationError):
        MemoryPrivacyService(db_session).get(user_id=user_id)
