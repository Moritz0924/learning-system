from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_assessment_v2_deterministic_evaluation_runner_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/run-assessment-v2-evals.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "assessment-v2 deterministic evaluation: passed" in result.stdout
