from __future__ import annotations

class PlanApplicationConflict(ValueError):
    pass


class AssessmentSubmissionConflict(ValueError):
    pass


class DocumentUploadTooLarge(ValueError):
    pass


class DocumentProcessingUnavailable(RuntimeError):
    pass
