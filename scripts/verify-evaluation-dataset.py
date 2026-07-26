from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.runner.cli import verify_dataset_main

raise SystemExit(verify_dataset_main())
