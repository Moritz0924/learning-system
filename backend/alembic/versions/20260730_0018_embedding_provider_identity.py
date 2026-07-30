"""scope embedding indexes and caches by provider identity

Revision ID: 20260730_0018
Revises: 20260729_0017
"""

from __future__ import annotations

import base64
import binascii
from hashlib import sha256
import json

from alembic import op
import sqlalchemy as sa


revision = "20260730_0018"
down_revision = "20260729_0017"
branch_labels = None
depends_on = None


LEGACY_PROVIDER = "legacy-unknown"
VERSION_ENVELOPE_PREFIX = "__ls_0018_version_v1__:"
CACHE_ENVELOPE_PREFIX = "__ls_0018_cache_v1__:"
CACHE_RESTORE_PREFIX = "__ls_0018_cache_restore__:"


def upgrade() -> None:
    bind = op.get_bind()
    provider_column = sa.Column(
        "embedding_provider",
        sa.String(length=128),
        nullable=False,
        server_default=LEGACY_PROVIDER,
    )
    cache_provider_column = sa.Column(
        "embedding_provider",
        sa.String(length=128),
        nullable=False,
        server_default=LEGACY_PROVIDER,
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "document_index_versions",
            recreate="always",
        ) as batch:
            batch.add_column(provider_column)
            batch.drop_constraint(
                "uq_document_index_versions_document_build",
                type_="unique",
            )
            batch.create_unique_constraint(
                "uq_document_index_versions_document_build",
                ["document_id", "build_key", "embedding_provider"],
            )
        op.drop_index(
            "ix_embedding_cache_entries_lookup",
            table_name="embedding_cache_entries",
        )
        with op.batch_alter_table(
            "embedding_cache_entries",
            recreate="always",
        ) as batch:
            batch.add_column(cache_provider_column)
            batch.drop_constraint(
                "uq_embedding_cache_model_dimensions_hash",
                type_="unique",
            )
            batch.create_unique_constraint(
                "uq_embedding_cache_provider_model_dimensions_hash",
                [
                    "embedding_provider",
                    "embedding_model",
                    "dimensions",
                    "content_hash",
                ],
            )
    else:
        op.add_column("document_index_versions", provider_column)
        op.drop_constraint(
            "uq_document_index_versions_document_build",
            "document_index_versions",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_document_index_versions_document_build",
            "document_index_versions",
            ["document_id", "build_key", "embedding_provider"],
        )
        op.drop_index(
            "ix_embedding_cache_entries_lookup",
            table_name="embedding_cache_entries",
        )
        op.add_column("embedding_cache_entries", cache_provider_column)
        op.drop_constraint(
            "uq_embedding_cache_model_dimensions_hash",
            "embedding_cache_entries",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_embedding_cache_provider_model_dimensions_hash",
            "embedding_cache_entries",
            [
                "embedding_provider",
                "embedding_model",
                "dimensions",
                "content_hash",
            ],
        )
    op.create_index(
        "ix_embedding_cache_entries_lookup",
        "embedding_cache_entries",
        [
            "embedding_provider",
            "embedding_model",
            "dimensions",
            "content_hash",
        ],
    )
    _restore_provider_identity(bind)


def downgrade() -> None:
    bind = op.get_bind()
    _prepare_provider_downgrade(bind)
    op.drop_index(
        "ix_embedding_cache_entries_lookup",
        table_name="embedding_cache_entries",
    )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "embedding_cache_entries",
            recreate="always",
        ) as batch:
            batch.drop_constraint(
                "uq_embedding_cache_provider_model_dimensions_hash",
                type_="unique",
            )
            batch.create_unique_constraint(
                "uq_embedding_cache_model_dimensions_hash",
                ["embedding_model", "dimensions", "content_hash"],
            )
            batch.drop_column("embedding_provider")
        with op.batch_alter_table(
            "document_index_versions",
            recreate="always",
        ) as batch:
            batch.drop_constraint(
                "uq_document_index_versions_document_build",
                type_="unique",
            )
            batch.create_unique_constraint(
                "uq_document_index_versions_document_build",
                ["document_id", "build_key"],
            )
            batch.drop_column("embedding_provider")
    else:
        op.drop_constraint(
            "uq_embedding_cache_provider_model_dimensions_hash",
            "embedding_cache_entries",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_embedding_cache_model_dimensions_hash",
            "embedding_cache_entries",
            ["embedding_model", "dimensions", "content_hash"],
        )
        op.drop_column("embedding_cache_entries", "embedding_provider")
        op.drop_constraint(
            "uq_document_index_versions_document_build",
            "document_index_versions",
            type_="unique",
        )
        op.create_unique_constraint(
            "uq_document_index_versions_document_build",
            "document_index_versions",
            ["document_id", "build_key"],
        )
        op.drop_column("document_index_versions", "embedding_provider")
    op.create_index(
        "ix_embedding_cache_entries_lookup",
        "embedding_cache_entries",
        ["embedding_model", "dimensions", "content_hash"],
    )


