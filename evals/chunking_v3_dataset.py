from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .chunking_v3 import (
    ChunkingDataset,
    ChunkingDocument,
    ChunkingQuery,
    EvidenceAnchor,
    canonical_dataset_hash,
    canonical_gold_hash,
)


@dataclass(frozen=True)
class FixtureBundle:
    dataset: ChunkingDataset
    sources: dict[str, str]


@dataclass(frozen=True)
class _DocumentSpec:
    source_type: str
    split: str
    language: str
    title: str
    primary: str
    support: str
    continuation: str
    transition: str
    keywords: str


def build_fixture_bundle() -> FixtureBundle:
    """Build the promotion-candidate, leakage-safe ablation-v2 fixture.

    The sources are intentionally stored as independent specifications instead
    of cloning a shared synthetic template across Development and Test.
    """
    documents: list[ChunkingDocument] = []
    queries: list[ChunkingQuery] = []
    anchors: list[EvidenceAnchor] = []
    sources: dict[str, str] = {}
    boundaries: dict[str, tuple[tuple[str, str], ...]] = {}
    for number, spec in enumerate(_SPECS, start=1):
        document_id = f"ablation-v2-{number:03d}"
        extension = {"markdown": "md", "pdf": "pdf", "pptx": "pptx", "text": "txt"}[spec.source_type]
        filename = f"{document_id}.{extension}"
        source = _source(spec, number)
        sources[document_id] = source
        documents.append(ChunkingDocument(
            document_id=document_id,
            filename=filename,
            split=spec.split,
            source_type=spec.source_type,
            source_sha256=_source_hash(source),
            language=spec.language,
            template_family=f"ablation-v2-{spec.split}-source-v1",
        ))
        primary_id = f"anchor-{number:03d}-primary"
        support_id = f"anchor-{number:03d}-support"
        primary_page = 1 if spec.split == "development" else 2
        support_page = 2 if spec.split == "development" else 1
        anchors.extend((
            EvidenceAnchor.create(
                anchor_id=primary_id,
                document_id=document_id,
                text=spec.primary,
                page_or_slide=primary_page,
                char_start=source.index(spec.primary),
                char_end=source.index(spec.primary) + len(spec.primary),
                source_locator=f"{document_id}:source:primary",
            ),
            EvidenceAnchor.create(
                anchor_id=support_id,
                document_id=document_id,
                text=spec.support,
                page_or_slide=support_page,
                char_start=source.index(spec.support),
                char_end=source.index(spec.support) + len(spec.support),
                source_locator=f"{document_id}:source:support",
            ),
        ))
        special_type = ("table", "code", "heading_scoped", "distractor", "repeated_evidence")[(number - 1) % 5]
        queries.extend((
            ChunkingQuery(
                query_id=f"q-{number:03d}-single",
                document_id=document_id,
                split=spec.split,
                query=_single_query(spec),
                gold_evidence_anchors=(primary_id,),
                query_type="single_evidence",
            ),
            ChunkingQuery(
                query_id=f"q-{number:03d}-multi",
                document_id=document_id,
                split=spec.split,
                query=_multi_query(spec),
                gold_evidence_anchors=(primary_id, support_id),
                query_type="multi_evidence",
            ),
            ChunkingQuery(
                query_id=f"q-{number:03d}-cross",
                document_id=document_id,
                split=spec.split,
                query=_cross_query(spec),
                gold_evidence_anchors=(primary_id, support_id),
                query_type="cross_page" if spec.source_type in {"pdf", "pptx"} else "cross_paragraph",
            ),
            ChunkingQuery(
                query_id=f"q-{number:03d}-{special_type}",
                document_id=document_id,
                split=spec.split,
                query=_special_query(spec, special_type),
                gold_evidence_anchors=(primary_id,),
                query_type=special_type,
            ),
        ))
        boundaries[document_id] = ((
            f"{document_id}:source:primary",
            f"{document_id}:source:support",
        ),)
    dataset_hash = canonical_dataset_hash(documents, queries)
    gold_hash = canonical_gold_hash(anchors, boundaries)
    return FixtureBundle(
        dataset=ChunkingDataset(
            dataset_version="chunking-v3-ablation-v2",
            documents=tuple(documents),
            queries=tuple(queries),
            anchors=tuple(anchors),
            topic_boundaries=boundaries,
            dataset_hash=dataset_hash,
            gold_hash=gold_hash,
        ),
        sources=sources,
    )


