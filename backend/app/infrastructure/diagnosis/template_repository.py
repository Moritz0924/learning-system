from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from backend.app.domain.diagnosis.contracts import DiagnosticTemplate
from backend.app.domain.diagnosis.validation import DiagnosisValidationError


_SAFE_IDENTIFIER = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class LoadedDiagnosticTemplate:
    template: DiagnosticTemplate
    sha256: str


class DiagnosticTemplateRepository:
    """Read-only repository for validated, versioned JSON diagnostic templates."""

    def __init__(self, resource_directory: Path | None = None) -> None:
        self._resource_directory = (
            resource_directory
            if resource_directory is not None
            else Path(__file__).resolve().parents[2] / "resources" / "diagnostics"
        )
        self._cache: dict[tuple[str, str], LoadedDiagnosticTemplate] = {}

    def load(
        self, *, domain: str, template_version: str | None = None
    ) -> LoadedDiagnosticTemplate:
        resolved_version = template_version or f"{domain}_v1"
        if not _SAFE_IDENTIFIER.fullmatch(domain) or not _SAFE_IDENTIFIER.fullmatch(resolved_version):
            raise DiagnosisValidationError(
                "invalid_template_identifier", "Diagnostic template identifiers are invalid."
            )

        cache_key = (domain, resolved_version)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        template_path = self._resource_directory / f"{resolved_version}.json"
        try:
            raw = json.loads(template_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DiagnosisValidationError(
                "template_not_found", f"Diagnostic template not found: {resolved_version}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise DiagnosisValidationError(
                "invalid_template", f"Diagnostic template is not valid JSON: {resolved_version}"
            ) from exc

        try:
            template = DiagnosticTemplate.model_validate(raw)
        except ValidationError as exc:
            raise DiagnosisValidationError(
                "invalid_template", f"Diagnostic template contract is invalid: {resolved_version}"
            ) from exc

        if template.template_version != resolved_version:
            raise DiagnosisValidationError(
                "template_version_mismatch",
                "Diagnostic template file name and declared version do not match.",
            )
        if template.domain != domain:
            raise DiagnosisValidationError(
                "template_domain_mismatch",
                "Diagnostic template domain does not match the requested domain.",
            )

        canonical_json = json.dumps(
            raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        loaded = LoadedDiagnosticTemplate(
            template=template,
            sha256=hashlib.sha256(canonical_json).hexdigest(),
        )
        self._cache[cache_key] = loaded
        return loaded
