from backend.app.services.document_parsing.models import (
    DocumentBlock,
    DocumentFileType,
    ProcessingMode,
    SourceElementType,
)
from backend.app.services.ocr import build_ocr_result_from_tesseract_data
from backend.app.services.document_parsing.fallback_policy import VisionFallbackPolicy
from backend.app.services.document_parsing.models import OCRResult
from backend.app.services.document_parsing.models import VisionEnrichmentStatus, VisionResult
from backend.app.services.document_parsing.parser import DocumentParser


def _pdf_with_text(*page_texts: str) -> bytes:
    import fitz

    document = fitz.open()
    for text in page_texts:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 550, 750), text)
    content = document.tobytes()
    document.close()
    return content


def test_document_block_serializes_stable_parser_metadata():
    block = DocumentBlock(
        filename="lesson.pdf",
        file_type=DocumentFileType.PDF,
        page_number=3,
        block_index=2,
        text="OCR text",
        processing_mode=ProcessingMode.PDF_OCR,
        source_element=SourceElementType.PDF_RENDER,
        ocr_confidence=0.76,
    )

    assert block.model_dump(mode="json") == {
        "filename": "lesson.pdf",
        "file_type": "pdf",
        "page_number": 3,
        "block_index": 2,
        "text": "OCR text",
        "processing_mode": "pdf_ocr",
        "source_element": "pdf_render",
        "ocr_confidence": 0.76,
        "vision_confidence": None,
        "vision_enriched": False,
        "vision_enrichment_status": "not_needed",
        "source_element_index": None,
        "warnings": [],
        "text_quality": None,
    }


def test_ocr_result_ignores_invalid_words_and_weights_confidence_by_characters():
    result = build_ocr_result_from_tesseract_data(
        {
            "text": ["short", "longerword", "", "ignored"],
            "conf": ["50", "90", "80", "-1"],
            "left": [1, 2, 3, 4],
            "top": [1, 2, 3, 4],
            "width": [1, 2, 3, 4],
            "height": [1, 2, 3, 4],
        }
    )

    assert result.text == "short longerword"
    assert result.word_count == 2
    assert result.confidence == (0.5 * 5 + 0.9 * 10) / 15
    assert [word.text for word in result.words] == ["short", "longerword"]


def test_auto_vision_fallback_triggers_for_low_confidence_ocr(monkeypatch):
    monkeypatch.setenv("OCR_VISION_FALLBACK", "auto")
    monkeypatch.setenv("OCR_MIN_CONFIDENCE", "0.65")
    policy = VisionFallbackPolicy()

    assert policy.should_enrich(
        ocr_result=OCRResult(text="unclear", confidence=0.4, word_count=1, text_char_count=7),
        file_type=DocumentFileType.IMAGE,
        page_number=1,
    )


def test_document_parser_routes_an_image_to_structured_ocr_block():
    image_content = __import__("base64").b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
    )

    class FakeOCR:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            assert filename == "slide.png"
            assert content == image_content
            return OCRResult(text="scanned lesson", confidence=0.8, word_count=2, text_char_count=14)

    result = __import__("asyncio").run(
        DocumentParser(ocr_service=FakeOCR()).parse_document(
            content=image_content, filename="slide.png", mime_type="image/png"
        )
    )

    assert result.status.value == "success"
    assert result.content_sha256
    assert result.blocks[0].processing_mode.value == "image_ocr"
    assert result.blocks[0].text == "scanned lesson"


def test_document_parser_extracts_pdf_text_layer_without_ocr():
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "native PDF lesson text with sufficient content to avoid the OCR fallback path")
    content = document.tobytes()
    document.close()

    result = __import__("asyncio").run(
        DocumentParser().parse_document(content=content, filename="lesson.pdf", mime_type="application/pdf")
    )

    assert result.status.value == "success"
    assert [(block.processing_mode.value, block.page_number) for block in result.blocks] == [("pdf_text", 1)]
    assert "native PDF lesson text" in result.blocks[0].text


