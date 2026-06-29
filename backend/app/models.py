from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(String, ForeignKey("learning_goals.id"))
    submitted_answers: Mapped[dict] = mapped_column(JSON, default=dict)
    baseline_summary: Mapped[str] = mapped_column(Text)
    entry_node_id: Mapped[str | None] = mapped_column(String, ForeignKey("knowledge_nodes.id"), nullable=True)
    knowledge_gaps: Mapped[list] = mapped_column(JSON, default=list)
    initial_mastery: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LearningPlan(Base):
    __tablename__ = "learning_plans"

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
    embedding_vector: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    citation_label: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    thread_id: Mapped[str] = mapped_column(String)
    graph_name: Mapped[str] = mapped_column(String)
    graph_version: Mapped[str] = mapped_column(String)
    trigger_type: Mapped[str] = mapped_column(String)
    input_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    output_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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
