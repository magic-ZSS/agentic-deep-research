"""Lazy, optional bridge to DeepEval public test-case contracts."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import metadata
from typing import Any

from open_deep_research.evaluation.metrics import evaluate_smoke
from open_deep_research.evaluation.models import BaselineCase, BaselineRunRecord, MetricResult


EXPECTED_DEEPEVAL_VERSION = "4.1.1"
_DEEPEVAL_IMPORT_LOCK = threading.Lock()
_DEEPEVAL_SAFE_IMPORT_ENV = {
    "DEEPEVAL_DISABLE_DOTENV": "1",
    "DEEPEVAL_DISABLE_LEGACY_KEYFILE": "1",
    "DEEPEVAL_TELEMETRY_OPT_OUT": "1",
    "DEEPEVAL_UPDATE_WARNING_OPT_IN": "0",
    "ERROR_REPORTING": "0",
    "CONFIDENT_TRACING_ENABLED": "NO",
    "CONFIDENT_OPEN_BROWSER": "0",
    "DEEPEVAL_NO_INSPECT_PROMPT": "1",
    "DEEPEVAL_FILE_SYSTEM": "READ_ONLY",
}
_DEEPEVAL_IMPORT_KEYS_TO_HIDE = ("CONFIDENT_API_KEY", "DEEPEVAL_RESULTS_FOLDER")
_DEEPEVAL_IMPORT_KEYS_TO_RESTORE = ("GRPC_VERBOSITY", "GRPC_TRACE")
_MISSING = object()


class DeepEvalUnavailableError(RuntimeError):
    """Raised only when an explicitly requested DeepEval conversion is unavailable."""


def deepeval_version() -> str | None:
    """Inspect installed metadata without importing DeepEval or starting telemetry."""
    try:
        return metadata.version("deepeval")
    except metadata.PackageNotFoundError:
        return None


def is_deepeval_available() -> bool:
    """Return whether the optional dependency is installed."""
    return deepeval_version() is not None


@contextmanager
def _guarded_deepeval_import() -> Iterator[None]:
    """Disable dotenv, uploads, tracing, and writable caches during lazy import."""
    with _DEEPEVAL_IMPORT_LOCK:
        keys = [
            *_DEEPEVAL_SAFE_IMPORT_ENV,
            *_DEEPEVAL_IMPORT_KEYS_TO_HIDE,
            *_DEEPEVAL_IMPORT_KEYS_TO_RESTORE,
        ]
        previous = {key: os.environ.get(key, _MISSING) for key in keys}
        try:
            os.environ.update(_DEEPEVAL_SAFE_IMPORT_ENV)
            for key in _DEEPEVAL_IMPORT_KEYS_TO_HIDE:
                os.environ.pop(key, None)
            yield
        finally:
            for key, value in previous.items():
                if value is _MISSING:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(value)


def to_deepeval_case(case: BaselineCase, run: BaselineRunRecord) -> Any:
    """Convert a completed record via a lazy import of DeepEval's public API."""
    installed = deepeval_version()
    if installed is None:
        raise DeepEvalUnavailableError(
            "DeepEval is optional and not installed; install the 'eval' extra to "
            "request LLMTestCase conversion. Offline smoke metrics do not require it."
        )
    if installed != EXPECTED_DEEPEVAL_VERSION:
        raise DeepEvalUnavailableError(
            f"DeepEval {installed} is installed, but Phase 0 expects "
            f"{EXPECTED_DEEPEVAL_VERSION}; use the pinned 'eval' extra."
        )

    try:
        with _guarded_deepeval_import():
            from deepeval.test_case import LLMTestCase  # type: ignore[import-not-found]

            return LLMTestCase(
                input=case.prompt,
                actual_output=run.output or "",
                name=case.id,
                token_cost=run.telemetry.estimated_cost,
                completion_time=run.telemetry.wall_time_ms / 1000,
                tags=list(dict.fromkeys([case.difficulty.value, *case.tags])),
                metadata={
                    "schema_version": run.schema_version,
                    "case_id": case.id,
                    "run_id": run.run_id,
                    "expected_requirements": [
                        requirement.model_dump(mode="json")
                        for requirement in case.expected_requirements
                    ],
                    "tokens": {
                        "input": run.telemetry.input_tokens,
                        "output": run.telemetry.output_tokens,
                        "total": run.telemetry.total_tokens,
                    },
                    "tool_requests_by_name": run.telemetry.tool_requests_by_name,
                    "tool_calls_by_name": run.telemetry.tool_calls_by_name,
                    "status": run.telemetry.status.value,
                    "artifact_ref_count": len(run.artifact_refs),
                },
            )
    except (ImportError, AttributeError) as exc:
        raise DeepEvalUnavailableError(
            "DeepEval 4.1.1 is installed but its public LLMTestCase API could not "
            "be imported. Reinstall the pinned 'eval' extra."
        ) from exc


class EvaluationAdapter:
    """Stable project-owned facade used by later evaluation phases."""

    @staticmethod
    def to_deepeval_case(case: BaselineCase, run: BaselineRunRecord) -> Any:
        """Delegate to the optional DeepEval conversion."""
        return to_deepeval_case(case, run)

    @staticmethod
    def evaluate_smoke(
        case: BaselineCase, run: BaselineRunRecord
    ) -> list[MetricResult]:
        """Run project-owned deterministic metrics without DeepEval."""
        return evaluate_smoke(case, run)
