from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum

from backend.app.services.document_parsing.models import DocumentParsingProfile


CHUNKING_ALGORITHM_VERSIONS = {
    "structure": "structure-v3.1",
    "semantic": "semantic-v3.1",
    "sentence_splitter": "sentence-v3.1",
    "relations": "relations-v3.1",
    "renderer": "renderer-v3.1",
    "size_guard": "size-v3.1",
    "table_splitter": "table-v3.1",
    "code_splitter": "code-v3.1",
}


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

    def __post_init__(self) -> None:
        controls = (
            self.window_size,
            self.min_boundary_samples,
            self.max_semantic_units,
            self.max_semantic_unit_chars,
        )
        if any(type(value) is not int or value <= 0 for value in controls):
            raise ValueError("semantic controls must be positive integers")
        if not isinstance(self.mad_multiplier, (int, float)) or self.mad_multiplier <= 0:
            raise ValueError("mad_multiplier must be positive")
        weights = (
            self.local_window_weight,
            self.adjacent_weight,
            self.relation_penalty_weight,
        )
        if any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in weights):
            raise ValueError("semantic weights must be between 0 and 1")
        if self.local_window_weight + self.adjacent_weight <= 0:
            raise ValueError("at least one similarity weight must be positive")


@dataclass(frozen=True)
class SizeGuardPolicy:
    min_tokens: int = 120
    target_tokens: int = 320
    max_tokens: int = 512

    def __post_init__(self) -> None:
        values = (self.min_tokens, self.target_tokens, self.max_tokens)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("size controls must be positive integers")
        if not self.min_tokens <= self.target_tokens <= self.max_tokens:
            raise ValueError("size controls must satisfy min_tokens <= target_tokens <= max_tokens")


@dataclass(frozen=True)
class HybridChunkPolicy:
    semantic: SemanticChunkPolicy = field(default_factory=SemanticChunkPolicy)
    size: SizeGuardPolicy = field(default_factory=SizeGuardPolicy)
    include_heading_context: bool = True
    semantic_batch_size: int = 64
    policy_version: str = "hybrid-v3.1"
    tokenizer_id: str = "cl100k_base"

    def __post_init__(self) -> None:
        if type(self.semantic_batch_size) is not int or self.semantic_batch_size <= 0:
            raise ValueError("semantic_batch_size must be a positive integer")

    @classmethod
    def from_mapping(cls, payload: dict) -> "HybridChunkPolicy":
        semantic_payload = dict(payload.get("semantic", {}))
        size_payload = dict(payload.get("size", {}))
        return cls(
            semantic=SemanticChunkPolicy(**semantic_payload),
            size=SizeGuardPolicy(**size_payload),
            include_heading_context=bool(payload.get("include_heading_context", True)),
            semantic_batch_size=int(payload.get("semantic_batch_size", 64)),
            policy_version=str(payload.get("policy_version", "hybrid-v3.1")),
            tokenizer_id=str(payload.get("tokenizer_id", "cl100k_base")),
        )


@dataclass(frozen=True)
class TokenizerIdentity:
    name: str


