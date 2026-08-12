from __future__ import annotations

from .domain import SemanticUnit


class ChunkRenderer:
    def render(self, units: tuple[SemanticUnit, ...]) -> str:
        if not units:
            return ""
        first = units[0]
        body = "\n\n".join(unit.text.strip() for unit in units if unit.text.strip())
        if first.block_type.value == "code":
            return body
        if first.block_type.value == "table":
            return body
        heading = "\n".join(first.heading_path)
        return f"{heading}\n\n{body}" if heading else body

