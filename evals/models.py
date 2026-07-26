"""Versioned data contracts for the offline LLM/RAG evaluation system."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from adaptive_tutor.phase2.telemetry import RetrievalScore, TimedLlmResult, TimedRetrievalResult


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSpan(StrictModel):
    evidence_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    section: str | None = None
    text: str = Field(min_length=1)


class EvaluationConversationMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8192)


class FormatContract(StrictModel):
    type: Literal[
        "free_text",
        "explanation_with_citations",
        "step_by_step",
        "comparison",
        "insufficient_evidence",
        "strict_json",
    ]
    required_sections: list[str] = Field(default_factory=list)
    required_json_schema: dict[str, Any] | None = None
    max_bullets: int | None = Field(default=None, ge=1)
    require_citations: bool = False
    forbidden_fields: list[str] = Field(default_factory=list)


class LearningQaEvaluationCase(StrictModel):
    case_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    split: Literal["development", "test"]
    category: Literal[
        "single_source",
        "paraphrase",
        "multi_evidence",
        "unanswerable",
        "prompt_injection",
        "multi_turn",
    ]
    difficulty: Literal["easy", "medium", "hard"]
    question: str = Field(min_length=1)
    conversation_history: list[EvaluationConversationMessage] = Field(default_factory=list)
    gold_answer_points: list[str] = Field(default_factory=list)
    gold_document_ids: list[str] = Field(default_factory=list)
    gold_evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    acceptable_alternative_document_ids: list[str] = Field(default_factory=list)
    is_answerable: bool
    expected_behavior: Literal["answer_with_citation", "abstain"]
    format_contract: FormatContract
    tags: list[str] = Field(default_factory=list)


class CorpusDocument(StrictModel):
    document_id: str
    filename: str
    title: str
    source_type: Literal["markdown", "text"]
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CorpusManifest(StrictModel):
    corpus_version: str
    documents: list[CorpusDocument]


class GoldEvidenceGroup(StrictModel):
    evidence_id: str
    document_id: str
    acceptable_chunk_ids: set[str] = Field(min_length=1)


class GoldChunkMapCase(StrictModel):
    evidence_groups: list[GoldEvidenceGroup]


class GoldChunkMap(StrictModel):
    dataset_version: str
    corpus_hash: str
    chunking_config_hash: str
    cases: dict[str, GoldChunkMapCase]


class EvaluationExecutionStatus(StrictModel):
    status: Literal[
        "completed",
        "retrieval_error",
        "llm_error",
        "parse_error",
        "judge_error",
        "skipped_dependency",
    ]
    retrieval_attempt_count: int = Field(ge=0)
    llm_attempt_count: int = Field(ge=0)
    judge_attempt_count: int = Field(ge=0)
    degraded_mode_used: bool = False
    error_code: str | None = None


class RetrievalEvaluationResult(StrictModel):
    retrieved_chunk_ids: list[str]
    retrieved_document_ids: list[str]
    retrieval_scores: list[RetrievalScore]
    document_hit_at: dict[int, float]
    document_recall_at: dict[int, float]
    chunk_hit_at: dict[int, float]
    evidence_recall_at: dict[int, float]
    all_evidence_hit_at: dict[int, float]
    retrieval_latency_ms: float = Field(ge=0)
    embedding_latency_ms: float | None = Field(default=None, ge=0)
    vector_search_latency_ms: float | None = Field(default=None, ge=0)
    retrieval_postprocess_latency_ms: float = Field(default=0.0, ge=0)


class AnswerEvaluationResult(StrictModel):
    raw_output: str
    answer_text: str | None
    cited_chunk_ids: list[str]
    cited_document_ids: list[str]
    citation_count: int = Field(ge=0)
    valid_reference_count: int = Field(ge=0)
    invalid_reference_count: int = Field(ge=0)
    citation_reference_validity_rate: float | None = Field(default=None, ge=0, le=1)
    citation_support_rate: float | None = Field(default=None, ge=0, le=1)
    citation_semantically_graded_count: int = Field(ge=0)
    contains_unsupported_claim: bool | None
    correctly_abstained: bool | None
    format_followed: bool
    json_parse_success: bool = False
    required_sections_present: bool = False
    citation_format_valid: bool = False
    abstention_format_correct: bool | None = None
    forbidden_field_detected: bool = False
    llm_request_latency_ms: float = Field(default=0.0, ge=0)
    llm_parse_latency_ms: float = Field(default=0.0, ge=0)
    answer_latency_ms: float = Field(ge=0)
    end_to_end_latency_ms: float = Field(ge=0)


class EvaluationCaseResult(StrictModel):
    run_id: str
    case_id: str
    repeat_index: int = 0
    dataset_version: str
    prompt_variant: str
    prompt_sha256: str
    model: str
    retrieval_config_hash: str
    execution: EvaluationExecutionStatus
    retrieval: RetrievalEvaluationResult
    answer: AnswerEvaluationResult
    grader_mode: Literal["automatic", "llm_judge", "human", "human_override"]
    judge_reason: str | None = None
    judge_result: JudgeVerdict | None = None
    human_override_result: JudgeVerdict | None = None
    human_override_reason: str | None = None
    human_reviewer: str | None = None


class PromptVariant(StrictModel):
    name: str
    content: str
    sha256: str
    file_path: str | None = None


class FormatGrade(StrictModel):
    format_followed: bool
    json_parse_success: bool
    required_sections_present: bool
    citation_format_valid: bool
    abstention_format_correct: bool | None
    forbidden_field_detected: bool
    errors: list[str] = Field(default_factory=list)
    parsed_answer: str | None = None
    parsed_citations: list[dict[str, str]] = Field(default_factory=list)


class CitationValidityDetail(StrictModel):
    cited_chunk_id: str
    cited_document_id: str
    valid: bool
    reason: str


class CitationReferenceGrade(StrictModel):
    valid_reference_count: int
    invalid_reference_count: int
    total_citation_count: int
    citation_reference_validity_rate: float | None
    details: list[CitationValidityDetail]


class CitationSemanticGrade(StrictModel):
    citation_support_rate: float | None
    semantically_graded_count: int
    semantic_grade_status: Literal["not_graded", "judge_graded", "human_graded", "judge_error"]


class JudgeVerdict(StrictModel):
    citation_supported: bool | None = None
    citation_support_by_index: list[bool] = Field(default_factory=list)
    contains_unsupported_claim: bool | None = None
    correctly_abstained: bool | None = None
    missing_answer_points: list[str] = Field(default_factory=list)
    reason: str = ""


class GroundingGrade(StrictModel):
    contains_unsupported_claim: bool | None
    correctly_abstained: bool | None
    unsupported_claim_reason: str | None = None
    semantic_grade_status: Literal["not_graded", "judge_graded", "human_graded", "judge_error"]


class WarmupResult(StrictModel):
    case_ids: list[str]
    succeeded: bool
    error_message: str | None = None


class EvaluationRunResult(StrictModel):
    run_id: str
    run_mode: Literal["mock_smoke", "remote"]
    quality_metrics_are_representative: bool
    prompt_variant: str
    prompt_sha256: str
    split: str | None
    repeat_count: int
    results: list[EvaluationCaseResult]
    warmup: WarmupResult | None = None
    git_commit_sha: str | None = None
    git_worktree_dirty: bool = False
    git_diff_sha256: str | None = None
    dataset_version: str = "learning-qa-v1"
    corpus_version: str = "learning-qa-v1"
    corpus_hash: str | None = None
    chunking_config_hash: str | None = None
    response_envelope_sha256: str | None = None
    model: str | None = None
    embedding_model: str | None = None
    retrieval_backend: str | None = None
    retrieval_limit: int = 5
    generation_context_k: int = 5
    metric_cutoffs: list[int] = Field(default_factory=lambda: [1, 3, 5])
    temperature: float = 0.0
    max_output_tokens: int | None = None
    seed: int | None = None
    judge_model: str | None = None
    conversation_mode: Literal["persistent", "dependency_unavailable"] = "dependency_unavailable"
