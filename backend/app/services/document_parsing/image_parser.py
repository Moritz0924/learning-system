from __future__ import annotations

from .fallback_policy import VisionFallbackPolicy
from .models import (
    DocumentBlock,
    DocumentFileType,
    OCRResult,
    ProcessingMode,
    SourceElementType,
    VisionEnrichmentStatus,
    VisionContext,
)
from .text_deduplicator import TextDeduplicator


class ImageParser:
    def __init__(self, *, ocr_service, vision_client=None, fallback_policy: VisionFallbackPolicy | None = None) -> None:
        self.ocr_service = ocr_service
        self.vision_client = vision_client
        self.fallback_policy = fallback_policy or VisionFallbackPolicy()
        self.deduplicator = TextDeduplicator()

    async def parse(
        self, *, content: bytes, filename: str, mime_type: str, page_number: int = 1,
        file_type: DocumentFileType = DocumentFileType.IMAGE, processing_mode: ProcessingMode = ProcessingMode.IMAGE_OCR,
        source_element: SourceElementType = SourceElementType.IMAGE_FILE, source_element_index: int | None = None,
        image_coverage_ratio: float | None = None,
    ) -> list[DocumentBlock]:
        try:
            ocr: OCRResult = await self.ocr_service.recognize_bytes(content, filename=filename)
        except Exception:
            ocr = OCRResult(text="", confidence=None, word_count=0, text_char_count=0)
        if not ocr.text.strip() and not self.vision_client:
            return []
        text = ocr.text.strip()
        vision_confidence = None
        vision_enriched = False
        status = VisionEnrichmentStatus.NOT_NEEDED
        if self.vision_client and self.fallback_policy.should_enrich(
            ocr_result=ocr, file_type=file_type, page_number=page_number,
            image_coverage_ratio=image_coverage_ratio,
            complex_visual_hint=bool(image_coverage_ratio and image_coverage_ratio >= 0.7),
        ):
            vision = await self.vision_client.analyze_image(content, mime_type=mime_type, context=VisionContext(
                filename=filename, file_type=file_type, page_number=page_number, source_element=source_element,
                existing_ocr_text=text, existing_ocr_confidence=ocr.confidence,
            ))
            status = vision.status
            vision_confidence = vision.confidence
            supplemental = self.deduplicator.remove_overlapping_lines(primary_text=text, supplemental_text=vision.supplemental_text)
            if supplemental:
                text = "\n".join(part for part in [text, supplemental] if part)
                vision_enriched = vision.status is VisionEnrichmentStatus.SUCCESS
        if not text:
            return []
        return [DocumentBlock(
            filename=filename, file_type=file_type, page_number=page_number, block_index=1, text=ocr.text.strip(),
            processing_mode=processing_mode, source_element=source_element, source_element_index=source_element_index,
            ocr_confidence=ocr.confidence, vision_confidence=vision_confidence,
            vision_enriched=vision_enriched, vision_enrichment_status=status,
        )]