def _source(spec: _DocumentSpec, number: int) -> str:
    if spec.split == "test":
        return _test_source(spec, number)
    return _development_source(spec, number)


def _development_source(spec: _DocumentSpec, number: int) -> str:
    parts = [
        f"# {spec.title}",
        spec.primary,
        spec.continuation,
        (
            f"Boundary candidate one for case {number} stays with {spec.title}. "
            f"Boundary candidate two for case {number} adds a gradual clarification. "
            f"Boundary candidate three for case {number} preserves the same topic. "
            f"Boundary candidate four for case {number} introduces a comparable condition. "
            f"Boundary candidate five for case {number} resolves the condition. "
            f"Boundary candidate six for case {number} closes the continuation. "
            f"Boundary candidate seven for case {number} prepares the abrupt transition."
        ),
        f"## Evidence ledger {number}\n{spec.support}",
        spec.transition,
        f"- Diagnostic vocabulary: {spec.keywords}.",
        f"Case {number} diagnostic note records a MAD=0 fallback region for {spec.title}.",
        "| signal | interpretation |\n| --- | --- |\n"
        f"| case-{number:03d} | {spec.title} |",
        "```text\n"
        f"probe_{number:03d} = '{spec.keywords.split(',')[0].strip()}'\n"
        "```",
    ]
    if spec.title == "长中文段落压缩":
        parts.append("".join(
            "这是用于检验长中文段落分割的连续说明，必须在完整语义边界保留证据而不能依据空白字符截断。"
            for _ in range(42)
        ))
    if spec.title == "Table provenance ledger":
        parts.append("| oversized evidence | value |\n| --- | --- |\n| " + "table-cell-" * 180 + " | retained |");
    if spec.title == "Code fence recovery":
        parts.append("```python\n" + "x" * 2400 + "\n```")
    return "\n\n".join(parts)


def _test_source(spec: _DocumentSpec, number: int) -> str:
    parts = [
        f"HELD-OUT TEST RECORD: {spec.title}",
        f"Support evidence\n{spec.support}",
        spec.transition,
        "| held-out signal | interpretation |\n| --- | --- |\n"
        f"| test-{number:03d} | {spec.title} |",
        (
            f"Held-out observation one for case {number} establishes the support context. "
            f"Held-out observation two for case {number} tests a gradual clarification. "
            f"Held-out observation three for case {number} preserves the context. "
            f"Held-out observation four for case {number} introduces a comparable condition. "
            f"Held-out observation five for case {number} resolves the condition. "
            f"Held-out observation six for case {number} prepares the claim. "
            f"Held-out observation seven for case {number} closes the sequence."
        ),
        f"## Held-out claim {number}\n{spec.primary}",
        spec.continuation,
        f"- Held-out diagnostic vocabulary: {spec.keywords}.",
        f"Test case {number} records a fixed evaluation decision for {spec.title}.",
        "```text\n"
        f"held_out_probe_{number:03d} = '{spec.keywords.split(',')[0].strip()}'\n"
        "```",
    ]
    return "\n\n".join(parts)


def _single_query(spec: _DocumentSpec) -> str:
    if spec.split == "test":
        return f"Held-out claim for {spec.title}: what main finding is supported?"
    return f"Calibrate the main finding in {spec.title}."


def _multi_query(spec: _DocumentSpec) -> str:
    if spec.split == "test":
        return f"Held-out synthesis for {spec.title}: connect support to the main claim."
    return f"Calibrate the connection between the main finding and support for {spec.title}."


def _cross_query(spec: _DocumentSpec) -> str:
    if spec.split == "test":
        return f"Held-out cross-section check for {spec.title}: which continuation joins the evidence?"
    return f"Calibrate which continuation across sections explains {spec.title}."


def _special_query(spec: _DocumentSpec, query_type: str) -> str:
    query = {
        "table": f"What table signal identifies {spec.title}?",
        "code": f"Which probe token belongs to {spec.title}?",
        "heading_scoped": f"Under the evidence ledger, what finding is recorded for {spec.title}?",
        "distractor": f"Ignore the abrupt transition; what is the verified finding for {spec.title}?",
        "repeated_evidence": f"Even if the same evidence repeats, what single finding answers {spec.title}?",
    }[query_type]
    return (
        f"Held-out special check for {spec.title}: {query}"
        if spec.split == "test"
        else f"Calibrate the special case for {spec.title}: {query}"
    )


