from __future__ import annotations

import asyncio
import io
import os

from .image_parser import ImageParser
from .models import DocumentBlock, DocumentFileType, ProcessingMode, SourceElementType


class PPTXParser:
    def __init__(self, *, image_parser: ImageParser) -> None:
        self.image_parser = image_parser

    async def parse(self, *, content: bytes, filename: str, mime_type: str) -> list[DocumentBlock]:
        return await asyncio.to_thread(self._parse_sync, content, filename, mime_type)

    def _parse_sync(self, content: bytes, filename: str, mime_type: str) -> list[DocumentBlock]:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        try:
            presentation = Presentation(io.BytesIO(content))
        except Exception as exc:
            raise ValueError("pptx document could not be parsed") from exc
        if len(presentation.slides) > _int_env("DOCUMENT_MAX_PPT_SLIDES", 100):
            raise ValueError("pptx document exceeds slide limit")
        blocks: list[DocumentBlock] = []
        for page_number, slide in enumerate(presentation.slides, start=1):
            native_text = "\n".join(
                shape.text.strip() for shape in slide.shapes if getattr(shape, "has_text_frame", False) and shape.text.strip()
            )
            if native_text:
                blocks.append(DocumentBlock(
                    filename=filename, file_type=DocumentFileType.PPTX, page_number=page_number, block_index=1,
                    text=native_text, processing_mode=ProcessingMode.PPT_NATIVE_TEXT,
                    source_element=SourceElementType.PPT_TEXT_SHAPES,
                ))
            for image_index, shape in enumerate((s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE), start=1):
                image_blocks = asyncio.run(self.image_parser.parse(
                    content=shape.image.blob, filename=filename, mime_type=shape.image.content_type,
                    page_number=page_number, file_type=DocumentFileType.PPTX,
                    processing_mode=ProcessingMode.PPT_OCR, source_element=SourceElementType.PPT_EMBEDDED_IMAGE,
                    source_element_index=image_index,
                    image_coverage_ratio=(shape.width * shape.height) / (presentation.slide_width * presentation.slide_height),
                ))
                blocks.extend(image_blocks)
        return blocks


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
