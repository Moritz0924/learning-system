from __future__ import annotations

import re

from .domain import QueryAnalysis


_TOKEN_PATTERN = re.compile(r"[\w.:-]+", re.UNICODE)
_EXACT_TERM_PATTERN = re.compile(
    r"(?<![\w])(?:"
    r"[A-Za-z_][A-Za-z0-9_.:-]*(?=\(\))"
    r"|[A-Z][A-Z0-9]*(?:[_-][A-Z0-9]+)+"
    r"|[A-Za-z][A-Za-z0-9_]*\.[A-Za-z0-9_.]+"
    r")(?:\(\))?"
)


class QueryAnalyzer:
    def analyze(self, query: str) -> QueryAnalysis:
        normalized = " ".join(query.split())
        if not normalized:
            raise ValueError("retrieval query is required")
        exact_terms = tuple(
            dict.fromkeys(match.group(0).removesuffix("()") for match in _EXACT_TERM_PATTERN.finditer(normalized))
        )
        return QueryAnalysis(
            original_query=query.strip(),
            normalized_query=normalized,
            tokens=tuple(match.group(0) for match in _TOKEN_PATTERN.finditer(normalized)),
            exact_terms=exact_terms,
        )
