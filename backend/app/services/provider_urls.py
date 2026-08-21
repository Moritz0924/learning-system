from __future__ import annotations

from hashlib import sha256
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_QUERY_SEGMENT = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|auth(?:orization)?|credentials?|password|"
    r"secrets?|tokens?|keys?|sig(?:nature)?)(?:$|[_-])",
    re.IGNORECASE,
)
_SIGNED_QUERY_NAMES = {
    "awsaccesskeyid",
    "googleaccessid",
    "accesstoken",
    "clientsecret",
    "subscriptionkey",
    "skoid",
    "sktid",
    "skt",
    "ske",
    "sks",
    "skv",
    "sig",
    "se",
    "ss",
    "srt",
    "st",
    "sp",
    "spr",
    "sr",
    "sv",
}


def build_provider_url(base_url: str, endpoint_path: str) -> str:
    parts = urlsplit(base_url.strip())
    path = (parts.path.rstrip("/") + "/" + endpoint_path.strip("/")).replace("//", "/")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def canonicalize_provider_base_url(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("provider base URL must be an absolute HTTP URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError("provider base URL must not contain credentials")
    scheme = parts.scheme.lower()
    hostname = parts.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = parts.port
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def provider_url_identity(base_url: str) -> str:
    canonical = canonicalize_provider_base_url(base_url)
    return sha256(canonical.encode("utf-8")).hexdigest()


def has_sensitive_query_name(base_url: str) -> bool:
    return any(
        name.lower() in _SIGNED_QUERY_NAMES
        or _SENSITIVE_QUERY_SEGMENT.search(name) is not None
        for name, _ in parse_qsl(urlsplit(base_url).query, keep_blank_values=True)
    )


__all__ = [
    "build_provider_url",
    "canonicalize_provider_base_url",
    "has_sensitive_query_name",
    "provider_url_identity",
]
