"""Run deterministic replay baselines or explicitly authorized live research."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from open_deep_research.evaluation.baseline import (  # noqa: E402
    DEFAULT_CASES_PATH,
    DEFAULT_FIXTURES_DIR,
    live_authorization_refusal,
    run_live_authorized,
    run_replay,
    select_case,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the zero-cost-by-default command-line contract."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument("--case", required=True, dest="case_id")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument(
        "--confirm-cost",
        action="store_true",
        help="Acknowledge that an authorized live case may incur external cost.",
    )
    return parser


async def _run_live(args: argparse.Namespace) -> int:
    refusal = live_authorization_refusal(
        args.case_id,
        confirm_cost=args.confirm_cost,
    )
    if refusal is not None:
        sys.stderr.write(refusal.model_dump_json() + "\n")
        return 3

    case = select_case(args.case_id, args.cases)
    record = await run_live_authorized(
        case,
        args.output,
        confirm_cost=args.confirm_cost,
    )
    sys.stdout.write(record.model_dump_json() + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Execute one case and return a process-friendly exit code."""
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "replay":
            record = run_replay(
                args.case_id,
                args.output,
                cases_path=args.cases,
                fixtures_dir=args.fixtures,
            )
            sys.stdout.write(record.model_dump_json() + "\n")
            return 0
        return asyncio.run(_run_live(args))
    except asyncio.CancelledError:
        sys.stderr.write(
            json.dumps({"status": "cancelled", "case_id": args.case_id}) + "\n"
        )
        return 130
    except KeyboardInterrupt:
        sys.stderr.write(
            json.dumps({"status": "cancelled", "case_id": args.case_id}) + "\n"
        )
        return 130
    except BaseException as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "status": "failed",
                    "case_id": args.case_id,
                    "error_type": type(exc).__name__,
                    "message": "baseline execution failed; external error text omitted",
                }
            )
            + "\n"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
