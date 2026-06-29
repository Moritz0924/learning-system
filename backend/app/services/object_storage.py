from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


class ObjectStorageUnavailable(RuntimeError):
    pass


class DocumentObjectStorage(Protocol):
    def put_bytes(self, object_key: str, content: bytes, *, content_type: str) -> None:
        ...

    def get_bytes(self, object_key: str) -> bytes:
        ...


@dataclass
class LocalDocumentObjectStorage:
    root_dir: Path

    def put_bytes(self, object_key: str, content: bytes, *, content_type: str) -> None:
        path = self._path_for(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get_bytes(self, object_key: str) -> bytes:
        path = self._path_for(object_key)
        if not path.exists():
            raise ObjectStorageUnavailable(f"document object not found: {object_key}")
        return path.read_bytes()

    def _path_for(self, object_key: str) -> Path:
        normalized = object_key.replace("\\", "/").lstrip("/")
        if ".." in Path(normalized).parts:
            raise ObjectStorageUnavailable("document object key cannot traverse directories")
        return self.root_dir / normalized


class MinioDocumentObjectStorage:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        try:
            from minio import Minio
            from minio.error import S3Error
        except ImportError as exc:
            raise ObjectStorageUnavailable("minio package is required for MinIO document storage") from exc

        parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
        secure = parsed.scheme == "https"
        netloc = parsed.netloc or parsed.path
        self._client = Minio(netloc, access_key=access_key, secret_key=secret_key, secure=secure)
        self._s3_error = S3Error
        self.bucket = bucket

    def put_bytes(self, object_key: str, content: bytes, *, content_type: str) -> None:
        self._ensure_bucket()
        self._client.put_object(
            self.bucket,
            object_key,
            BytesIO(content),
            length=len(content),
            content_type=content_type,
        )

    def get_bytes(self, object_key: str) -> bytes:
        try:
            response = self._client.get_object(self.bucket, object_key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except self._s3_error as exc:
            raise ObjectStorageUnavailable(f"document object not found: {object_key}") from exc

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)


def build_document_object_storage() -> DocumentObjectStorage:
    backend = os.getenv("DOCUMENT_OBJECT_STORAGE_BACKEND")
    if backend is None:
        backend = "minio" if os.getenv("MINIO_ENDPOINT") else "local"
    backend = backend.lower()
    if backend == "minio":
        endpoint = os.getenv("MINIO_ENDPOINT")
        access_key = os.getenv("MINIO_ACCESS_KEY")
        secret_key = os.getenv("MINIO_SECRET_KEY")
        bucket = os.getenv("MINIO_BUCKET", "adaptive-tutor-documents")
        if not endpoint or not access_key or not secret_key:
            raise ObjectStorageUnavailable("MINIO_ENDPOINT, MINIO_ACCESS_KEY and MINIO_SECRET_KEY are required")
        return MinioDocumentObjectStorage(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
        )
    if backend == "local":
        root_dir = Path(os.getenv("DOCUMENT_OBJECT_STORAGE_LOCAL_DIR", ".document_objects"))
        return LocalDocumentObjectStorage(root_dir=root_dir)
    raise ObjectStorageUnavailable(f"unsupported document object storage backend: {backend}")