def test_high_quality_pdf_text_records_quality_metadata_without_running_ocr():
    class OCRMustNotRun:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            raise AssertionError("OCR must not run for high-quality native PDF text")

    native_text = "alpha beta gamma delta epsilon " * 10
    result = __import__("asyncio").run(
        DocumentParser(ocr_service=OCRMustNotRun()).parse_document(
            content=_pdf_with_text(native_text), filename="native.pdf", mime_type="application/pdf"
        )
    )

    assert len(result.blocks) == 1
    assert result.parser_version == "document-parser-v3"
    block = result.blocks[0]
    assert block.processing_mode is ProcessingMode.PDF_TEXT
    assert block.text_quality.selected == "native"
    assert block.text_quality.reason == "native_quality_sufficient"
    assert block.text_quality.native.quality_sufficient is True
    assert block.text_quality.ocr is None


def test_low_quality_native_pdf_selects_better_ocr_candidate():
    ocr_text = "reliable OCR lesson content with stable readable words " * 8

    class BetterOCR:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            return OCRResult(
                text=ocr_text,
                confidence=0.95,
                word_count=64,
                text_char_count=len(ocr_text),
            )

    result = __import__("asyncio").run(
        DocumentParser(ocr_service=BetterOCR()).parse_document(
            content=_pdf_with_text("brief title"), filename="scan.pdf", mime_type="application/pdf"
        )
    )

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.processing_mode is ProcessingMode.PDF_OCR
    assert block.text == ocr_text.strip()
    assert block.text_quality.selected == "ocr"
    assert block.text_quality.reason == "ocr_better"
    assert block.text_quality.native.quality_sufficient is False
    assert block.text_quality.ocr.quality_sufficient is True


def test_pdf_keeps_better_native_candidate_when_both_candidates_are_low_quality():
    native_text = ("alpha " * 9) + "+++++"

    class WorseOCR:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            return OCRResult(text="aaaa " * 60, confidence=0.9, word_count=60, text_char_count=240)

    result = __import__("asyncio").run(
        DocumentParser(ocr_service=WorseOCR()).parse_document(
            content=_pdf_with_text(native_text), filename="borderline.pdf", mime_type="application/pdf"
        )
    )

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.processing_mode is ProcessingMode.PDF_TEXT
    assert block.text_quality.selected == "native"
    assert block.text_quality.reason == "native_better"
    assert block.text_quality.native.quality_sufficient is False
    assert block.text_quality.ocr.quality_sufficient is False
    assert [warning.code for warning in block.warnings] == ["pdf_text_quality_below_threshold"]


def test_pdf_prefers_ocr_when_candidate_quality_is_tied():
    native_text = ("alpha " * 9) + "+++++"

    class TiedOCR:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            return OCRResult(text=native_text, confidence=0.9, word_count=9, text_char_count=50)

    result = __import__("asyncio").run(
        DocumentParser(ocr_service=TiedOCR()).parse_document(
            content=_pdf_with_text(native_text), filename="tie.pdf", mime_type="application/pdf"
        )
    )

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.processing_mode is ProcessingMode.PDF_OCR
    assert block.text_quality.selected == "ocr"
    assert block.text_quality.reason == "ocr_tie_preferred"


def test_pdf_retains_native_text_when_ocr_is_empty():
    class EmptyOCR:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            return OCRResult(text="", confidence=None, word_count=0, text_char_count=0)

    result = __import__("asyncio").run(
        DocumentParser(ocr_service=EmptyOCR()).parse_document(
            content=_pdf_with_text("brief title"), filename="empty-ocr.pdf", mime_type="application/pdf"
        )
    )

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.processing_mode is ProcessingMode.PDF_TEXT
    assert block.text_quality.selected == "native"
    assert block.text_quality.reason == "ocr_unavailable_or_empty"
    assert [warning.code for warning in block.warnings] == [
        "pdf_ocr_unavailable_or_empty",
        "pdf_text_quality_below_threshold",
    ]


def test_pdf_retains_native_text_when_page_rendering_fails(monkeypatch):
    import fitz

    def fail_render(*args, **kwargs):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(fitz.Page, "get_pixmap", fail_render)
    result = __import__("asyncio").run(
        DocumentParser().parse_document(
            content=_pdf_with_text("brief title"), filename="render-failure.pdf", mime_type="application/pdf"
        )
    )

    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert block.text == "brief title"
    assert block.text_quality.reason == "ocr_unavailable_or_empty"
    assert [warning.code for warning in block.warnings] == [
        "pdf_ocr_unavailable_or_empty",
        "pdf_text_quality_below_threshold",
    ]


