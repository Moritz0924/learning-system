from __future__ import annotations

import os
import asyncio
from io import BytesIO

from backend.app.services.document_parsing.models import OCRResult, OCRWord


class OCRUnavailable(RuntimeError):
    pass


def build_ocr_result_from_tesseract_data(data: dict) -> OCRResult:
    words: list[OCRWord] = []
    weighted_confidence = 0.0
    weighted_characters = 0
    for index, raw_text in enumerate(data.get("text", [])):
        text = str(raw_text).strip()
        try:
            confidence = float(data.get("conf", [])[index])
        except (IndexError, TypeError, ValueError):
            continue
        if not text or confidence < 0:
            continue
        normalized_confidence = max(0.0, min(1.0, confidence / 100.0))
        words.append(
            OCRWord(
                text=text,
                confidence=normalized_confidence,
                left=_int_at(data, "left", index),
                top=_int_at(data, "top", index),
                width=_int_at(data, "width", index),
                height=_int_at(data, "height", index),
            )
        )
        weighted_confidence += normalized_confidence * len(text)
        weighted_characters += len(text)
    return OCRResult(
        text=" ".join(word.text for word in words),
        confidence=(weighted_confidence / weighted_characters) if weighted_characters else None,
        word_count=len(words),
        text_char_count=weighted_characters,
        words=words,
    )


def _int_at(data: dict, name: str, index: int) -> int | None:
    try:
        return int(data.get(name, [])[index])
    except (IndexError, TypeError, ValueError):
        return None


class TesseractOCRClient:
    def __init__(self, *, languages: str | None = None) -> None:
        self.languages = _config_value(languages) or _config_value(os.getenv("TESSERACT_LANG")) or "eng+chi_sim"

    def extract_text(self, content: bytes, *, filename: str) -> str:
        return self._recognize_bytes_sync(content, filename=filename).text

    async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
        return await asyncio.to_thread(self._recognize_bytes_sync, content, filename=filename)

    def _recognize_bytes_sync(self, content: bytes, *, filename: str) -> OCRResult:
        try:
            from PIL import Image
            import pytesseract
            from pytesseract import TesseractNotFoundError
        except ImportError as exc:
            raise OCRUnavailable("pillow and pytesseract are required for image OCR") from exc

        try:
            with Image.open(BytesIO(content)) as image:
                data = pytesseract.image_to_data(image, lang=self.languages, output_type=pytesseract.Output.DICT)
            return build_ocr_result_from_tesseract_data(data)
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
