from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum

from backend.app.services.document_parsing.models import DocumentParsingProfile


class ChunkingStrategy(str, Enum):
    V2 = "v2"
    HYBRID_V3 = "hybrid_v3"


@dataclass(frozen=True)
class SemanticChunkPolicy:
    window_size: int = 2
    min_boundary_samples: int = 5
    mad_multiplier: float = 1.5
    local_window_weight: float = 0.65
    adjacent_weight: float = 0.35
    relation_penalty_weight: float = 0.20
    max_semantic_units: int = 10_000
    max_semantic_unit_chars: int = 2_000


@dataclass(frozen=True)
class SizeGuardPolicy:
    min_tokens: int = 120
    target_tokens: int = 320
    max_tokens: int = 512


@dataclass(frozen=True)
class HybridChunkPolicy:
    semantic: SemanticChunkPolicy = field(default_factory=SemanticChunkPolicy)
    size: SizeGuardPolicy = field(default_factory=SizeGuardPolicy)
    include_heading_context: bool = True
    semantic_batch_size: int = 64
    policy_version: str = "hybrid-v3-initial"
    tokenizer_id: str = "cl100k_base"

    @classmethod
    def from_mapping(cls, payload: dict) -> "HybridChunkPolicy":
        semantic_payload = dict(payload.get("semantic", {}))
        size_payload = dict(payload.get("size", {}))
        return cls(
            semantic=SemanticChunkPolicy(**semantic_payload),
            size=SizeGuardPolicy(**size_payload),
            include_heading_context=bool(payload.get("include_heading_context", True)),
            semantic_batch_size=int(payload.get("semantic_batch_size", 64)),
            policy_version=str(payload.get("policy_version", "hybrid-v3-initial")),
            tokenizer_id=str(payload.get("tokenizer_id", "cl100k_base")),
        )


@dataclass(frozen=True)
class TokenizerIdentity:
    name: str


@dataclass(frozen=True)
class ChunkingExecutionConfig:
    strategy: ChunkingStrategy
    parser_profile: DocumentParsingProfile
    policy_version: str
    policy_fingerprint: str
    tokenizer_id: str
    policy: HybridChunkPolicy | None = None

    @classmethod
    def from_policy(
        cls,
        *,
        strategy: ChunkingStrategy | str,
        parser_profile: DocumentParsingProfile,
        policy: HybridChunkPolicy,
        tokenizer: TokenizerIdentity,
    ) -> "ChunkingExecutionConfig":
        normalized_strategy = ChunkingStrategy(strategy)
        return cls(
            strategy=normalized_strategy,
            parser_profile=parser_profile,
            policy_version=policy.policy_version,
            policy_fingerprint=policy_fingerprint(
                policy,
                tokenizer_id=tokenizer.name,
            ),
            tokenizer_id=tokenizer.name,
            policy=policy,
        )


def policy_fingerprint(
    policy: HybridChunkPolicy,
    *,
    tokenizer_id: str | None = None,
) -> str:
    payload = asdict(policy)
    if tokenizer_id is not None:
        payload["tokenizer_id"] = tokenizer_id
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def chunking_strategy_from_env(environ: dict[str, str] | None = None) -> ChunkingStrategy:
    values = environ if environ is not None else os.environ
    enabled = values.get("FEATURE_HYBRID_CHUNKING_V3", "false").strip().lower()
    return ChunkingStrategy.HYBRID_V3 if enabled in {"1", "true", "yes", "on"} else ChunkingStrategy.V2


__all__ = [
    "ChunkingExecutionConfig",
    "ChunkingStrategy",
    "HybridChunkPolicy",
    "SemanticChunkPolicy",
    "SizeGuardPolicy",
    "TokenizerIdentity",
    "chunking_strategy_from_env",
    "policy_fingerprint",
]