def test_blank_parser_version_uses_v3_consistently(monkeypatch):
    monkeypatch.setenv("DOCUMENT_PARSER_VERSION", "   ")

    result = __import__("asyncio").run(
        DocumentParser().parse_document(
            content=_pdf_with_text("alpha beta gamma delta epsilon " * 10),
            filename="version.pdf",
            mime_type="application/pdf",
        )
    )

    assert result.parser_version == "document-parser-v3"


def test_document_parser_extracts_ppt_text_shapes_without_ocr():
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(0, 0, 1_000_000, 1_000_000).text_frame.text = "native slide text"
    buffer = __import__("io").BytesIO()
    presentation.save(buffer)

    result = __import__("asyncio").run(
        DocumentParser().parse_document(
            content=buffer.getvalue(),
            filename="lesson.pptx",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    )

    assert result.status.value == "success"
    assert [(block.processing_mode.value, block.page_number) for block in result.blocks] == [("ppt_native_text", 1)]
    assert result.blocks[0].text == "native slide text"


def test_document_parser_ocr_extracts_embedded_pptx_image():
    from PIL import Image
    from pptx import Presentation

    image = __import__("io").BytesIO()
    Image.new("RGB", (20, 20), "white").save(image, format="PNG")
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_picture(__import__("io").BytesIO(image.getvalue()), 0, 0, 1_000_000, 1_000_000)
    content = __import__("io").BytesIO()
    presentation.save(content)

    class FakeOCR:
        async def recognize_bytes(self, image_content: bytes, *, filename: str) -> OCRResult:
            return OCRResult(text="scanned slide", confidence=0.9, word_count=2, text_char_count=13)

    result = __import__("asyncio").run(
        DocumentParser(ocr_service=FakeOCR()).parse_document(
            content=content.getvalue(), filename="scanned.pptx",
            mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
    )

    assert [(block.processing_mode.value, block.source_element_index) for block in result.blocks] == [("ppt_ocr", 1)]


def test_document_parser_ocr_extracts_scanned_pdf_page():
    import fitz
    from PIL import Image

    image = __import__("io").BytesIO()
    Image.new("RGB", (50, 50), "white").save(image, format="PNG")
    document = fitz.open()
    page = document.new_page()
    page.insert_image(page.rect, stream=image.getvalue())
    content = document.tobytes()
    document.close()

    class FakeOCR:
        async def recognize_bytes(self, image_content: bytes, *, filename: str) -> OCRResult:
            return OCRResult(text="scanned PDF", confidence=0.9, word_count=2, text_char_count=11)

    result = __import__("asyncio").run(
        DocumentParser(ocr_service=FakeOCR()).parse_document(content=content, filename="scan.pdf", mime_type="application/pdf")
    )

    assert [(block.processing_mode.value, block.page_number) for block in result.blocks] == [("pdf_ocr", 1)]


def test_image_parser_persists_non_duplicate_vision_supplemental_text(monkeypatch):
    monkeypatch.setenv("OCR_VISION_FALLBACK", "always")
    image_content = __import__("base64").b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
    )

    class FakeOCR:
        async def recognize_bytes(self, content: bytes, *, filename: str) -> OCRResult:
            return OCRResult(text="OCR heading", confidence=0.9, word_count=2, text_char_count=11)

    class FakeVision:
        async def analyze_image(self, *args, **kwargs) -> VisionResult:
            return VisionResult(
                supplemental_text="OCR heading\nChart label",
                confidence=0.8,
                status=VisionEnrichmentStatus.SUCCESS,
            )

    result = __import__("asyncio").run(
        DocumentParser(ocr_service=FakeOCR(), vision_client=FakeVision()).parse_document(
            content=image_content, filename="diagram.png", mime_type="image/png"
        )
    )

    assert result.blocks[0].text == "OCR heading\nChart label"
    assert result.blocks[0].vision_enriched is True


def test_document_parser_reports_file_page_count_not_block_count():
    import fitz

    document = fitz.open()
    document.new_page().insert_text((72, 72), "first page has text")
    document.new_page()
    document.new_page().insert_text((72, 72), "third page has text")
    content = document.tobytes()
    document.close()

    result = __import__("asyncio").run(
        DocumentParser().parse_document(content=content, filename="three-pages.pdf", mime_type="application/pdf")
    )

    assert result.page_count == 3
    assert result.block_count == 2
