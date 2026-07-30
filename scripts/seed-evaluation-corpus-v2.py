from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.runner.cli import seed_v2_main

raise SystemExit(seed_v2_main())
