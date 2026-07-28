from __future__ import annotations

from pathlib import Path


def test_canonical_text_hash_is_stable_across_line_endings(tmp_path: Path) -> None:
    from evals.runner.hashing import canonical_text_sha256

    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes("第一行\nsecond line\n".encode("utf-8"))
    crlf.write_bytes("第一行\r\nsecond line\r\n".encode("utf-8"))

    assert canonical_text_sha256(lf) == canonical_text_sha256(crlf)