def _prepare_provider_downgrade(bind) -> None:
    _prepare_version_rows(bind)
    _prepare_cache_rows(bind)


def _prepare_version_rows(bind) -> None:
    # Version IDs are intentionally immutable here: document_chunks references
    # them with ON DELETE CASCADE on PostgreSQL and SQLite when FK enforcement is on.
    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT id, document_id, build_key, embedding_provider, error_message
                FROM document_index_versions
                ORDER BY document_id, build_key, id
                """
            )
        ).mappings()
    )
    groups: dict[tuple[str, str], list] = {}
    used_keys: dict[str, set[str]] = {}
    for row in rows:
        document_id = str(row["document_id"])
        build_key = str(row["build_key"])
        groups.setdefault((document_id, build_key), []).append(row)
        used_keys.setdefault(document_id, set()).add(build_key)

    updates: list[dict[str, object]] = []
    for (document_id, original_build_key), group in groups.items():
        ordered = sorted(
            group,
            key=lambda row: (
                str(row["embedding_provider"]) != LEGACY_PROVIDER,
                str(row["id"]),
            ),
        )
        for offset, row in enumerate(ordered):
            provider = str(row["embedding_provider"])
            target_build_key = original_build_key
            if offset:
                target_build_key = _compat_key(
                    kind="version",
                    identity=(
                        f"{document_id}\0{row['id']}\0{original_build_key}\0{provider}"
                    ),
                    used=used_keys[document_id],
                )
            error_message = row["error_message"]
            if provider != LEGACY_PROVIDER:
                error_message = _encode_envelope(
                    VERSION_ENVELOPE_PREFIX,
                    {
                        "format": 1,
                        "build_key": original_build_key,
                        "embedding_provider": provider,
                        "error_message": row["error_message"],
                    },
                )
            elif offset:
                raise RuntimeError(
                    "cannot downgrade duplicate legacy embedding-provider versions safely"
                )
            if target_build_key != original_build_key or error_message != row["error_message"]:
                updates.append(
                    {
                        "id": row["id"],
                        "build_key": target_build_key,
                        "error_message": error_message,
                    }
                )

    for values in updates:
        bind.execute(
            sa.text(
                """
                UPDATE document_index_versions
                SET build_key = :build_key, error_message = :error_message
                WHERE id = :id
                """
            ),
            values,
        )


def _prepare_cache_rows(bind) -> None:
    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT id, content_hash, embedding_provider, embedding_model, dimensions
                FROM embedding_cache_entries
                ORDER BY embedding_model, dimensions, content_hash, id
                """
            )
        ).mappings()
    )
    groups: dict[tuple[str, int, str], list] = {}
    used_models: dict[tuple[int, str], set[str]] = {}
    for row in rows:
        model = str(row["embedding_model"])
        dimensions = int(row["dimensions"])
        content_hash = str(row["content_hash"])
        groups.setdefault((model, dimensions, content_hash), []).append(row)
        used_models.setdefault((dimensions, content_hash), set()).add(model)

    updates: list[dict[str, object]] = []
    target_ids = {str(row["id"]) for row in rows}
    for (original_model, dimensions, content_hash), group in groups.items():
        ordered = sorted(
            group,
            key=lambda row: (
                str(row["embedding_provider"]) != LEGACY_PROVIDER,
                str(row["id"]),
            ),
        )
        for offset, row in enumerate(ordered):
            provider = str(row["embedding_provider"])
            target_model = original_model
            if offset:
                target_model = _compat_key(
                    kind="cache",
                    identity=(
                        f"{row['id']}\0{original_model}\0{dimensions}\0"
                        f"{content_hash}\0{provider}"
                    ),
                    used=used_models[(dimensions, content_hash)],
                )
            target_id = str(row["id"])
            if provider != LEGACY_PROVIDER:
                target_id = _encode_envelope(
                    CACHE_ENVELOPE_PREFIX,
                    {
                        "format": 1,
                        "id": row["id"],
                        "embedding_provider": provider,
                        "embedding_model": original_model,
                    },
                )
                if target_id in target_ids and target_id != str(row["id"]):
                    raise RuntimeError(
                        "cannot downgrade embedding cache safely: encoded id collision"
                    )
                target_ids.add(target_id)
            elif offset:
                raise RuntimeError(
                    "cannot downgrade duplicate legacy embedding-provider cache rows safely"
                )
            if target_id != str(row["id"]) or target_model != original_model:
                updates.append(
                    {
                        "current_id": row["id"],
                        "target_id": target_id,
                        "embedding_model": target_model,
                    }
                )

    _apply_cache_updates(bind, updates)


def _restore_provider_identity(bind) -> None:
    _restore_version_rows(bind)
    _restore_cache_rows(bind)


