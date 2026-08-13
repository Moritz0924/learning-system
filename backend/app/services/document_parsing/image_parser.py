from __future__ import annotations

from dataclasses import dataclass

from .fallback_policy import VisionFallbackPolicy
from .models import (
    DocumentBlock,
    DocumentBlockType,
    DocumentFileType,
    OCRResult,
    ProcessingMode,
    SourceElementType,
    VisionEnrichmentStatus,
    VisionContext,
)
from .text_deduplicator import TextDeduplicator


@dataclass(frozen=True)
class ImageParseArtifacts:
    ocr: OCRResult | None
    final_text: str
    vision_status: VisionEnrichmentStatus | None
    vision_confidence: float | None
    warnings: tuple[str, ...] = ()


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
        image_coverage_ratio: float | None = None, structured: bool = False,
    ) -> list[DocumentBlock]:
        artifacts = await self.extract_artifacts(
            content=content,
            filename=filename,
            mime_type=mime_type,
            page_number=page_number,
            file_type=file_type,
            source_element=source_element,
            image_coverage_ratio=image_coverage_ratio,
        )
        if not artifacts.final_text:
            return []
        ocr = artifacts.ocr
        return [DocumentBlock(
            filename=filename, file_type=file_type, page_number=page_number, block_index=1, text=artifacts.final_text,
            processing_mode=processing_mode, source_element=source_element, source_element_index=source_element_index,
            ocr_confidence=ocr.confidence if ocr is not None else None,
            vision_confidence=artifacts.vision_confidence,
            vision_enriched=(
                artifacts.vision_status is VisionEnrichmentStatus.SUCCESS
                and artifacts.final_text != (ocr.text.strip() if ocr is not None else "")
            ),
            vision_enrichment_status=artifacts.vision_status or VisionEnrichmentStatus.NOT_NEEDED,
            block_type=(DocumentBlockType.IMAGE_DESCRIPTION if structured else DocumentBlockType.UNKNOWN),
        )]

    async def extract_artifacts(
        self,
        *,
        content: bytes,
        filename: str,
        mime_type: str,
        page_number: int = 1,
        file_type: DocumentFileType = DocumentFileType.IMAGE,
        source_element: SourceElementType = SourceElementType.IMAGE_FILE,
        image_coverage_ratio: float | None = None,
    ) -> ImageParseArtifacts:
        warnings: list[str] = []
        try:
            ocr: OCRResult | None = await self.ocr_service.recognize_bytes(content, filename=filename)
        except Exception:
            ocr = None
            warnings.append("ocr_unavailable")
        text = ocr.text.strip() if ocr is not None else ""
        status = VisionEnrichmentStatus.NOT_NEEDED
        vision_confidence = None
        if self.vision_client and self.fallback_policy.should_enrich(
            ocr_result=ocr or OCRResult(text="", confidence=None, word_count=0, text_char_count=0),
            file_type=file_type,
            page_number=page_number,
            image_coverage_ratio=image_coverage_ratio,
            complex_visual_hint=bool(image_coverage_ratio and image_coverage_ratio >= 0.7),
        ):
            try:
                vision = await self.vision_client.analyze_image(content, mime_type=mime_type, context=VisionContext(
                    filename=filename,
                    file_type=file_type,
                    page_number=page_number,
                    source_element=source_element,
                    existing_ocr_text=text,
                    existing_ocr_confidence=ocr.confidence if ocr is not None else None,
                ))
                status = vision.status
                vision_confidence = vision.confidence
                supplemental = self.deduplicator.remove_overlapping_lines(
                    primary_text=text,
                    supplemental_text=vision.supplemental_text,
                )
                if supplemental:
                    text = "\n".join(part for part in (text, supplemental) if part)
            except Exception:
                status = VisionEnrichmentStatus.FAILED
                warnings.append("vision_unavailable")
        return ImageParseArtifacts(
            ocr=ocr,
            final_text=text,
            vision_status=status,
            vision_confidence=vision_confidence,
            warnings=tuple(warnings),
        )
