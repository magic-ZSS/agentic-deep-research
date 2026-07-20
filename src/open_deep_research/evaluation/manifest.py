"""Capture a secret-free, machine-readable Phase 0 environment manifest."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import platform
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from open_deep_research.configuration import Configuration


MANIFEST_SCHEMA_VERSION = "1.0"
CORE_PACKAGE_NAMES = (
    "open_deep_research",
    "langgraph",
    "langchain-core",
    "langchain",
    "langsmith",
    "pydantic",
    "pytest",
    "deepeval",
)
PROTECTED_CORE_FILES = (
    "src/open_deep_research/deep_researcher.py",
    "src/open_deep_research/prompts.py",
    "src/open_deep_research/utils.py",
    "src/open_deep_research/configuration.py",
    "src/open_deep_research/state.py",
)
ENV_DERIVED_MODEL_FIELDS = {
    "summarization_model",
    "research_model",
    "compression_model",
    "final_report_model",
}


def sha256_file(path: str | Path) -> str:
    """Calculate a file digest without normalizing line endings."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return repr(value)


def _config_defaults() -> dict[str, dict[str, Any]]:
    defaults: dict[str, dict[str, Any]] = {}
    for name, field in Configuration.model_fields.items():
        ui_config = ((field.json_schema_extra or {}).get("metadata") or {}).get(
            "x_oap_ui_config", {}
        )
        environment_present = bool(os.environ.get(name.upper()))
        runtime_default = (
            "[ENV_OVERRIDE_REDACTED]"
            if name in ENV_DERIVED_MODEL_FIELDS and environment_present
            else None
            if name in ENV_DERIVED_MODEL_FIELDS
            else _json_value(field.default)
        )
        defaults[name] = {
            "runtime_default": runtime_default,
            "ui_default": _json_value(ui_config.get("default")),
            "environment_override": name.upper(),
        }
        if name in ENV_DERIVED_MODEL_FIELDS:
            defaults[name]["runtime_default_source"] = "environment_at_import"
            defaults[name]["environment_override_present"] = environment_present
    return defaults


def _package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in CORE_PACKAGE_NAMES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _project_commit(project_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _worktree_clean(project_root: Path) -> bool:
    completed = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return not completed.stdout.strip()


def capture_manifest(
    project_root: str | Path,
    *,
    phase_start_commit: str | None = None,
    phase_start_worktree_clean: bool | None = None,
) -> dict[str, Any]:
    """Capture stable environment facts without keys, values, or full pip freeze."""
    root = Path(project_root).resolve()
    with (root / "pyproject.toml").open("rb") as source:
        pyproject = tomllib.load(source)
    project = pyproject["project"]
    configuration_defaults = _config_defaults()
    commit_at_capture = _project_commit(root)
    worktree_clean_at_capture = _worktree_clean(root)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "phase_start_commit": phase_start_commit or commit_at_capture,
        "project_commit_at_capture": commit_at_capture,
        "phase_start_worktree_clean": (
            worktree_clean_at_capture
            if phase_start_worktree_clean is None
            else phase_start_worktree_clean
        ),
        "worktree_clean_at_capture": worktree_clean_at_capture,
        "runtime": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "conda_environment": os.environ.get("CONDA_DEFAULT_ENV"),
        },
        "project_contract": {
            "requires_python_at_phase_start": ">=3.10",
            "requires_python": project["requires-python"],
            "langgraph_python_version": "3.11",
            "declared_dependencies": project["dependencies"],
            "optional_eval_dependencies": project.get("optional-dependencies", {}).get(
                "eval", []
            ),
            "setuptools_packages": pyproject["tool"]["setuptools"]["packages"],
        },
        "resolved_packages": _package_versions(),
        "configuration_defaults": configuration_defaults,
        "configuration_drift": [
            {
                "field": "allow_clarification",
                "runtime_default": configuration_defaults["allow_clarification"][
                    "runtime_default"
                ],
                "ui_default": configuration_defaults["allow_clarification"][
                    "ui_default"
                ],
                "decision": "[ASK USER] keep unchanged during Phase 0",
            },
            {
                "field": "print_process_info",
                "runtime_default": configuration_defaults["print_process_info"][
                    "runtime_default"
                ],
                "ui_default": configuration_defaults["print_process_info"]["ui_default"],
                "decision": "[ASK USER] keep unchanged during Phase 0",
            },
            {
                "field": "model_defaults",
                "runtime_fields": [
                    "summarization_model",
                    "research_model",
                    "compression_model",
                    "final_report_model",
                ],
                "observation": (
                    "Runtime model defaults come from environment at module import; "
                    "the manifest records only presence/redaction while UI metadata "
                    "declares OpenAI model fallbacks."
                ),
                "decision": "[ASK USER] keep unchanged during Phase 0",
            },
        ],
        "protected_core_sha256": {
            relative_path: sha256_file(root / relative_path)
            for relative_path in PROTECTED_CORE_FILES
        },
        "known_baseline_behavior": {
            "graph": "Supervisor-Researcher with final report generation",
            "evaluation_default": "offline replay; evaluation telemetry disabled",
            "live_requires": [
                "ODR_EVAL_MODE=live",
                "RUN_LIVE_RESEARCH=1",
                "--confirm-cost",
            ],
            "known_core_defects_not_fixed": [
                "Supervisor completion guard contains `or True`",
                "compression retry does not re-invoke the model",
                "unknown researcher tool names can raise KeyError",
            ],
        },
        "capture_process": {
            "command": (
                "conda run --no-capture-output -n open-deep-research "
                "python scripts/capture_baseline_manifest.py --phase-start-commit "
                f"{phase_start_commit or commit_at_capture} --phase-start-clean"
            ),
            "secret_policy": "No environment values, API keys, or full pip freeze stored.",
            "python_executable_recorded": False,
        },
    }
