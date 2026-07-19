from __future__ import annotations

class PlanApplicationConflict(ValueError):
    pass


class AssessmentSubmissionConflict(ValueError):
    pass


class AssessmentAnswerValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentUploadTooLarge(ValueError):
    pass


class DocumentProcessingUnavailable(RuntimeError):
    pass
