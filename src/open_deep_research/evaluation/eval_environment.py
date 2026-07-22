"""Fail-closed compatibility checks for paid Phase 7 evaluation."""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from importlib import metadata
from typing import Protocol

EXPECTED_PYTHON = (3, 11)
EXPECTED_PACKAGES = {
    "click": "8.3.1",
    "deepeval": "4.1.1",
    "huggingface-hub": "1.4.1",
}


class EvaluationEnvironmentError(RuntimeError):
    """Reject paid evaluation when its isolated environment is unhealthy."""


class PipCheckRunner(Protocol):
    """Run the local dependency consistency check without shell expansion."""

    def __call__(self) -> subprocess.CompletedProcess[str]:
        """Return the completed local ``pip check`` process."""
        ...


@dataclass(frozen=True, slots=True)
class EvaluationEnvironmentReport:
    """Record the non-secret environment facts used by the paid-run gate."""

    python: str
    packages: dict[str, str]
    pip_check: str
    import_smoke: list[str]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-compatible preflight record."""
        return asdict(self)


def _installed_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in EXPECTED_PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError as exc:
            raise EvaluationEnvironmentError(
                f"required evaluation package is missing: {package}"
            ) from exc
    return versions


def _run_pip_check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        check=False,
        capture_output=True,
        text=True,
    )


def require_evaluation_environment(
    *,
    python_version: tuple[int, int] | None = None,
    package_versions: Mapping[str, str] | None = None,
    pip_check_runner: PipCheckRunner | None = None,
    import_modules: tuple[str, ...] = (
        "deepeval",
        "open_deep_research.evaluation.full_runner",
    ),
    import_runner: Callable[[str], object] | None = None,
) -> EvaluationEnvironmentReport:
    """Require Python 3.11, the compatibility lock, and a clean ``pip check``.

    The injectable facts are intended for deterministic offline tests.  The
    production entry point supplies none of them and therefore inspects only
    the currently running interpreter.
    """
    resolved_python = python_version or (sys.version_info.major, sys.version_info.minor)
    if resolved_python != EXPECTED_PYTHON:
        raise EvaluationEnvironmentError(
            "paid Phase 7 evaluation requires the isolated Python 3.11 environment"
        )

    resolved_packages = dict(
        package_versions if package_versions is not None else _installed_versions()
    )
    mismatches = {
        name: {"expected": expected, "actual": resolved_packages.get(name)}
        for name, expected in EXPECTED_PACKAGES.items()
        if resolved_packages.get(name) != expected
    }
    if mismatches:
        detail = ", ".join(
            f"{name}={item['actual']!r} (expected {item['expected']})"
            for name, item in sorted(mismatches.items())
        )
        raise EvaluationEnvironmentError(
            "evaluation compatibility lock is not satisfied: " + detail
        )

    runner: Callable[[], subprocess.CompletedProcess[str]] = (
        pip_check_runner or _run_pip_check
    )
    result = runner()
    if result.returncode != 0:
        # Dependency output contains package names but should not contain
        # credentials.  Keep the public error stable and concise regardless.
        raise EvaluationEnvironmentError(
            "evaluation environment failed `python -m pip check`; "
            "repair it before any paid call"
        )

    importer = import_runner or importlib.import_module
    imported: list[str] = []
    for module_name in import_modules:
        try:
            importer(module_name)
        except Exception as exc:
            raise EvaluationEnvironmentError(
                f"evaluation import smoke failed for {module_name}; "
                "repair the isolated environment before any paid call"
            ) from exc
        imported.append(module_name)

    return EvaluationEnvironmentReport(
        python=f"{resolved_python[0]}.{resolved_python[1]}",
        packages={name: resolved_packages[name] for name in sorted(EXPECTED_PACKAGES)},
        pip_check="passed",
        import_smoke=imported,
    )
