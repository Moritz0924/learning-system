from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from backend.app.application.config_service import (
    RuntimeResolver,
    embedding_profile_identity,
    environment_embedding_identity,
)
from backend.app.application.document_index_service import (
    DocumentIndexService,
    document_index_build_key,
    embedding_client_identity,
)
from backend.app.infrastructure.secrets import SecretStore
from backend.app.models import (
    Document,
    DocumentChunk,
    DocumentIndexVersion,
    OutboxEvent,
    UserCapabilityBinding,
    User,
    UserModelProfile,
)


EMBEDDING_REINDEX_EVENT_TYPE = "document.reindex_embedding"


@dataclass(frozen=True)
class EmbeddingReindexEventClaim:
    event_id: str
    lease_token: str


def enqueue_embedding_reindex_events(
    session: Session,
    *,
    user_id: str,
    model_profile_id: str | None,
    change_id: str | None = None,
    queued_profile_identity: str | None = None,
) -> int:
    documents = list(
        session.scalars(
            select(Document)
            .join(
                DocumentIndexVersion,
                (DocumentIndexVersion.document_id == Document.id)
                & (DocumentIndexVersion.status == "active"),
            )
            .where(
                Document.owner_user_id == user_id,
                Document.parse_status == "success",
            )
            .order_by(Document.id)
        )
    )
    if queued_profile_identity is None:
        if model_profile_id is None:
            queued_profile_identity = environment_embedding_identity()
        else:
            profile = session.scalar(
                select(UserModelProfile).where(
                    UserModelProfile.id == model_profile_id,
                    UserModelProfile.user_id == user_id,
                )
            )
            if profile is None:
                raise LookupError("embedding profile not found")
            queued_profile_identity = embedding_profile_identity(profile)
    dedupe_identity = change_id or queued_profile_identity
    created = 0
    for document in documents:
        dedupe_key = (
            f"{EMBEDDING_REINDEX_EVENT_TYPE}:{user_id}:{document.id}:{dedupe_identity}"
        )
        if session.scalar(select(OutboxEvent.id).where(OutboxEvent.dedupe_key == dedupe_key)):
            continue
        session.add(
            OutboxEvent(
                id=f"outbox-{uuid4()}",
                event_type=EMBEDDING_REINDEX_EVENT_TYPE,
                dedupe_key=dedupe_key,
                payload_json={
                    "user_id": user_id,
                    "document_id": document.id,
                    "model_profile_id": model_profile_id,
                    "embedding_profile_identity": queued_profile_identity,
                },
                status="pending",
            )
        )
        session.flush()
        created += 1
    return created


def process_embedding_reindex_event(
    session: Session,
    *,
    event_id: str,
    secret_store: SecretStore | None = None,
    resolver: object | None = None,
) -> dict:
    event = session.get(OutboxEvent, event_id)
    if event is None:
        raise LookupError("embedding reindex event not found")
    if event.event_type != EMBEDDING_REINDEX_EVENT_TYPE:
        raise ValueError("unexpected embedding reindex event type")
    if event.status == "succeeded":
        return {"event_id": event.id, "status": "succeeded", "already_processed": True}
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    user_id = payload.get("user_id")
    document_id = payload.get("document_id")
    model_profile_id = payload.get("model_profile_id")
    queued_profile_identity = payload.get("embedding_profile_identity")
    if (
        not isinstance(user_id, str)
        or not isinstance(document_id, str)
        or not isinstance(queued_profile_identity, str)
    ):
        return _fail_event(session, event, "embedding_reindex_invalid_payload")
    now = datetime.utcnow()
    claimed = session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event.id,
            or_(
                OutboxEvent.status.in_({"queued", "dispatched"}),
                and_(OutboxEvent.status == "pending", OutboxEvent.available_at <= now),
            ),
        )
        .values(
            status="processing",
            attempts=OutboxEvent.attempts + 1,
            dispatch_token=None,
            last_error=None,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.refresh(event)
        return {
            "event_id": event.id,
            "status": event.status,
            "already_processed": True,
        }
    session.refresh(event)
    try:
        if not _binding_matches(
            session,
            user_id,
            model_profile_id,
            queued_profile_identity,
            lock=False,
        ):
            return _stale_event(session, event)
        document = session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.owner_user_id == user_id,
                Document.parse_status == "success",
            )
        )
        active = session.scalar(
            select(DocumentIndexVersion).where(
                DocumentIndexVersion.document_id == document_id,
                DocumentIndexVersion.status == "active",
            )
        )
        if document is None or active is None:
            return _fail_event(session, event, "embedding_reindex_source_missing")
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.index_version_id == active.id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        if not chunks:
            return _fail_event(session, event, "embedding_reindex_source_missing")
        runtime = resolver or RuntimeResolver(
            session, user_id=user_id, secret_store=secret_store
        )
        embedding_client = runtime.resolve("embedding")
        provider, model, dimensions = embedding_client_identity(embedding_client)
        service = DocumentIndexService(session, embedding_client)
        candidate = service.build_index(
            user_id=user_id,
            document_id=document_id,
            build_key=document_index_build_key(
                document_sha256=document.sha256,
                chunker_version=active.chunker_version,
                embedding_provider=provider,
                embedding_model=model,
                embedding_dimensions=dimensions,
            ),
            chunks=[
                {"content": chunk.content, "metadata": dict(chunk.metadata_json or {})}
                for chunk in chunks
            ],
            chunker_version=active.chunker_version,
        )
        if candidate.status not in {"ready", "retired", "active"}:
            return _fail_event(session, event, "embedding_reindex_failed")
        session.expire_all()
        event = session.get(OutboxEvent, event_id)
        if not _binding_matches(
            session,
            user_id,
            model_profile_id,
            queued_profile_identity,
            lock=True,
        ):
            return _stale_event(session, event)
        if candidate.status != "active":
            service.activate_index(
                user_id=user_id,
                document_id=document_id,
                index_version_id=candidate.id,
            )
        event.status = "succeeded"
        event.last_error = None
        session.flush()
        return {"event_id": event.id, "status": "succeeded"}
    except Exception:
        session.rollback()
        event = session.get(OutboxEvent, event_id)
        return _fail_event(session, event, "embedding_reindex_failed")


