"""Aggregate Phase 7 JSONL records without dropping failures or skips."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from open_deep_research.evaluation.experiment_models import ExperimentRun  # noqa: E402
from open_deep_research.evaluation.reporting import aggregate_runs  # noqa: E402
from open_deep_research.evaluation.storage import load_jsonl  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Aggregate an experiment JSONL file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    source = args.input / "runs.jsonl" if args.input.is_dir() else args.input
    report = aggregate_runs(load_jsonl(source, ExperimentRun))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
