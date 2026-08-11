from __future__ import annotations

import asyncio
import os
import time

from backend.app.services.ocr import build_ocr_client
from backend.app.services.vision_understanding import VisionClient

from .file_validation import validate_document
from .exceptions import UnsupportedDocumentTypeError
from .image_parser import ImageParser
from .models import DocumentFileType, DocumentParseResult, ParseStatus
from .pdf_parser import PDFParser
from .pptx_parser import PPTXParser


class DocumentParser:
    def __init__(self, *, ocr_service=None, vision_client=None) -> None:
        self.ocr_service = ocr_service or build_ocr_client()
        self.vision_client = vision_client or VisionClient()

    async def parse_image(self, *, content: bytes, filename: str, mime_type: str | None = None) -> DocumentParseResult:
        result = await self.parse_document(content=content, filename=filename, mime_type=mime_type)
        if result.file_type is not DocumentFileType.IMAGE:
            raise UnsupportedDocumentTypeError("ocr_image accepts image files only")
        return result

    async def extract_ppt_content(self, *, content: bytes, filename: str, mime_type: str | None = None) -> DocumentParseResult:
        result = await self.parse_document(content=content, filename=filename, mime_type=mime_type)
        if result.file_type is not DocumentFileType.PPTX:
            raise UnsupportedDocumentTypeError("extract_ppt_content accepts pptx files only")
        return result

    async def parse_document(self, *, content: bytes, filename: str, mime_type: str | None = None) -> DocumentParseResult:
        started = time.perf_counter()
        validated = validate_document(content=content, filename=filename, mime_type=mime_type)
        image_parser = ImageParser(ocr_service=self.ocr_service, vision_client=self.vision_client)
        if validated.file_type is DocumentFileType.IMAGE:
            blocks = await image_parser.parse(content=content, filename=filename, mime_type=validated.mime_type)
            page_count = 1
        elif validated.file_type is DocumentFileType.PDF:
            pdf_parser = PDFParser(image_parser=image_parser)
            page_count = await pdf_parser.page_count(content=content)
            blocks = await pdf_parser.parse(content=content, filename=filename, mime_type=validated.mime_type)
        else:
            pptx_parser = PPTXParser(image_parser=image_parser)
            page_count = await pptx_parser.page_count(content=content)
            blocks = await pptx_parser.parse(content=content, filename=filename, mime_type=validated.mime_type)
        for index, block in enumerate(blocks, start=1):
            block.block_index = index
        return DocumentParseResult(
            status=ParseStatus.SUCCESS if blocks else ParseStatus.FAILED,
            filename=filename, file_type=validated.file_type, mime_type=validated.mime_type,
            content_sha256=validated.sha256,
            parser_version=_parser_version(),
            page_count=page_count, block_count=len(blocks), blocks=blocks,
            processing_time_ms=int((time.perf_counter() - started) * 1000),
        )


def _parser_version() -> str:
    configured = os.getenv("DOCUMENT_PARSER_VERSION", "document-parser-v3").strip()
    return configured or "document-parser-v3"