def _source_hash(source: str) -> str:
    return hashlib.sha256(source.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def dataset_asset_payloads(bundle: FixtureBundle | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = bundle or build_fixture_bundle()
    dataset = bundle.dataset
    documents = dataset.documents
    manifest = {
        "dataset_version": dataset.dataset_version,
        "generator": "evals.chunking_v3_dataset.build_fixture_bundle",
        "document_split": {
            "development_documents": sum(item.split == "development" for item in documents),
            "test_documents": sum(item.split == "test" for item in documents),
            "unit_of_split": "document",
        },
        "query_split": {
            "development_queries": sum(item.split == "development" for item in dataset.queries),
            "test_queries": sum(item.split == "test" for item in dataset.queries),
        },
        "source_types": {
            source_type: sum(item.source_type == source_type for item in documents)
            for source_type in ("markdown", "pdf", "pptx", "text")
        },
        "languages": {
            language: sum(item.language == language for item in documents)
            for language in ("zh", "en", "mixed")
        },
        "query_types": sorted({item.query_type for item in dataset.queries}),
        "template_leakage": {
            "validator": "normalized document, paragraph fingerprint, cross-split lexical similarity",
            "threshold": 0.82,
            "rule": "template family cannot cross Development/Test",
        },
        "dataset_hash": dataset.dataset_hash,
        "gold_hash": dataset.gold_hash,
        "gold_identity": "EvidenceAnchor",
        "gold_source_rule": "stable source locators and text spans only; never V2/V3 chunks or source_unit_ids",
        "notes": "PDF/PPTX bytes are deterministically materialized by the evaluation runner before parser invocation.",
    }
    gold = {
        "dataset_version": dataset.dataset_version,
        "gold_hash": dataset.gold_hash,
        "anchors": [anchor.__dict__ for anchor in dataset.anchors],
        "topic_boundaries": {
            document_id: [list(pair) for pair in pairs]
            for document_id, pairs in sorted(dataset.topic_boundaries.items())
        },
        "identity_fields": [
            "anchor_id",
            "document_id",
            "page_or_slide",
            "normalized_text",
            "normalized_text_sha256",
            "char_start",
            "char_end",
            "source_locator",
        ],
        "query_gold_field": "gold_evidence_anchors",
    }
    return manifest, gold


_SPECS = (
    _DocumentSpec("markdown", "development", "en", "Vector cache eviction", "A cache shard evicts least-recent vectors after its evidence window closes.", "The shard records eviction timestamps before replacing the vector payload.", "The same shard continues serving neighboring retrievals until a replacement commits.", "A later section shifts abruptly to audit retention for unrelated archives.", "lattice, eviction, timestamp, payload"),
    _DocumentSpec("markdown", "development", "zh", "中文证据对齐", "中文证据对齐要求引用片段与原始段落的语义边界一致。", "校验器会保存段落偏移量以便复核引用来源。", "同一主题的后续说明强调不要把相邻短句拆散。", "随后话题切换到日志轮换策略，与证据对齐无关。", "语义边界, 偏移量, 引用复核, 段落"),
    _DocumentSpec("markdown", "development", "mixed", "Bilingual rubric routing", "Bilingual rubric routing sends English prompts to a shared evidence ledger.", "中文评分备注保留原始术语，避免翻译改变判定。", "The continuation compares two rubric levels without changing the source evidence.", "The narrative then moves to unrelated GPU queue scheduling.", "rubric, ledger, 中文备注, routing"),
    _DocumentSpec("markdown", "development", "en", "Graph checkpoint repair", "Checkpoint repair restores the last durable graph state before replaying a tool call.", "Replay verification compares the restored checkpoint hash with the audit record.", "A continuation explains why read-only tools can reuse the recovered state.", "An abrupt subsection discusses cafeteria sensor calibration instead.", "checkpoint, replay, hash, read-only"),
    _DocumentSpec("markdown", "development", "zh", "长中文段落压缩", "长中文段落压缩应在完整句子边界处保留关键证据。", "令牌预算计算使用同一分词器而不是空白字符估算。", "后续段落继续解释相邻句之间的主题延续。", "新主题转向报表封面颜色，并不影响压缩结论。", "长段落, 分词器, 预算, 延续"),
    _DocumentSpec("markdown", "development", "mixed", "Table provenance ledger", "Table provenance ledger binds each metric row to a source locator.", "表头在切分后的每个片段中重复，确保读者识别列含义。", "The same-topic continuation links row values to their evidence spans.", "The document then pivots to unrelated notification sounds.", "locator, header, 行证据, metric"),
    _DocumentSpec("markdown", "development", "en", "Code fence recovery", "Code fence recovery rebuilds a complete fence around every oversized line fragment.", "The validator rejects fragments that lose their language marker or closing fence.", "A continuation shows that function boundaries are preferred before line fragments.", "The next topic concerns postal address normalization only.", "fence, function, fragment, marker"),
    _DocumentSpec("markdown", "test", "zh", "标题范围检索", "标题范围检索要求回答只使用当前章节内的可验证证据。", "章节路径会随 chunk metadata 保存，供检索后过滤。", "后续说明强调跨段问题仍需保留两个证据锚点。", "随后改谈备份磁带编号，属于干扰信息。", "标题范围, 章节路径, 锚点, 过滤"),
    _DocumentSpec("markdown", "test", "mixed", "OCR confidence arbitration", "OCR confidence arbitration selects the higher-quality page candidate before chunking.", "当分数相同，系统偏向 OCR 结果以保留可定位的文本块。", "The continuation records native fallback when OCR is unavailable.", "A separate paragraph changes to weather station maintenance.", "ocr, tie, 原生回退, page"),
    _DocumentSpec("markdown", "test", "en", "Semantic MAD threshold", "Semantic MAD threshold chooses a boundary only when the score exceeds calibrated dispersion.", "Zero-dispersion regions use a deterministic fallback threshold for reproducible decisions.", "The continuation counts selected boundaries for activation diagnostics.", "The next paragraph discusses office keycard replacement.", "mad, dispersion, threshold, diagnostics"),
    _DocumentSpec("pdf", "development", "en", "Cross-page dosage evidence", "Cross-page dosage evidence keeps the treatment interval attached to its cited protocol.", "The protocol appendix supplies the monitoring condition needed to interpret the interval.", "The continuation crosses a page because the prior sentence remains grammatically unfinished.", "A new page starts an unrelated section on warehouse lighting.", "dosage, protocol, interval, monitor"),
    _DocumentSpec("pdf", "development", "zh", "PDF 原生文本质量", "PDF 原生文本质量先通过硬门，再比较软质量分数。", "OCR 候选提供文字框时可恢复更可靠的阅读顺序。", "后续说明保留原生文本作为 OCR 不可用时的回退。", "另一页讨论打印机耗材，与质量路由无关。", "硬门, 软分, OCR, 阅读顺序"),
    _DocumentSpec("pdf", "development", "mixed", "Page citation disambiguation", "Page citation disambiguation stores the exact page locator for each retrieved claim.", "中文补充说明要求引用不要把 slide 或 image 误标为 page。", "The continuation compares page ranges with single-page references.", "The topic then jumps to an unrelated keyboard layout.", "page, locator, 中文补充, range"),
    _DocumentSpec("pdf", "development", "en", "Spatial table rejection", "Spatial table rejection refuses prose columns that only resemble a grid.", "Column alignment and repeated row structure are both required before emitting a table block.", "The continuation degrades false candidates to paragraphs without losing text.", "A later page describes museum ticket prices instead.", "spatial, columns, rows, prose"),
    _DocumentSpec("pdf", "development", "zh", "跨页主题延续", "跨页主题延续仅在前句未完成且后句继续同一语义时成立。", "页面变化本身不能被视为强制切分边界。", "后续段落记录关系检查器给出的 continuation 分数。", "接着内容转向会议座位安排，构成突变主题。", "跨页, 未完成, continuation, 突变"),
    _DocumentSpec("pdf", "development", "mixed", "Evidence density accounting", "Evidence density accounting counts each covered anchor exactly once.", "重复命中的中文证据不会再次增加 context density 分子。", "The continuation divides unique evidence tokens by retrieved token totals.", "The next page changes to unrelated printer queue metrics.", "density, unique, 中文证据, tokens"),
    _DocumentSpec("pdf", "development", "en", "Provider batch discipline", "Provider batch discipline embeds semantic units in batches rather than per-unit HTTP calls.", "The telemetry records logical texts separately from physical provider requests.", "The continuation preserves batch behavior while measuring ingestion latency.", "An abrupt appendix discusses bicycle storage rules.", "batch, telemetry, provider, latency"),
    _DocumentSpec("pdf", "test", "zh", "扫描页回退", "扫描页回退在原生文字不足时调用 OCR 生成结构化块。", "没有文字框的 OCR 仍输出段落，不能伪造表格。", "后续说明要求 OCR provider 只调用一次以避免重复成本。", "新页转为园艺灌溉计划，属于干扰项。", "扫描页, OCR, 段落, 单次调用"),
    _DocumentSpec("pdf", "test", "mixed", "Retrieval anchor recall", "Retrieval anchor recall measures unique gold anchors covered by ranked chunks.", "同一锚点多次出现时，中文说明要求 Recall 不重复累加。", "The continuation records each anchor's first-hit rank for EvidenceNDCG.", "The report then diverts to unrelated power consumption charts.", "recall, first-hit, 锚点, ranked"),
    _DocumentSpec("pdf", "test", "en", "Hard boundary preservation", "Hard boundary preservation prevents a tiny merge from crossing code or table regions.", "The guard compares weak semantic boundaries only within the same structural region.", "The continuation confirms every final chunk remains within the token maximum.", "A final page changes to cafeteria menu planning.", "hard, merge, structural, maximum"),
    _DocumentSpec("pptx", "development", "en", "Slide context inheritance", "Slide context inheritance gives slide bodies the title as metadata rather than ordinary prose.", "A new slide resets the prior title context with a soft boundary.", "The continuation keeps list items and tables associated with their current slide.", "The presentation then turns to unrelated travel reimbursements.", "slide, context, soft, metadata"),
    _DocumentSpec("pptx", "development", "zh", "演示文稿表格", "演示文稿表格的列标题必须在切分后继续可见。", "幻灯片引用使用 slide 位置类型而不是 PDF page。", "后续说明将图片描述与文字主体区分保存。", "下一张幻灯片改讲办公室绿植，无关当前证据。", "幻灯片, 表头, slide, 图片描述"),
    _DocumentSpec("pptx", "development", "mixed", "Image caption isolation", "Image caption isolation stores visual OCR as an image-description block.", "中文说明要求独立图片不要继续使用 unknown 类型。", "The continuation preserves the slide locator for visual evidence.", "A later slide discusses unrelated hiring timelines.", "image, caption, unknown, slide"),
    _DocumentSpec("pptx", "test", "en", "Presentation reading order", "Presentation reading order sorts row bands before left-to-right columns.", "The parser retains source element indexes so evidence can be traced to a shape.", "The continuation keeps a title out of normal semantic body units.", "The deck then switches to an unrelated logo refresh.", "row-band, shape, title, order"),
    _DocumentSpec("pptx", "test", "zh", "幻灯片跨页问答", "幻灯片跨页问答需要同时命中标题上下文和后续正文证据。", "检索器应保存每个来源的 slide 编号以支持复核。", "后续内容说明不同幻灯片不能继承旧标题。", "随后转向电梯维护提醒，和问答无关。", "跨页问答, 标题上下文, slide编号, 复核"),
    _DocumentSpec("text", "development", "en", "Plain text anchor spans", "Plain text anchor spans use character offsets without inventing a page number.", "The citation label names the text file and chunk index instead of page one.", "The continuation links neighboring paragraphs through stable source locators.", "The text later discusses unrelated desk reservation rules.", "plain, offsets, text, locator"),
    _DocumentSpec("text", "development", "zh", "纯文本分段", "纯文本分段保留连续段落的原始字符偏移。", "文本引用应显示文件与 chunk，而不是伪造页码。", "后续说明通过稳定锚点支持跨段证据查询。", "之后主题变为茶水间库存，与分段无关。", "纯文本, 字符偏移, 引用, 锚点"),
    _DocumentSpec("text", "development", "mixed", "Token budget stopping", "Token budget stopping stops before adding a chunk that would exceed the limit.", "中文说明强调第一个 oversized chunk 也不能越过预算。", "The continuation reports retrieved_tokens for every budget level.", "A separate paragraph discusses unrelated package deliveries.", "budget, oversized, 中文说明, retrieved_tokens"),
    _DocumentSpec("text", "test", "en", "Explicit index isolation", "Explicit index isolation queries only the supplied completed index-version identifiers.", "It never requires those indexes to be active in production serving state.", "The continuation rejects failed or building versions before vector search.", "The next paragraph changes to unrelated fire drill procedures.", "explicit, index-version, completed, vector"),
    _DocumentSpec("text", "test", "zh", "提升门禁判定", "提升门禁判定要求正确性、语义激活、检索和效率全部通过。", "缺少 provider-backed Test 证据时必须保持 V2 默认。", "后续内容记录 paired bootstrap 的固定随机种子。", "最后转向停车位登记，不影响门禁结论。", "提升门禁, V2默认, bootstrap, provider"),
)
