"""Run deterministic smoke or explicitly authorized full evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from open_deep_research.evaluation.runner import (  # noqa: E402
    require_full_run,
    run_smoke,
)


def parser() -> argparse.ArgumentParser:
    """Build the evaluation CLI parser."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    result.add_argument("--variants", default="all")
    result.add_argument("--dataset-version", default="v1")
    result.add_argument("--repeats", type=int, default=1)
    result.add_argument("--confirm-cost", action="store_true")
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    """Run one evaluation mode and return a process exit code."""
    args = parser().parse_args(argv)
    selected = None if args.variants == "all" else args.variants.split(",")
    if args.mode == "full":
        try:
            require_full_run(confirm_cost=args.confirm_cost, repeats=args.repeats)
        except (PermissionError, RuntimeError, ValueError) as exc:
            sys.stderr.write(
                json.dumps(
                    {
                        "status": "not_run_no_authorization",
                        "mode": "full",
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            return 3
        sys.stderr.write(
            json.dumps(
                {
                    "status": "blocked",
                    "mode": "full",
                    "message": (
                        "authorization gates are open, but live execution must be "
                        "started only from an approved Phase 7 session"
                    ),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        return 4
    records = run_smoke(
        project_root=PROJECT_ROOT,
        output_dir=args.output,
        dataset_version=args.dataset_version,
        variant_ids=selected,
    )
    sys.stdout.write(
        json.dumps(
            {
                "status": "completed",
                "mode": "smoke",
                "runs": len(records),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
