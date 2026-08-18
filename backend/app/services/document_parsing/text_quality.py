from __future__ import annotations

import os
import unicodedata

from .models import TextQualityAssessment


_ALLOWED_CONTROLS = {"\n", "\r", "\t"}
_INVALID_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn"}
_MOJIBAKE_PATTERNS = ("â€™", "â€œ", "â€\u009d", "â€“", "â€”", "ï¿½", "Ã", "Â")


def assess_pdf_text(
    text: str,
    *,
    min_chars: int | None = None,
    min_printable_ratio: float | None = None,
    target_chars: int | None = None,
    min_score: float | None = None,
) -> TextQualityAssessment:
    min_chars = min_chars or _positive_int_env("DOCUMENT_PDF_MIN_TEXT_CHARS", 50)
    target_chars = target_chars or _positive_int_env("DOCUMENT_PDF_QUALITY_TARGET_CHARS", 200)
    target_chars = max(target_chars, min_chars)
    min_printable_ratio = _ratio_value(
        min_printable_ratio,
        "DOCUMENT_PDF_MIN_PRINTABLE_RATIO",
        0.95,
    )
    min_score = _ratio_value(min_score, "DOCUMENT_PDF_MIN_QUALITY_SCORE", 0.80)

    non_whitespace = [character for character in text if not character.isspace()]
    char_count = len(non_whitespace)
    replacement_count = text.count("\ufffd")
    invalid_control_count = sum(_is_invalid_control(character) for character in text)
    printable_count = sum(_is_readable(character) for character in text)
    printable_ratio = printable_count / len(text) if text else 0.0

    tokens = _latin_tokens(text)
    word_count = len(tokens)
    linguistic_count = sum(_is_linguistic(character) for character in non_whitespace)
    latin_count = sum(_is_latin(character) or character.isdigit() for character in non_whitespace)
    base_signal_ratio = linguistic_count / char_count if char_count else 0.0
    repeat_ratio = _repeated_character_count(text) / char_count if char_count else 0.0
    fragment_ratio = (
        sum(len(token) == 1 for token in tokens) / word_count
        if word_count
        else 0.0
    )
    latin_share = latin_count / linguistic_count if linguistic_count else 0.0
    mojibake_ratio = _mojibake_character_count(text) / char_count if char_count else 0.0
    anomaly_penalty = min(
        1.0,
        repeat_ratio + 0.5 * fragment_ratio * latin_share + mojibake_ratio,
    )
    # ponytail: Structural heuristics catch extraction damage, not semantic nonsense;
    # add a calibrated language model only if real samples prove it necessary.
    text_signal_ratio = base_signal_ratio * (1.0 - anomaly_penalty)

    char_score = min(char_count / max(target_chars, 1), 1.0)
    base_score = 0.25 * char_score + 0.20 * printable_ratio + 0.50 * text_signal_ratio
    latin_dominant = bool(linguistic_count and latin_count * 2 >= linguistic_count)
    soft_score = (
        base_score + 0.05 * min(word_count / 20, 1.0)
        if latin_dominant
        else base_score / 0.95
    )
    soft_score = min(1.0, max(0.0, soft_score))

    failures: list[str] = []
    if char_count < min_chars:
        failures.append("insufficient_chars")
    if printable_ratio < min_printable_ratio:
        failures.append("low_printable_ratio")
    if replacement_count:
        failures.append("replacement_character")
    if invalid_control_count:
        failures.append("invalid_control_character")
    hard_gate_pass = not failures

    return TextQualityAssessment(
        hard_gate_pass=hard_gate_pass,
        hard_gate_failures=failures,
        char_count=char_count,
        printable_ratio=round(printable_ratio, 6),
        replacement_count=replacement_count,
        invalid_control_count=invalid_control_count,
        word_count=word_count,
        text_signal_ratio=round(text_signal_ratio, 6),
        soft_score=round(soft_score, 6),
        quality_sufficient=hard_gate_pass and soft_score >= min_score,
    )


def _is_invalid_control(character: str) -> bool:
    return character not in _ALLOWED_CONTROLS and unicodedata.category(character) in _INVALID_CATEGORIES


def _is_readable(character: str) -> bool:
    return (
        character != "\ufffd"
        and not _is_invalid_control(character)
        and (character.isprintable() or character in _ALLOWED_CONTROLS)
    )


def _is_linguistic(character: str) -> bool:
    return unicodedata.category(character)[0] in {"L", "N"}


def _is_latin(character: str) -> bool:
    return unicodedata.category(character).startswith("L") and "LATIN" in unicodedata.name(character, "")


def _latin_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    for character in text:
        if _is_latin(character) or character.isdigit():
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def _repeated_character_count(text: str) -> int:
    repeated = 0
    run_length = 0
    previous = ""
    for character in text:
        if character == previous and not character.isspace():
            run_length += 1
            continue
        if run_length >= 4:
            repeated += run_length
        previous = character
        run_length = 1
    if run_length >= 4:
        repeated += run_length
    return repeated


def _mojibake_character_count(text: str) -> int:
    return min(len(text), sum(text.count(pattern) * len(pattern) for pattern in _MOJIBAKE_PATTERNS))


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _ratio_value(value: float | None, name: str, default: float) -> float:
    if value is None:
        try:
            value = float(os.getenv(name, str(default)))
        except ValueError:
            return default
    return value if 0.0 <= value <= 1.0 else default