def _restore_version_rows(bind) -> None:
    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT id, document_id, build_key, embedding_provider, error_message
                FROM document_index_versions
                ORDER BY document_id, id
                """
            )
        ).mappings()
    )
    updates: list[dict[str, object]] = []
    final_keys: set[tuple[str, str, str]] = set()
    for row in rows:
        provider = str(row["embedding_provider"])
        build_key = str(row["build_key"])
        error_message = row["error_message"]
        payload = _decode_envelope(error_message, VERSION_ENVELOPE_PREFIX)
        if payload is not None:
            provider = _required_string(payload, "embedding_provider")
            build_key = _required_string(payload, "build_key")
            error_message = payload.get("error_message")
            if error_message is not None and not isinstance(error_message, str):
                raise RuntimeError("invalid 0018 version downgrade envelope")
            updates.append(
                {
                    "id": row["id"],
                    "build_key": build_key,
                    "embedding_provider": provider,
                    "error_message": error_message,
                }
            )
        final_key = (str(row["document_id"]), build_key, provider)
        if final_key in final_keys:
            raise RuntimeError(
                "cannot restore embedding provider identities: duplicate version key"
            )
        final_keys.add(final_key)

    for values in updates:
        bind.execute(
            sa.text(
                """
                UPDATE document_index_versions
                SET build_key = :build_key,
                    embedding_provider = :embedding_provider,
                    error_message = :error_message
                WHERE id = :id
                """
            ),
            values,
        )


def _restore_cache_rows(bind) -> None:
    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT id, content_hash, embedding_provider, embedding_model, dimensions
                FROM embedding_cache_entries
                ORDER BY id
                """
            )
        ).mappings()
    )
    updates: list[dict[str, object]] = []
    final_ids: set[str] = set()
    final_keys: set[tuple[str, str, int, str]] = set()
    for row in rows:
        current_id = str(row["id"])
        target_id = current_id
        provider = str(row["embedding_provider"])
        model = str(row["embedding_model"])
        payload = _decode_envelope(current_id, CACHE_ENVELOPE_PREFIX)
        if payload is not None:
            target_id = _required_string(payload, "id")
            provider = _required_string(payload, "embedding_provider")
            model = _required_string(payload, "embedding_model")
            updates.append(
                {
                    "current_id": current_id,
                    "target_id": target_id,
                    "embedding_provider": provider,
                    "embedding_model": model,
                }
            )
        final_key = (
            provider,
            model,
            int(row["dimensions"]),
            str(row["content_hash"]),
        )
        if target_id in final_ids or final_key in final_keys:
            raise RuntimeError(
                "cannot restore embedding provider identities: duplicate cache identity"
            )
        final_ids.add(target_id)
        final_keys.add(final_key)

    _apply_cache_updates(bind, updates, restore=True)


def _apply_cache_updates(
    bind,
    updates: list[dict[str, object]],
    *,
    restore: bool = False,
) -> None:
    staged: list[dict[str, object]] = []
    used_ids = set(
        bind.execute(sa.text("SELECT id FROM embedding_cache_entries")).scalars()
    )
    reserved_ids = {str(values["target_id"]) for values in updates}
    for offset, values in enumerate(updates):
        current_id = str(values["current_id"])
        counter = 0
        while True:
            temporary_id = (
                f"{CACHE_RESTORE_PREFIX}{offset}:{counter}:"
                f"{sha256(current_id.encode('utf-8')).hexdigest()}"
            )
            if temporary_id not in used_ids and temporary_id not in reserved_ids:
                break
            counter += 1
        bind.execute(
            sa.text(
                "UPDATE embedding_cache_entries SET id = :temporary_id WHERE id = :current_id"
            ),
            {"temporary_id": temporary_id, "current_id": current_id},
        )
        used_ids.discard(current_id)
        used_ids.add(temporary_id)
        staged.append({**values, "temporary_id": temporary_id})

    for values in staged:
        if restore:
            bind.execute(
                sa.text(
                    """
                    UPDATE embedding_cache_entries
                    SET id = :target_id,
                        embedding_provider = :embedding_provider,
                        embedding_model = :embedding_model
                    WHERE id = :temporary_id
                    """
                ),
                values,
            )
        else:
            bind.execute(
                sa.text(
                    """
                    UPDATE embedding_cache_entries
                    SET id = :target_id, embedding_model = :embedding_model
                    WHERE id = :temporary_id
                    """
                ),
                values,
            )


def _compat_key(*, kind: str, identity: str, used: set[str]) -> str:
    counter = 0
    while True:
        digest = sha256(f"{identity}\0{counter}".encode("utf-8")).hexdigest()
        candidate = f"0018-{kind}-{digest}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        counter += 1


def _encode_envelope(prefix: str, payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")
    return prefix + encoded


def _decode_envelope(value, prefix: str) -> dict[str, object] | None:
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(value[len(prefix) :].encode("ascii")).decode(
                "utf-8"
            )
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise RuntimeError("invalid 0018 provider downgrade envelope") from exc
    if not isinstance(payload, dict) or payload.get("format") != 1:
        raise RuntimeError("invalid 0018 provider downgrade envelope")
    return payload


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise RuntimeError("invalid 0018 provider downgrade envelope")
    return value
