"""Capture the Phase 0 environment manifest without external service calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from open_deep_research.evaluation.manifest import capture_manifest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Print the manifest or write it to an explicitly selected local path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--phase-start-commit")
    parser.add_argument("--phase-start-clean", action="store_true", default=None)
    args = parser.parse_args(argv)
    payload = json.dumps(
        capture_manifest(
            PROJECT_ROOT,
            phase_start_commit=args.phase_start_commit,
            phase_start_worktree_clean=args.phase_start_clean,
        ),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output is None:
        sys.stdout.write(payload + "\n")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
