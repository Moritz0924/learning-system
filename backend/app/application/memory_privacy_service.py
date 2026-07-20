from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domain.memory import MemoryPrivacySettings, MemoryScopeNotFound
from backend.app.models import LearnerProfile


LONG_TERM_MEMORY_PRIVACY_KEY = "long_term_memory"


class MemoryPrivacyConfigurationError(ValueError):
    pass


@dataclass
class MemoryPrivacyService:
    session: Session

    def get(self, *, user_id: str, for_update: bool = False) -> MemoryPrivacySettings:
        statement = select(LearnerProfile).where(LearnerProfile.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        profile = self.session.scalar(statement)
        if profile is None:
            raise MemoryScopeNotFound("Memory privacy profile was not found.")
        return parse_memory_privacy_settings(profile.privacy_settings)

    def update(
        self,
        *,
        user_id: str,
        settings: MemoryPrivacySettings,
    ) -> MemoryPrivacySettings:
        statement = (
            select(LearnerProfile)
            .where(LearnerProfile.user_id == user_id)
            .with_for_update()
        )
        profile = self.session.scalar(statement)
        if profile is None:
            raise MemoryScopeNotFound("Memory privacy profile was not found.")
        stored = dict(profile.privacy_settings or {})
        stored[LONG_TERM_MEMORY_PRIVACY_KEY] = settings.model_dump()
        profile.privacy_settings = stored
        self.session.flush()
        return settings


def parse_memory_privacy_settings(raw_privacy: object) -> MemoryPrivacySettings:
    if raw_privacy is None:
        raw_privacy = {}
    if not isinstance(raw_privacy, dict):
        raise MemoryPrivacyConfigurationError("Stored memory privacy settings are invalid.")
    if LONG_TERM_MEMORY_PRIVACY_KEY not in raw_privacy:
        return MemoryPrivacySettings()
    raw_settings = raw_privacy[LONG_TERM_MEMORY_PRIVACY_KEY]
    if not isinstance(raw_settings, dict):
        raise MemoryPrivacyConfigurationError("Stored memory privacy settings are invalid.")
    try:
        return MemoryPrivacySettings.model_validate(raw_settings)
    except ValidationError as error:
        raise MemoryPrivacyConfigurationError("Stored memory privacy settings are invalid.") from error
