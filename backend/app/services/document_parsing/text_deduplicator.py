from __future__ import annotations

import re


class TextDeduplicator:
    def remove_overlapping_lines(self, *, primary_text: str, supplemental_text: str) -> str:
        primary = {_normalized(line) for line in primary_text.splitlines() if _normalized(line)}
        return "\n".join(
            line.strip()
            for line in supplemental_text.splitlines()
            if _normalized(line) and _normalized(line) not in primary
        )


def _normalized(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()
