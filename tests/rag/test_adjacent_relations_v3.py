from __future__ import annotations

import pytest

from backend.app.domain.rag.chunking.v3.domain import SemanticUnit
from backend.app.domain.rag.chunking.v3.relations import AdjacentRelationChecker
from backend.app.services.document_parsing.models import DocumentBlockType


def _unit(text: str, *, page: int = 1, order: int = 1) -> SemanticUnit:
    return SemanticUnit(
        unit_id=text, text=text, source_unit_ids=(text,), page_start=page,
        page_end=page, heading_path=("RAG",), block_type=DocumentBlockType.PARAGRAPH,
        order=order,
    )


@pytest.mark.parametrize("marker", ["因此", "此外", "therefore", "for example"])
def test_discourse_continuation_is_bounded_signal(marker: str) -> None:
    result = AdjacentRelationChecker().check(_unit("left"), _unit(f"{marker} right", order=2))

    assert 0 <= result.continuation_score <= 1
    assert result.continuation_score > 0
    assert result.reasons


def test_colon_list_and_cross_page_continuation_are_explainable() -> None:
    result = AdjacentRelationChecker().check(
        _unit("The architecture consists of:"),
        _unit("1. parser\n2. index", page=2, order=2),
    )

    assert result.continuation_score > 0
    assert "colon_explanation" in result.reasons
    assert "ordered_list" in result.reasons
    assert "cross_page_continuation" in result.reasons


def test_relation_signal_does_not_act_as_hard_veto() -> None:
    result = AdjacentRelationChecker().check(_unit("therefore"), _unit("new topic", order=2))

    assert result.continuation_score < 1


def test_cross_page_only_boosts_an_unfinished_left_sentence() -> None:
    complete = AdjacentRelationChecker().check(
        _unit("This concludes the architecture.", page=1),
        _unit("Deployment strategy", page=2, order=2),
    )
    unfinished = AdjacentRelationChecker().check(
        _unit("The system consists of", page=1),
        _unit("three major components.", page=2, order=2),
    )

    assert "cross_page_continuation" not in complete.reasons
    assert "cross_page_continuation" in unfinished.reasons
    assert unfinished.continuation_score > complete.continuation_score
