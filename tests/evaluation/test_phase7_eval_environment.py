from __future__ import annotations

import subprocess

import pytest

from open_deep_research.evaluation.eval_environment import (
    EXPECTED_PACKAGES,
    EvaluationEnvironmentError,
    require_evaluation_environment,
)


def _pip_result(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["python", "-m", "pip", "check"],
        returncode=returncode,
        stdout="No broken requirements found.\n" if returncode == 0 else "",
        stderr="dependency conflict\n" if returncode else "",
    )


def test_paid_environment_requires_python311_locked_packages_and_clean_pip() -> None:
    imported: list[str] = []
    report = require_evaluation_environment(
        python_version=(3, 11),
        package_versions=EXPECTED_PACKAGES,
        pip_check_runner=lambda: _pip_result(0),
        import_modules=("deepeval", "full_runner"),
        import_runner=lambda name: imported.append(name),
    )

    assert report.python == "3.11"
    assert report.packages == dict(sorted(EXPECTED_PACKAGES.items()))
    assert report.pip_check == "passed"
    assert report.import_smoke == imported == ["deepeval", "full_runner"]


@pytest.mark.parametrize(
    ("python_version", "packages", "pip_code"),
    [
        ((3, 12), EXPECTED_PACKAGES, 0),
        ((3, 11), {**EXPECTED_PACKAGES, "click": "8.4.2"}, 0),
        ((3, 11), EXPECTED_PACKAGES, 1),
    ],
)
def test_paid_environment_fails_closed_before_external_calls(
    python_version: tuple[int, int], packages: dict[str, str], pip_code: int
) -> None:
    with pytest.raises(EvaluationEnvironmentError):
        require_evaluation_environment(
            python_version=python_version,
            package_versions=packages,
            pip_check_runner=lambda: _pip_result(pip_code),
            import_modules=(),
        )


def test_paid_environment_rejects_windows_import_failure_after_clean_pip() -> None:
    def blocked(module_name: str) -> object:
        raise ImportError(f"application control blocked {module_name}")

    with pytest.raises(EvaluationEnvironmentError, match="import smoke failed"):
        require_evaluation_environment(
            python_version=(3, 11),
            package_versions=EXPECTED_PACKAGES,
            pip_check_runner=lambda: _pip_result(0),
            import_modules=("open_deep_research.evaluation.full_runner",),
            import_runner=blocked,
        )
