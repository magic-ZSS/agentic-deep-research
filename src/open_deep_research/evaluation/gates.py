"""Central authorization gates for costly evaluation-only entry points."""

from __future__ import annotations

import os


class EvaluationAuthorizationError(RuntimeError):
    """Raised before an external evaluation starts without explicit authorization."""


def require_full_eval_authorization() -> None:
    """Require both full mode and its dedicated environment switch."""
    missing: list[str] = []
    if os.environ.get("ODR_EVAL_MODE", "smoke").lower() != "full":
        missing.append("ODR_EVAL_MODE=full")
    if os.environ.get("RUN_FULL_EVAL") != "1":
        missing.append("RUN_FULL_EVAL=1")
    if missing:
        raise EvaluationAuthorizationError(
            "Full evaluation was not started; missing: " + ", ".join(missing)
        )