def claim_pending_embedding_reindex_events(
    session: Session,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> list[EmbeddingReindexEventClaim]:
    current_time = now or datetime.utcnow()
    events = list(
        session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.event_type == EMBEDDING_REINDEX_EVENT_TYPE,
                OutboxEvent.status.in_({"pending", "queued", "dispatched"}),
                OutboxEvent.available_at <= current_time,
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
            .limit(max(1, limit))
            .with_for_update(skip_locked=True)
        )
    )
    lease_until = current_time + timedelta(seconds=_embedding_reindex_dispatch_lease_seconds())
    claims: list[EmbeddingReindexEventClaim] = []
    for event in events:
        lease_token = f"dispatch-{uuid4()}"
        event.status = "queued"
        event.available_at = lease_until
        event.dispatch_token = lease_token
        event.last_error = None
        claims.append(EmbeddingReindexEventClaim(event_id=event.id, lease_token=lease_token))
    session.flush()
    return claims


def release_embedding_reindex_event(
    session: Session,
    *,
    event_id: str,
    lease_token: str,
    error_type: str,
) -> bool:
    released = session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id,
            OutboxEvent.status == "queued",
            OutboxEvent.dispatch_token == lease_token,
        )
        .values(
            status="pending",
            available_at=datetime.utcnow(),
            dispatch_token=None,
            last_error=f"embedding reindex dispatch failed: {error_type}",
        )
        .execution_options(synchronize_session=False)
    )
    return released.rowcount == 1


def _embedding_reindex_dispatch_lease_seconds() -> int:
    try:
        return max(1, int(os.getenv("EMBEDDING_REINDEX_DISPATCH_LEASE_SECONDS", "300")))
    except ValueError:
        return 300


def _binding_matches(
    session: Session,
    user_id: str,
    model_profile_id: str | None,
    queued_profile_identity: str,
    *,
    lock: bool,
) -> bool:
    if lock:
        session.scalar(select(User.id).where(User.id == user_id).with_for_update())
    statement = select(UserCapabilityBinding).where(
        UserCapabilityBinding.user_id == user_id,
        UserCapabilityBinding.capability == "embedding",
    )
    if lock:
        statement = statement.with_for_update()
    binding = session.scalar(statement)
    if binding is None:
        return (
            model_profile_id is None
            and environment_embedding_identity() == queued_profile_identity
        )
    if binding.model_profile_id != model_profile_id:
        return False
    profile = session.scalar(
        select(UserModelProfile).where(
            UserModelProfile.id == binding.model_profile_id,
            UserModelProfile.user_id == user_id,
        )
    )
    return (
        profile is not None
        and profile.enabled
        and embedding_profile_identity(profile) == queued_profile_identity
    )


def _stale_event(session: Session, event: OutboxEvent) -> dict:
    event.status = "succeeded"
    event.last_error = "stale_binding"
    session.flush()
    return {"event_id": event.id, "status": "stale"}


def _fail_event(session: Session, event: OutboxEvent, code: str) -> dict:
    event.status = "failed"
    event.last_error = code
    session.flush()
    return {"event_id": event.id, "status": "failed", "code": code}


__all__ = [
    "EMBEDDING_REINDEX_EVENT_TYPE",
    "EmbeddingReindexEventClaim",
    "claim_pending_embedding_reindex_events",
    "enqueue_embedding_reindex_events",
    "process_embedding_reindex_event",
    "release_embedding_reindex_event",
]
