from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.runner.cli import build_chunk_map_main

raise SystemExit(build_chunk_map_main())
