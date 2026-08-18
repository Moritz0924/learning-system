from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.chunking_v3_dataset import dataset_asset_payloads


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    manifest, gold = dataset_asset_payloads()
    for path, payload in (
        (root / "evals/datasets/chunking_v3_ablation_v2_manifest.json", manifest),
        (root / "evals/datasets/chunking_v3_ablation_v2_gold.json", gold),
    ):
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
