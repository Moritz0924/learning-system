from __future__ import annotations

import os
from io import BytesIO


class OCRUnavailable(RuntimeError):
    pass


class TesseractOCRClient:
    def __init__(self, *, languages: str | None = None) -> None:
        self.languages = languages or os.getenv("TESSERACT_LANG", "eng+chi_sim")

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
    backend = os.getenv("OCR_BACKEND", "tesseract").lower()
    if backend != "tesseract":
        raise OCRUnavailable(f"unsupported OCR backend: {backend}")
    return TesseractOCRClient()
