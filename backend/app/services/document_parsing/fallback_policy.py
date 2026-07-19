from __future__ import annotations

import os

from .models import DocumentFileType, OCRResult


class VisionFallbackPolicy:
    def __init__(self) -> None:
        self.mode = os.getenv("OCR_VISION_FALLBACK", "auto").strip().lower() or "auto"
        self.min_confidence = _float_env("OCR_MIN_CONFIDENCE", 0.65)
        self.min_text_chars = _int_env("OCR_MIN_TEXT_CHARS", 20)

    def should_enrich(
        self,
        *,
        ocr_result: OCRResult,
        file_type: DocumentFileType,
        page_number: int,
        image_coverage_ratio: float | None = None,
        complex_visual_hint: bool = False,
    ) -> bool:
        del file_type, page_number, image_coverage_ratio
        if os.getenv("VISION_ENABLED", "true").strip().lower() in {"0", "false", "no"}:
            return False
        if self.mode == "disabled":
            return False
        if self.mode == "always":
            return True
        if self.mode != "auto":
            return False
        return bool(
            complex_visual_hint
            or ocr_result.confidence is None
            or ocr_result.confidence < self.min_confidence
            or ocr_result.text_char_count < self.min_text_chars
        )


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if 0 <= value <= 1 else default


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
