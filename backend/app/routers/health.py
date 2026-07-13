from __future__ import annotations

import math
import os
from urllib.parse import urlparse

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from backend.app.core.runtime_config import missing_runtime_configuration, runtime_environment


router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/ready", response_model=None)
def readiness():
    missing = missing_runtime_configuration()
    unavailable = probe_runtime_dependencies() if not missing else []
    payload = {
        "status": "ready" if not missing and not unavailable else "not_ready",
        "environment": runtime_environment(),
        "missing": missing,
        "unavailable": unavailable,
    }
    if missing or unavailable:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
    return payload


def probe_runtime_dependencies() -> list[str]:
    if runtime_environment() not in {"prod", "production"}:
        return []
    failures: list[str] = []
    probes = [("database connectivity failed", _probe_database)]
    if _env_value("DOCUMENT_PROCESSING_MODE", "inline").lower() == "celery":
        probes.append(("redis connectivity failed", _probe_redis))
    if _env_value("DOCUMENT_OBJECT_STORAGE_BACKEND", "local").lower() == "minio":
        probes.append(("minio connectivity failed", _probe_minio))
    for label, probe in probes:
        try:
            probe()
        except Exception:
            failures.append(label)
    return failures


def _probe_database() -> None:
    database_url = _env_value("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is missing")
    timeout = _probe_timeout_seconds()
    connect_args = (
        {"connect_timeout": max(1, math.ceil(timeout))}
        if database_url.startswith("postgresql")
        else {}
    )
    probe_engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    try:
        with probe_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        probe_engine.dispose()


def _probe_redis() -> None:
    from redis import Redis

    timeout = _probe_timeout_seconds()
    client = Redis.from_url(
        _env_value("REDIS_URL") or "",
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    try:
        if client.ping() is not True:
            raise RuntimeError("redis ping failed")
    finally:
        client.close()


def _probe_minio() -> None:
    import urllib3
    from minio import Minio

    endpoint = _env_value("MINIO_ENDPOINT") or ""
    parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
    netloc = parsed.netloc or parsed.path
    timeout = _probe_timeout_seconds()
    http_client = urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=timeout, read=timeout),
        retries=False,
    )
    try:
        client = Minio(
            netloc,
            access_key=_env_value("MINIO_ACCESS_KEY") or "",
            secret_key=_env_value("MINIO_SECRET_KEY") or "",
            secure=parsed.scheme == "https",
            http_client=http_client,
        )
        bucket = _env_value("MINIO_BUCKET") or ""
        if not client.bucket_exists(bucket):
            raise RuntimeError("configured MinIO bucket does not exist")
    finally:
        http_client.clear()


def _probe_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("READINESS_PROBE_TIMEOUT_SECONDS", "2"))
    except ValueError:
        return 2.0
    return min(10.0, max(0.1, configured))


def _env_value(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default) or ""
    return value.strip()
