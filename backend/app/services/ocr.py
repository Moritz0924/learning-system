from __future__ import annotations

import os
from io import BytesIO


class OCRUnavailable(RuntimeError):
    pass


class TesseractOCRClient:
    def __init__(self, *, languages: str | None = None) -> None:
        self.languages = _config_value(languages) or _config_value(os.getenv("TESSERACT_LANG")) or "eng+chi_sim"

    def extract_text(self, content: bytes, *, filename: str) -> str:
        try:
            from PIL import Image
            import pytesseract
            from pytesseract import TesseractNotFoundError
        except ImportError as exc:
            raise OCRUnavailable("pillow and pytesseract are required for image OCR") from exc

        try:
            image = Image.open(BytesIO(content))
            return pytesseract.image_to_string(image, lang=self.languages).strip()
        except TesseractNotFoundError as exc:
            raise OCRUnavailable("tesseract executable is required for image OCR") from exc
        except Exception as exc:
            raise ValueError(f"image OCR failed for {filename}") from exc


def build_ocr_client() -> TesseractOCRClient:
    backend = (_config_value(os.getenv("OCR_BACKEND")) or "tesseract").lower()
    if backend != "tesseract":
        raise OCRUnavailable(f"unsupported OCR backend: {backend}")
    return TesseractOCRClient()


def _config_value(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
