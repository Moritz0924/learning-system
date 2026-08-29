from __future__ import annotations

class PlanApplicationConflict(ValueError):
    pass


class FeedbackIdempotencyConflict(ValueError):
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


class TaskStateConflict(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskNotStarted(TaskStateConflict):
    def __init__(self) -> None:
        super().__init__("task.not_started", "task must have an active learning session before completion")


class TaskCompletionInProgress(TaskStateConflict):
    def __init__(self) -> None:
        super().__init__("task.completion_in_progress", "task completion is already in progress")
