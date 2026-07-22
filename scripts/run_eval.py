"""Run deterministic smoke or explicitly authorized full evaluation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from open_deep_research.evaluation.budget import EvaluationBudgetError  # noqa: E402
from open_deep_research.evaluation.eval_environment import (  # noqa: E402
    EvaluationEnvironmentError,
)
from open_deep_research.evaluation.full_preflight import (  # noqa: E402
    FullPreflightError,
)
from open_deep_research.evaluation.gates import (  # noqa: E402
    EvaluationAuthorizationError,
)
from open_deep_research.evaluation.runner import (  # noqa: E402
    inspect_full_preflight,
    run_authorized_calibration,
    run_authorized_full,
    run_smoke,
)
from open_deep_research.evaluation.source_gate import (  # noqa: E402
    EvaluationSourceGateError,
)


def parser() -> argparse.ArgumentParser:
    """Build the evaluation CLI parser."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--mode", choices=("smoke", "calibration", "full"), default="smoke"
    )
    result.add_argument("--variants", default="all")
    result.add_argument("--dataset-version", default="v1")
    result.add_argument("--repeats", type=int, default=1)
    result.add_argument("--confirm-cost", action="store_true")
    result.add_argument("--max-total-tokens", type=int)
    result.add_argument("--resume", action="store_true")
    result.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate a completed calibration and print projection without external calls",
    )
    result.add_argument(
        "--calibration-output",
        type=Path,
        default=PROJECT_ROOT / "artifacts/evaluation/calibration",
    )
    result.add_argument(
        "--tracking", choices=("local", "langsmith"), default="local"
    )
    result.add_argument("--langsmith-project")
    result.add_argument("--output", type=Path, required=True)
    return result


def _safe_calibration_error(error: Exception) -> dict[str, str]:
    """Expose gate errors, but fingerprint runtime failures without raw payloads."""
    fingerprint = hashlib.sha256(
        f"calibration:{type(error).__module__}:{type(error).__name__}".encode()
    ).hexdigest()
    expected = (
        EvaluationAuthorizationError,
        EvaluationBudgetError,
        EvaluationEnvironmentError,
        EvaluationSourceGateError,
    )
    if isinstance(error, expected):
        message = str(error)
        status = "not_run_no_authorization"
    elif isinstance(error, FileExistsError):
        message = "calibration output already exists; inspect it before --resume"
        status = "not_run_or_stopped_safely"
    else:
        message = (
            "calibration stopped and paid state is unknown; inspect budget.json "
            "and journal.json, then use --resume rather than starting a new output"
        )
        status = "stopped_or_paid_state_unknown"
    return {
        "status": status,
        "error_type": type(error).__name__,
        "error_fingerprint": fingerprint,
        "message": message,
    }


def _safe_full_error(error: Exception) -> dict[str, str]:
    """Keep expected gate messages useful and runtime failures private."""
    fingerprint = hashlib.sha256(
        f"full:{type(error).__module__}:{type(error).__name__}".encode()
    ).hexdigest()
    expected = (
        EvaluationAuthorizationError,
        EvaluationBudgetError,
        EvaluationEnvironmentError,
        EvaluationSourceGateError,
        FullPreflightError,
    )
    if isinstance(error, expected):
        message = str(error)
        status = "not_run_no_authorization"
    elif isinstance(error, FileExistsError):
        message = "full output already exists; review it before --resume"
        status = "not_run_or_stopped_safely"
    else:
        message = (
            "full evaluation stopped and paid state is unknown; inspect budget.json "
            "and journal.json, then use --resume rather than starting a new output"
        )
        status = "stopped_or_paid_state_unknown"
    return {
        "status": status,
        "error_type": type(error).__name__,
        "error_fingerprint": fingerprint,
        "message": message,
    }


def _safe_preflight_error(error: Exception) -> dict[str, str]:
    """Report a read-only preflight failure without leaking private values."""
    fingerprint = hashlib.sha256(
        f"full-preflight:{type(error).__module__}:{type(error).__name__}".encode()
    ).hexdigest()
    expected = (
        EvaluationAuthorizationError,
        EvaluationBudgetError,
        EvaluationEnvironmentError,
        EvaluationSourceGateError,
        FullPreflightError,
    )
    return {
        "status": "preflight_failed_no_external_call",
        "error_type": type(error).__name__,
        "error_fingerprint": fingerprint,
        "message": (
            str(error)
            if isinstance(error, expected)
            else "full preflight failed locally; no paid execution was dispatched"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """Run one evaluation mode and return a process exit code."""
    args = parser().parse_args(argv)
    selected = None if args.variants == "all" else args.variants.split(",")
    if args.preflight_only and args.mode != "full":
        sys.stderr.write(
            json.dumps(
                {
                    "status": "preflight_failed_no_external_call",
                    "message": "--preflight-only is valid only with --mode full",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        return 2
    if args.mode == "calibration":
        try:
            outcome = asyncio.run(
                run_authorized_calibration(
                    project_root=PROJECT_ROOT,
                    output_dir=args.output,
                    dataset_version=args.dataset_version,
                    variant_ids=selected,
                    repeats=args.repeats,
                    requested_max_tokens=args.max_total_tokens,
                    confirm_cost=args.confirm_cost,
                    resume=args.resume,
                )
            )
        except Exception as exc:
            sys.stderr.write(
                json.dumps(
                    {
                        "mode": args.mode,
                        **_safe_calibration_error(exc),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            return 3
        sys.stdout.write(
            json.dumps(asdict(outcome), ensure_ascii=False, default=str) + "\n"
        )
        return 0 if outcome.status == "completed" else 5
    if args.mode == "full":
        if args.preflight_only:
            try:
                payload = inspect_full_preflight(
                    project_root=PROJECT_ROOT,
                    output_dir=args.output,
                    calibration_dir=args.calibration_output,
                    dataset_version=args.dataset_version,
                    variant_ids=selected,
                    repeats=args.repeats,
                    requested_max_tokens=args.max_total_tokens,
                )
            except Exception as exc:
                sys.stderr.write(
                    json.dumps(
                        {"mode": "full_preflight", **_safe_preflight_error(exc)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                return 4
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return 0
        try:
            outcome = asyncio.run(
                run_authorized_full(
                    project_root=PROJECT_ROOT,
                    output_dir=args.output,
                    calibration_dir=args.calibration_output,
                    dataset_version=args.dataset_version,
                    variant_ids=selected,
                    repeats=args.repeats,
                    requested_max_tokens=args.max_total_tokens,
                    confirm_cost=args.confirm_cost,
                    resume=args.resume,
                    tracking=args.tracking,
                    langsmith_project=args.langsmith_project,
                )
            )
        except Exception as exc:
            sys.stderr.write(
                json.dumps(
                    {
                        "mode": "full",
                        **_safe_full_error(exc),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            return 3
        sys.stdout.write(
            json.dumps(asdict(outcome), ensure_ascii=False, default=str) + "\n"
        )
        return 0 if outcome.status == "completed" else 5
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
