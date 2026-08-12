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
    assert "cross_page" in result.reasons


def test_relation_signal_does_not_act_as_hard_veto() -> None:
    result = AdjacentRelationChecker().check(_unit("therefore"), _unit("new topic", order=2))

    assert result.continuation_score < 1
