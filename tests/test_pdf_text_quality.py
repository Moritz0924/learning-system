from backend.app.services.document_parsing.text_quality import assess_pdf_text


def test_clean_english_text_passes_hard_gates_and_soft_score():
    assessment = assess_pdf_text("alpha beta gamma delta epsilon " * 10)

    assert assessment.char_count == 260
    assert assessment.word_count == 50
    assert assessment.printable_ratio == 1.0
    assert assessment.text_signal_ratio == 1.0
    assert assessment.soft_score == 1.0
    assert assessment.hard_gate_failures == []
    assert assessment.quality_sufficient is True


def test_clean_chinese_text_does_not_duplicate_characters_as_words():
    text = "自适应学习系统通过检索增强生成提供可靠课程内容和引用证据。" * 10

    assessment = assess_pdf_text(text)

    assert assessment.char_count >= 200
    assert assessment.word_count == 0
    assert assessment.soft_score >= 0.8
    assert assessment.quality_sufficient is True


def test_single_latin_math_variable_does_not_penalize_clean_chinese_text():
    text = ("自适应学习系统使用变量x表示掌握度，并根据证据更新学习路径。" * 10)

    assessment = assess_pdf_text(text)

    assert assessment.word_count == 10
    assert assessment.text_signal_ratio >= 0.9
    assert assessment.quality_sufficient is True


def test_short_text_fails_the_character_hard_gate():
    assessment = assess_pdf_text("short readable text")

    assert assessment.hard_gate_pass is False
    assert assessment.hard_gate_failures == ["insufficient_chars"]
    assert assessment.quality_sufficient is False


def test_replacement_and_private_use_characters_fail_hard_gates():
    assessment = assess_pdf_text(("valid lesson content " * 8) + "\ufffd\ue000")

    assert assessment.replacement_count == 1
    assert assessment.invalid_control_count == 1
    assert "replacement_character" in assessment.hard_gate_failures
    assert "invalid_control_character" in assessment.hard_gate_failures
    assert assessment.quality_sufficient is False


def test_symbol_soup_has_too_little_text_signal_to_pass():
    assessment = assess_pdf_text("+-*/=<>[]{}()!?@#$%^&|" * 10)

    assert assessment.hard_gate_pass is True
    assert assessment.text_signal_ratio == 0.0
    assert assessment.soft_score < 0.8
    assert assessment.quality_sufficient is False


def test_repeated_and_fragmented_latin_text_is_penalized():
    repeated = assess_pdf_text("aaaa " * 60)
    fragmented = assess_pdf_text("a b c d e f g h i j " * 15)

    assert repeated.text_signal_ratio == 0.0
    assert repeated.quality_sufficient is False
    assert fragmented.text_signal_ratio == 0.5
    assert fragmented.quality_sufficient is False


def test_quality_assessment_is_deterministic():
    text = "Reliable PDF extraction keeps readable words and stable metadata. " * 5

    assert assess_pdf_text(text) == assess_pdf_text(text)
