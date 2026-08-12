from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer


class DocumentFileType(str, Enum):
    PDF = "pdf"
    PPTX = "pptx"
    IMAGE = "image"


class DocumentParsingProfile(str, Enum):
    LEGACY_V2 = "legacy_v2"
    STRUCTURED_V3 = "structured_v3"


class ProcessingMode(str, Enum):
    PPT_NATIVE_TEXT = "ppt_native_text"
    PPT_OCR = "ppt_ocr"
    PDF_TEXT = "pdf_text"
    PDF_OCR = "pdf_ocr"
    IMAGE_OCR = "image_ocr"


class VisionEnrichmentStatus(str, Enum):
    NOT_NEEDED = "not_needed"
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    SKIPPED_DISABLED = "skipped_disabled"
    SKIPPED_LIMIT = "skipped_limit"


class ParseStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class SourceElementType(str, Enum):
    PDF_TEXT_LAYER = "pdf_text_layer"
    PDF_RENDER = "pdf_render"
    PPT_TEXT_SHAPES = "ppt_text_shapes"
    PPT_EMBEDDED_IMAGE = "ppt_embedded_image"
    PPT_SLIDE_RENDER = "ppt_slide_render"
    IMAGE_FILE = "image_file"


class DocumentBlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    CODE = "code"
    TABLE = "table"
    IMAGE_DESCRIPTION = "image_description"
    SLIDE_TITLE = "slide_title"
    SLIDE_BODY = "slide_body"
    UNKNOWN = "unknown"


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x0: float
    y0: float
    x1: float
    y1: float


class BlockStyleSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    font_size: float | None = None
    font_size_ratio: float | None = None
    is_bold: bool = False
    is_monospace: bool = False
    list_level: int | None = Field(default=None, ge=0)


class OCRWord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None


class OCRResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    word_count: int = Field(ge=0)
    text_char_count: int = Field(ge=0)
    words: list[OCRWord] = Field(default_factory=list)


class VisionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    file_type: DocumentFileType
    page_number: int = Field(ge=1)
    source_element: SourceElementType
    existing_ocr_text: str = ""
    existing_ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class VisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplemental_text: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    complex_visual: bool = False
    status: VisionEnrichmentStatus
    model_name: str | None = None
    error_code: str | None = None


class ParseWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    page_number: int | None = None
    source_element: SourceElementType | None = None


class TextQualityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hard_gate_pass: bool
    hard_gate_failures: list[str] = Field(default_factory=list)
    char_count: int = Field(ge=0)
    printable_ratio: float = Field(ge=0.0, le=1.0)
    replacement_count: int = Field(ge=0)
    invalid_control_count: int = Field(ge=0)
    word_count: int = Field(ge=0)
    text_signal_ratio: float = Field(ge=0.0, le=1.0)
    soft_score: float = Field(ge=0.0, le=1.0)
    quality_sufficient: bool


class PDFTextQualityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: str = "pdf-text-quality-v1"
    native: TextQualityAssessment
    ocr: TextQualityAssessment | None = None
    selected: Literal["native", "ocr"]
    reason: Literal[
        "native_quality_sufficient",
        "ocr_better",
        "native_better",
        "ocr_tie_preferred",
        "ocr_unavailable_or_empty",
    ]
    minimum_score: float = Field(ge=0.0, le=1.0)


class DocumentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    file_type: DocumentFileType
    page_number: int = Field(ge=1)
    block_index: int = Field(ge=1)
    text: str
    processing_mode: ProcessingMode
    source_element: SourceElementType
    ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    vision_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    vision_enriched: bool = False
    vision_enrichment_status: VisionEnrichmentStatus = VisionEnrichmentStatus.NOT_NEEDED
    source_element_index: int | None = Field(default=None, ge=1)
    warnings: list[ParseWarning] = Field(default_factory=list)
    text_quality: PDFTextQualityMetadata | None = None
    block_type: DocumentBlockType = DocumentBlockType.UNKNOWN
    heading_level: int | None = Field(default=None, ge=1, le=6)
    bbox: BoundingBox | None = None
    reading_order: int | None = Field(default=None, ge=0)
    structure_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    style_signals: BlockStyleSignals | None = None
    source_char_start: int | None = Field(default=None, ge=0)
    source_char_end: int | None = Field(default=None, ge=0)
    table_header_rows: int | None = Field(default=None, ge=0)

    @model_serializer(mode="wrap")
    def _serialize_compatibly(self, handler):
        data = handler(self)
        if self.block_type is DocumentBlockType.UNKNOWN and all(
            value is None
            for value in (
                self.heading_level,
                self.bbox,
                self.reading_order,
                self.structure_confidence,
                self.style_signals,
                self.source_char_start,
                self.source_char_end,
                self.table_header_rows,
            )
        ):
            for key in (
                "block_type",
                "heading_level",
                "bbox",
                "reading_order",
                "structure_confidence",
                "style_signals",
                "source_char_start",
                "source_char_end",
                "table_header_rows",
            ):
                data.pop(key, None)
        return data


class DocumentParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ParseStatus
    filename: str
    file_type: DocumentFileType
    mime_type: str
    content_sha256: str
    parser_version: str
    page_count: int = Field(ge=0)
    block_count: int = Field(ge=0)
    truncated: bool = False
    blocks: list[DocumentBlock] = Field(default_factory=list)
    warnings: list[ParseWarning] = Field(default_factory=list)
    processing_time_ms: int = Field(ge=0)
