from __future__ import annotations


class AssessmentDomainError(ValueError):
    code = "assessment.invalid"
    status_code = 422

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class AssessmentUnavailable(AssessmentDomainError):
    code = "assessment.generation_unavailable"
    status_code = 503


class AssessmentConflict(AssessmentDomainError):
    code = "assessment.request_id_payload_conflict"
    status_code = 409
