"""Cost gates for the repository's pytest suite."""

from __future__ import annotations

import os

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Require an explicit CLI acknowledgement in addition to environment gates."""
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Allow tests marked live when live environment gates are also set.",
    )
    parser.addoption(
        "--run-full-eval",
        action="store_true",
        default=False,
        help="Allow tests marked full_eval when full environment gates are also set.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip any potentially costly test unless every gate is explicit."""
    live_authorized = bool(
        config.getoption("--run-live")
        and os.environ.get("ODR_EVAL_MODE", "smoke").lower() == "live"
        and os.environ.get("RUN_LIVE_RESEARCH") == "1"
    )
    full_authorized = bool(
        config.getoption("--run-full-eval")
        and os.environ.get("ODR_EVAL_MODE", "smoke").lower() == "full"
        and os.environ.get("RUN_FULL_EVAL") == "1"
    )
    skip_live = pytest.mark.skip(reason="live evaluation cost gates are closed")
    skip_full = pytest.mark.skip(reason="full evaluation cost gates are closed")
    for item in items:
        if "live" in item.keywords and not live_authorized:
            item.add_marker(skip_live)
        if "full_eval" in item.keywords and not full_authorized:
            item.add_marker(skip_full)