@dataclass(frozen=True)
class ChunkingExecutionSnapshot:
    snapshot_version: str
    strategy: ChunkingStrategy
    parser_profile: DocumentParsingProfile
    parser_implementation_version: str
    chunking_implementation_version: str
    v3_policy: HybridChunkPolicy | None = None
    policy_fingerprint: str | None = None
    tokenizer_id: str | None = None

    @classmethod
    def v2(cls) -> "ChunkingExecutionSnapshot":
        return cls(
            snapshot_version="chunking-execution-v1",
            strategy=ChunkingStrategy.V2,
            parser_profile=DocumentParsingProfile.LEGACY_V2,
            parser_implementation_version="legacy-parser-v3",
            chunking_implementation_version="chunking-v2",
        )

    @classmethod
    def from_v3_policy(
        cls,
        *,
        policy: HybridChunkPolicy,
        tokenizer: TokenizerIdentity,
    ) -> "ChunkingExecutionSnapshot":
        return cls(
            snapshot_version="chunking-execution-v1",
            strategy=ChunkingStrategy.HYBRID_V3,
            parser_profile=DocumentParsingProfile.STRUCTURED_V3,
            parser_implementation_version="document-parser-v4.1",
            chunking_implementation_version="hybrid-chunking-v3.1",
            policy_fingerprint=policy_fingerprint(
                policy,
                tokenizer_id=tokenizer.name,
            ),
            tokenizer_id=tokenizer.name,
            v3_policy=policy,
        )

    @classmethod
    def from_policy(
        cls,
        *,
        strategy: ChunkingStrategy | str,
        parser_profile: DocumentParsingProfile,
        policy: HybridChunkPolicy,
        tokenizer: TokenizerIdentity,
    ) -> "ChunkingExecutionSnapshot":
        if ChunkingStrategy(strategy) is not ChunkingStrategy.HYBRID_V3 or parser_profile is not DocumentParsingProfile.STRUCTURED_V3:
            raise ValueError("only V3 execution snapshots may carry a policy")
        return cls.from_v3_policy(policy=policy, tokenizer=tokenizer)

    @property
    def policy(self) -> HybridChunkPolicy | None:
        """Compatibility alias for V3-only callers; V2 remains policy-free."""
        return self.v3_policy

    @property
    def policy_version(self) -> str | None:
        return self.v3_policy.policy_version if self.v3_policy is not None else None

    def to_payload(self) -> dict:
        payload = {
            "snapshot_version": self.snapshot_version,
            "strategy": self.strategy.value,
            "parser_profile": self.parser_profile.value,
            "parser_implementation_version": self.parser_implementation_version,
            "chunking_implementation_version": self.chunking_implementation_version,
            "policy_fingerprint": self.policy_fingerprint,
            "tokenizer_id": self.tokenizer_id,
            "v3_policy": asdict(self.v3_policy) if self.v3_policy is not None else None,
        }
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "ChunkingExecutionSnapshot":
        try:
            strategy = ChunkingStrategy(str(payload["strategy"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("execution snapshot has an invalid strategy") from exc
        if strategy is ChunkingStrategy.V2:
            snapshot = cls.v2()
            required = {
                "snapshot_version": snapshot.snapshot_version,
                "parser_profile": snapshot.parser_profile.value,
                "parser_implementation_version": snapshot.parser_implementation_version,
                "chunking_implementation_version": snapshot.chunking_implementation_version,
            }
            if any(payload.get(key) != value for key, value in required.items()):
                raise ValueError("V2 execution snapshot implementation identity is incompatible")
            if any(payload.get(key) is not None for key in ("v3_policy", "policy", "policy_fingerprint", "tokenizer_id")):
                raise ValueError("V2 execution snapshot must not carry V3 policy fields")
            return snapshot
        required_keys = (
            "snapshot_version",
            "parser_profile",
            "parser_implementation_version",
            "chunking_implementation_version",
            "v3_policy",
            "policy_fingerprint",
            "tokenizer_id",
        )
        if any(key not in payload or payload[key] in (None, "") for key in required_keys):
            raise ValueError("V3 execution snapshot is missing required version identity")
        policy_payload = payload["v3_policy"]
        if not isinstance(policy_payload, Mapping):
            raise ValueError("V3 execution snapshot policy must be an object")
        policy = HybridChunkPolicy.from_mapping(dict(policy_payload))
        tokenizer_id = str(payload["tokenizer_id"])
        if policy.tokenizer_id != tokenizer_id:
            raise ValueError("V3 execution snapshot tokenizer does not match policy")
        snapshot = cls.from_v3_policy(policy=policy, tokenizer=TokenizerIdentity(tokenizer_id))
        expected = {
            "snapshot_version": snapshot.snapshot_version,
            "parser_profile": snapshot.parser_profile.value,
            "parser_implementation_version": snapshot.parser_implementation_version,
            "chunking_implementation_version": snapshot.chunking_implementation_version,
            "policy_fingerprint": snapshot.policy_fingerprint,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError("V3 execution snapshot implementation identity is incompatible")
        return snapshot


ChunkingExecutionConfig = ChunkingExecutionSnapshot


def policy_fingerprint(
    policy: HybridChunkPolicy,
    *,
    tokenizer_id: str | None = None,
) -> str:
    payload = {
        "policy": asdict(policy),
        "tokenizer_id": tokenizer_id or policy.tokenizer_id,
        "algorithm_versions": CHUNKING_ALGORITHM_VERSIONS,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def chunking_strategy_from_env(environ: dict[str, str] | None = None) -> ChunkingStrategy:
    values = environ if environ is not None else os.environ
    enabled = values.get("FEATURE_HYBRID_CHUNKING_V3", "false").strip().lower()
    return ChunkingStrategy.HYBRID_V3 if enabled in {"1", "true", "yes", "on"} else ChunkingStrategy.V2


__all__ = [
    "ChunkingExecutionConfig",
    "ChunkingExecutionSnapshot",
    "ChunkingStrategy",
    "CHUNKING_ALGORITHM_VERSIONS",
    "HybridChunkPolicy",
    "SemanticChunkPolicy",
    "SizeGuardPolicy",
    "TokenizerIdentity",
    "chunking_strategy_from_env",
    "policy_fingerprint",
]
