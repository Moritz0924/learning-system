from __future__ import annotations


def test_sentence_splitter_preserves_chinese_punctuation_without_spaces() -> None:
    from backend.app.domain.rag.chunking.v3.sentence_splitter import SentenceSplitter

    assert SentenceSplitter().split("第一句。第二句！第三句？") == [
        "第一句。",
        "第二句！",
        "第三句？",
    ]


def test_sentence_splitter_handles_english_and_mixed_text() -> None:
    from backend.app.domain.rag.chunking.v3.sentence_splitter import SentenceSplitter

    assert SentenceSplitter().split("First sentence. Second sentence! 第三句。 Fourth?") == [
        "First sentence.",
        "Second sentence!",
        "第三句。",
        "Fourth?",
    ]
