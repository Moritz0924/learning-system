from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, String, Text, UniqueConstraint, cast, event, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

from .db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


def utcnow_aware() -> datetime:
    return datetime.now(timezone.utc)


class _PGVector1536(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "vector(1536)"

    def bind_expression(self, bindvalue):
        return cast(bindvalue, self)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="learner")
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


@event.listens_for(User, "before_insert")
def _derive_normalized_email(_, __, user: User) -> None:
    if not user.normalized_email:
        user.normalized_email = user.email.strip().lower()


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_status", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow_aware)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow_aware)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String, nullable=True)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_session_expires", "session_id", "expires_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("auth_sessions.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    parent_token_id: Mapped[str | None] = mapped_column(String, ForeignKey("refresh_tokens.id"), nullable=True)
    replaced_by_token_id: Mapped[str | None] = mapped_column(String, ForeignKey("refresh_tokens.id"), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow_aware)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reuse_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LearnerProfile(Base):
    __tablename__ = "learner_profiles"

    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), primary_key=True)
    weekly_hours: Mapped[int] = mapped_column(Integer, default=10)
    available_slots: Mapped[dict] = mapped_column(JSON, default=dict)
    learning_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    baseline_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    privacy_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Curriculum(Base):
    __tablename__ = "curricula"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    version: Mapped[str] = mapped_column(String, default="v1")
    title: Mapped[str] = mapped_column(String)
    domain: Mapped[str] = mapped_column(String, default="ai_app_dev")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    curriculum_id: Mapped[str] = mapped_column(String, ForeignKey("curricula.id"))
    code: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    sequence: Mapped[int] = mapped_column(Integer)
    node_type: Mapped[str] = mapped_column(String, default="concept")
    difficulty: Mapped[int] = mapped_column(Integer, default=2)
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=45)
    mastery_threshold: Mapped[float] = mapped_column(Float, default=70)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        UniqueConstraint(
            "curriculum_id",
            "from_node_id",
            "to_node_id",
            "relation_type",
            name="uq_knowledge_edges_relation",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    curriculum_id: Mapped[str] = mapped_column(String, ForeignKey("curricula.id"))
    from_node_id: Mapped[str] = mapped_column(String, ForeignKey("knowledge_nodes.id"))
    to_node_id: Mapped[str] = mapped_column(String, ForeignKey("knowledge_nodes.id"))
    relation_type: Mapped[str] = mapped_column(String, default="prerequisite")


class LearningGoal(Base):
    __tablename__ = "learning_goals"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "id",
            name="uq_learning_goals_user_id_id",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String)
    domain: Mapped[str] = mapped_column(String, default="ai_app_dev")
    target_outcome: Mapped[str] = mapped_column(Text)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    weekly_hours_target: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="active")
    learning_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BaselineDiagnostic(Base):
    __tablename__ = "baseline_diagnostics"
    __table_args__ = (
        Index(
            "uq_baseline_diagnostics_user_request_id",
            "user_id",
            "request_id",
            unique=True,
            sqlite_where=text("request_id IS NOT NULL"),
            postgresql_where=text("request_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy_unversioned")
    template_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    submitted_answers: Mapped[dict] = mapped_column(JSON, default=dict)
    baseline_summary: Mapped[str] = mapped_column(Text)
    entry_node_id: Mapped[str | None] = mapped_column(String, ForeignKey("knowledge_nodes.id"), nullable=True)
    knowledge_gaps: Mapped[list] = mapped_column(JSON, default=list)
    initial_mastery: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningPlan(Base):
    __tablename__ = "learning_plans"
    __table_args__ = (
        UniqueConstraint("user_id", "goal_id", "version", name="uq_learning_plans_user_goal_version"),
        Index(
            "uq_learning_plans_active_user_goal",
            "user_id",
            "goal_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    curriculum_id: Mapped[str | None] = mapped_column(String, ForeignKey("curricula.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="active")
    generated_by: Mapped[str] = mapped_column(String, default="planner")
    rationale_json: Mapped[dict] = mapped_column(JSON, default=dict)
    valid_from: Mapped[date] = mapped_column(Date, default=date.today)
    valid_to: Mapped[date] = mapped_column(Date, default=date.today)
    plan_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tasks: Mapped[list["PlanTask"]] = relationship(cascade="all, delete-orphan")


class PlanTask(Base):
    __tablename__ = "plan_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("learning_plans.id"))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    knowledge_node_id: Mapped[str] = mapped_column(String, ForeignKey("knowledge_nodes.id"))
    knowledge_node_code: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    task_type: Mapped[str] = mapped_column(String, default="study")
    objective: Mapped[str] = mapped_column(Text, default="")
    scheduled_date: Mapped[date] = mapped_column(Date, default=date.today)
    scheduled_day: Mapped[int] = mapped_column(Integer, default=1)
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=45)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="pending")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    origin: Mapped[str] = mapped_column(String, default="planner")


class LearningSession(Base):
    __tablename__ = "learning_sessions"
    __table_args__ = (
        Index(
            "uq_learning_sessions_active_user_task",
            "user_id",
            "task_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("learning_plans.id"))
    task_id: Mapped[str] = mapped_column(String, ForeignKey("plan_tasks.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="active")
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)


class LearningEvent(Base):
    __tablename__ = "learning_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    session_id: Mapped[str | None] = mapped_column(String, ForeignKey("learning_sessions.id"), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String, ForeignKey("plan_tasks.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    event_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningStateSnapshot(Base):
    __tablename__ = "learning_state_snapshots"
    __table_args__ = (
        UniqueConstraint("user_id", "goal_id", name="uq_learning_state_snapshots_user_goal"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    active_plan_id: Mapped[str] = mapped_column(String, ForeignKey("learning_plans.id"))
    active_plan_version: Mapped[int] = mapped_column(Integer)
    baseline_diagnostic_id: Mapped[str] = mapped_column(String, ForeignKey("baseline_diagnostics.id"))
    phase_assessment_state_id: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_plan_adjustment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    mastery_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    current_state: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_from: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MasteryRecord(Base):
    __tablename__ = "mastery_records"
    __table_args__ = (
        UniqueConstraint("user_id", "goal_id", "knowledge_node_id", name="uq_mastery_user_goal_node"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    knowledge_node_id: Mapped[str] = mapped_column(String, ForeignKey("knowledge_nodes.id"))
    mastery_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer, default=1)
    source_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    plan_id: Mapped[str | None] = mapped_column(String, ForeignKey("learning_plans.id"), nullable=True)
    assessment_type: Mapped[str] = mapped_column(String)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rubric_version: Mapped[str] = mapped_column(String, default="phase2-rubric-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list["AssessmentItem"]] = relationship(cascade="all, delete-orphan")


class AssessmentItem(Base):
    __tablename__ = "assessment_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    assessment_id: Mapped[str] = mapped_column(String, ForeignKey("assessments.id"))
    knowledge_node_id: Mapped[str] = mapped_column(String, ForeignKey("knowledge_nodes.id"))
    question_type: Mapped[str] = mapped_column(String)
    prompt: Mapped[str] = mapped_column(Text)
    options_json: Mapped[dict] = mapped_column(JSON, default=dict)
    reference_answer: Mapped[str] = mapped_column(Text)
    rubric_json: Mapped[dict] = mapped_column(JSON, default=dict)
    difficulty: Mapped[int] = mapped_column(Integer, default=2)
    source_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    assessment_id: Mapped[str] = mapped_column(String, ForeignKey("assessments.id"))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    score: Mapped[float] = mapped_column(Float)
    feedback: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="graded")

    answers: Mapped[list["AssessmentAnswer"]] = relationship(cascade="all, delete-orphan")


class AssessmentAnswer(Base):
    __tablename__ = "assessment_answers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String, ForeignKey("assessment_attempts.id"))
    item_id: Mapped[str] = mapped_column(String, ForeignKey("assessment_items.id"))
    answer_text: Mapped[str] = mapped_column(Text)
    answer_json: Mapped[dict] = mapped_column(JSON, default=dict)
    score: Mapped[float] = mapped_column(Float)
    grader_type: Mapped[str] = mapped_column(String, default="rule")
    grader_reason: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)


class PlanAdjustmentRecord(Base):
    __tablename__ = "plan_adjustments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    previous_plan_id: Mapped[str | None] = mapped_column(String, ForeignKey("learning_plans.id"), nullable=True)
    new_plan_id: Mapped[str | None] = mapped_column(String, ForeignKey("learning_plans.id"), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    before_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    after_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    plan_patch: Mapped[dict] = mapped_column(JSON, default=dict)
    change_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    rationale_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="proposed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PhaseAssessmentState(Base):
    __tablename__ = "phase_assessment_states"
    __table_args__ = (
        UniqueConstraint("user_id", "goal_id", "phase_code", name="uq_phase_assessment_user_goal_phase"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    assessment_id: Mapped[str | None] = mapped_column(String, ForeignKey("assessments.id"), nullable=True)
    phase_code: Mapped[str] = mapped_column(String)
    covered_node_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="draft")
    readiness_score: Mapped[float] = mapped_column(Float, default=0)
    last_result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    next_action: Mapped[str] = mapped_column(String, default="review")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_user_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True)
    corpus_type: Mapped[str] = mapped_column(String, default="user_uploaded")
    filename: Mapped[str] = mapped_column(String)
    object_key: Mapped[str] = mapped_column(String)
    mime_type: Mapped[str] = mapped_column(String)
    parse_status: Mapped[str] = mapped_column(String, default="pending")
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    parse_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sha256: Mapped[str] = mapped_column(String)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    trusted_level: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("documents.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    embedding_vector: Mapped[str | None] = mapped_column(Text().with_variant(_PGVector1536(), "postgresql"), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    citation_label: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "goal_id"],
            ["learning_goals.user_id", "learning_goals.id"],
            name="fk_memories_user_goal",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_memories_user_idempotency",
        ),
        CheckConstraint(
            "importance >= 0 AND importance <= 1",
            name="ck_memories_importance_range",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memories_confidence_range",
        ),
        Index("ix_memories_user_scope_type", "user_id", "goal_id", "memory_type"),
        Index("ix_memories_user_enabled_expiry", "user_id", "is_enabled", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    goal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    memory_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="memory-v1")
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow_aware)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow_aware, onupdate=utcnow_aware
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_dispatch_due", "event_type", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String)
    dedupe_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    dispatch_token: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ConversationThread(Base):
    __tablename__ = "conversation_threads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "goal_id"],
            ["learning_goals.user_id", "learning_goals.id"],
            name="fk_conversation_threads_user_goal",
        ),
        UniqueConstraint(
            "user_id",
            "goal_id",
            "id",
            name="uq_conversation_threads_user_goal_id",
        ),
        UniqueConstraint(
            "user_id",
            "goal_id",
            "legacy_key",
            name="uq_conversation_threads_user_goal_legacy_key",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_conversation_threads_status",
        ),
        Index(
            "ix_conversation_threads_user_goal_status",
            "user_id",
            "goal_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    goal_id: Mapped[str] = mapped_column(String, nullable=False)
    legacy_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow_aware
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow_aware, onupdate=utcnow_aware
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "goal_id", "thread_id"],
            [
                "conversation_threads.user_id",
                "conversation_threads.goal_id",
                "conversation_threads.id",
            ],
            name="fk_agent_runs_conversation_thread",
        ),
        Index(
            "uq_agent_runs_active_thread",
            "thread_id",
            unique=True,
            sqlite_where=text("status IN ('running', 'cancellation_requested')"),
            postgresql_where=text("status IN ('running', 'cancellation_requested')"),
        ),
        Index("ix_agent_runs_user_thread_created", "user_id", "thread_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    thread_id: Mapped[str] = mapped_column(String)
    correlation_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    graph_name: Mapped[str] = mapped_column(String)
    graph_version: Mapped[str] = mapped_column(String)
    trigger_type: Mapped[str] = mapped_column(String)
    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    output_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    node_trace: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow_aware
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_run_id: Mapped[str | None] = mapped_column(String, ForeignKey("agent_runs.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String)
    request_hash: Mapped[str] = mapped_column(String)
    response_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    source_urls: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
