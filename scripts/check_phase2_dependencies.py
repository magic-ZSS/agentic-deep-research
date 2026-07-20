"""Offline Windows/Python compatibility gate for the Phase 2 PaperQA extra.

This script never installs packages, resolves dependencies, accesses the network, or
constructs an LLM/embedding client. A missing optional dependency is an observable
gate result (exit code 2), not a skipped success.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.metadata
import json
import platform
import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_COMMIT = "d7675d7b7eddeb3535e8c260399c5bbeeb818c50"
EXPECTED_VERSIONS: dict[str, str] = {
    "paper-qa": "2026.3.18",
    "paper-qa-pypdf": "2026.3.18",
    "tantivy": "0.26.0",
    "fhaviary": "0.34.0",
    "fhlmi": "0.45.0",
    "litellm": "1.82.4",
}
_EXACT_REQUIREMENT = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*==\s*([A-Za-z0-9_.+!-]+)\s*$"
)
_BANNED_CALL_NAMES = {
    "ask",
    "aquery",
    "agent_query",
    "PaperSearch",
    "GatherEvidence",
    "GenerateAnswer",
    "Complete",
}


def _normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _error(code: str, component: str, message: str) -> dict[str, str]:
    return {"code": code, "component": component, "message": message}


def check_pyproject_pins(project_root: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Require every compatibility-tested package to be exactly pinned."""
    errors: list[dict[str, str]] = []
    path = project_root / "pyproject.toml"
    try:
        project = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return (
            {"path": str(path), "knowledge": [], "pins": {}, "ok": False},
            [_error("pyproject_unreadable", "pyproject", str(exc))],
        )
    requirements = (
        project.get("project", {})
        .get("optional-dependencies", {})
        .get("knowledge", [])
    )
    if not isinstance(requirements, list):
        requirements = []
        errors.append(
            _error(
                "knowledge_extra_invalid",
                "pyproject",
                "project.optional-dependencies.knowledge must be a list",
            )
        )
    pins: dict[str, str] = {}
    non_exact: list[str] = []
    for requirement in requirements:
        if not isinstance(requirement, str):
            non_exact.append(repr(requirement))
            continue
        match = _EXACT_REQUIREMENT.fullmatch(requirement)
        if not match:
            non_exact.append(requirement)
            continue
        name = _normalize_distribution_name(match.group(1))
        if name in pins:
            errors.append(
                _error("duplicate_pin", "pyproject", f"duplicate knowledge pin: {name}")
            )
        pins[name] = match.group(2)
    for name, expected in EXPECTED_VERSIONS.items():
        actual = pins.get(name)
        if actual != expected:
            errors.append(
                _error(
                    "pin_mismatch",
                    "pyproject",
                    f"{name} must be exactly pinned to {expected}; found {actual!r}",
                )
            )
    return (
        {
            "path": str(path),
            "knowledge": requirements,
            "pins": pins,
            "non_exact_entries": non_exact,
            "ok": not errors,
        },
        errors,
    )


def check_reference_commit(project_root: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Verify the reviewed PaperQA source snapshot remains explicitly traceable."""
    path = project_root / "doc" / "reference" / "refs.lock.json"
    errors: list[dict[str, str]] = []
    actual: str | None = None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for repository in payload.get("repositories", []):
            if repository.get("id") == "paper-qa":
                actual = repository.get("commit")
                break
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        errors.append(_error("reference_lock_unreadable", "reference", str(exc)))
    if actual != REFERENCE_COMMIT:
        errors.append(
            _error(
                "reference_commit_mismatch",
                "reference",
                f"expected PaperQA reference {REFERENCE_COMMIT}; found {actual!r}",
            )
        )
    return (
        {
            "path": str(path),
            "expected_commit": REFERENCE_COMMIT,
            "actual_commit": actual,
            "ok": actual == REFERENCE_COMMIT,
        },
        errors,
    )


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def check_adapter_forbidden_apis(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Reject static imports/calls that would embed PaperQA's answer or Agent loop."""
    errors: list[dict[str, str]] = []
    findings: list[dict[str, Any]] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return (
            {"path": str(path), "findings": [], "ok": False},
            [_error("adapter_unreadable", "adapter", str(exc))],
        )
    for node in ast.walk(tree):
        candidate: str | None = None
        kind: str | None = None
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("paperqa.agents"):
                    findings.append(
                        {"kind": "import", "line": node.lineno, "name": alias.name}
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("paperqa.agents"):
                findings.append(
                    {"kind": "import", "line": node.lineno, "name": module}
                )
            for alias in node.names:
                if alias.name in _BANNED_CALL_NAMES:
                    findings.append(
                        {
                            "kind": "import",
                            "line": node.lineno,
                            "name": f"{module}.{alias.name}",
                        }
                    )
        elif isinstance(node, ast.Call):
            candidate = _dotted_name(node.func)
            kind = "call"
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("paperqa.agents"):
                candidate = node.value
                kind = "dynamic_import"
        if candidate and (
            candidate.split(".")[-1] in _BANNED_CALL_NAMES
            or ".agents" in candidate
        ):
            findings.append(
                {"kind": kind, "line": getattr(node, "lineno", None), "name": candidate}
            )
    findings = sorted(
        {json.dumps(item, sort_keys=True): item for item in findings}.values(),
        key=lambda item: (item.get("line") or 0, item["name"]),
    )
    for finding in findings:
        errors.append(
            _error(
                "forbidden_paperqa_api",
                "adapter",
                f"{finding['kind']} {finding['name']} at line {finding['line']}",
            )
        )
    return (
        {"path": str(path), "findings": findings, "ok": not findings},
        errors,
    )


def check_installed_versions(
    version_getter: Callable[[str], str],
) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    """Compare installed distribution metadata against the validated matrix."""
    installed: dict[str, Any] = {}
    missing: list[str] = []
    errors: list[dict[str, str]] = []
    for name, expected in EXPECTED_VERSIONS.items():
        try:
            actual = version_getter(name)
        except importlib.metadata.PackageNotFoundError:
            actual = None
            missing.append(name)
            errors.append(
                _error("missing_distribution", name, f"{name} is not installed")
            )
        except Exception as exc:  # Keep diagnostics structured for broken metadata.
            actual = None
            errors.append(
                _error("metadata_error", name, f"{type(exc).__name__}: {exc}")
            )
        ok = actual == expected
        installed[name] = {"expected": expected, "actual": actual, "ok": ok}
        if actual is not None and not ok:
            errors.append(
                _error(
                    "version_mismatch",
                    name,
                    f"expected {expected}; found {actual}",
                )
            )
    return installed, missing, errors


def check_imports_and_offline_settings(
    module_importer: Callable[[str], ModuleType | Any],
    *,
    temporary_parent: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """Import only local package code and construct explicit offline settings."""
    imports: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    modules: dict[str, Any] = {}
    for module_name in ("paperqa", "paperqa_pypdf", "tantivy"):
        try:
            modules[module_name] = module_importer(module_name)
            imports[module_name] = {"ok": True, "error": None}
        except Exception as exc:
            imports[module_name] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            errors.append(
                _error(
                    "import_failed",
                    module_name,
                    f"{type(exc).__name__}: {exc}",
                )
            )

    paperqa = modules.get("paperqa")
    required_exports: dict[str, bool] = {}
    for export in ("Docs", "Doc", "Text", "Context", "Settings"):
        present = paperqa is not None and getattr(paperqa, export, None) is not None
        required_exports[export] = present
        if not present:
            errors.append(
                _error(
                    "missing_export",
                    "paperqa",
                    f"paperqa.{export} is unavailable",
                )
            )
    imports["paperqa_exports"] = required_exports

    pypdf = modules.get("paperqa_pypdf")
    parser_export = pypdf is not None and callable(
        getattr(pypdf, "parse_pdf_to_pages", None)
    )
    imports["paperqa_pypdf.parse_pdf_to_pages"] = {"ok": parser_export}
    if not parser_export:
        errors.append(
            _error(
                "missing_export",
                "paperqa_pypdf",
                "parse_pdf_to_pages is unavailable",
            )
        )
    tantivy = modules.get("tantivy")
    tantivy_index = tantivy is not None and getattr(tantivy, "Index", None) is not None
    imports["tantivy.Index"] = {"ok": tantivy_index}
    if not tantivy_index:
        errors.append(
            _error("missing_export", "tantivy", "tantivy.Index is unavailable")
        )

    offline: dict[str, Any] = {
        "constructed": False,
        "use_doc_details": None,
        "defer_embedding": None,
        "parse_media": None,
        "enrich_media": None,
        "evidence_skip_summary": None,
        "index_directory": None,
        "sync_with_paper_directory": None,
        "rebuild_index": None,
        "ok": False,
    }
    settings_type = getattr(paperqa, "Settings", None) if paperqa is not None else None
    if settings_type is not None:
        try:
            smoke_index = (temporary_parent or Path.cwd()) / (
                ".phase-validation-tmp/phase2-paperqa-settings-smoke"
            )
            settings = settings_type(
                parsing={
                    "use_doc_details": False,
                    "multimodal": False,
                    "defer_embedding": True,
                },
                answer={"evidence_skip_summary": True},
                # An explicit, uncreated path prevents the default ~/.pqa mkdir.
                agent={
                    "index": {
                        "index_directory": str(smoke_index),
                        "sync_with_paper_directory": False,
                    },
                    "rebuild_index": False,
                },
            )
            parse_media, enrich_media = settings.parsing.should_parse_and_enrich_media
            configured_index = settings.agent.index
            offline.update(
                {
                    "constructed": True,
                    "use_doc_details": settings.parsing.use_doc_details,
                    "defer_embedding": settings.parsing.defer_embedding,
                    "parse_media": parse_media,
                    "enrich_media": enrich_media,
                    "evidence_skip_summary": settings.answer.evidence_skip_summary,
                    "index_directory": str(configured_index.index_directory),
                    "sync_with_paper_directory": (
                        configured_index.sync_with_paper_directory
                    ),
                    "rebuild_index": settings.agent.rebuild_index,
                }
            )
            offline["ok"] = (
                offline["use_doc_details"] is False
                and offline["defer_embedding"] is True
                and offline["parse_media"] is False
                and offline["enrich_media"] is False
                and offline["evidence_skip_summary"] is True
                and offline["index_directory"] == str(smoke_index)
                and offline["sync_with_paper_directory"] is False
                and offline["rebuild_index"] is False
            )
            if not offline["ok"]:
                errors.append(
                    _error(
                        "offline_settings_unsafe",
                        "paperqa.Settings",
                        "explicit offline settings did not preserve all required flags",
                    )
                )
        except Exception as exc:
            offline["error"] = f"{type(exc).__name__}: {exc}"
            errors.append(
                _error(
                    "offline_settings_failed",
                    "paperqa.Settings",
                    f"{type(exc).__name__}: {exc}",
                )
            )
    return imports, offline, errors


def collect_report(
    project_root: Path = PROJECT_ROOT,
    *,
    version_getter: Callable[[str], str] = importlib.metadata.version,
    module_importer: Callable[[str], ModuleType | Any] = importlib.import_module,
    system_name: str | None = None,
    python_version: tuple[int, int, int] | None = None,
) -> dict[str, Any]:
    """Collect a complete compatibility report without mutating the environment."""
    errors: list[dict[str, str]] = []
    current_system = system_name or platform.system()
    current_python = python_version or (
        sys.version_info.major,
        sys.version_info.minor,
        sys.version_info.micro,
    )
    platform_ok = current_system == "Windows"
    python_ok = current_python >= (3, 11, 0)
    if not platform_ok:
        errors.append(
            _error("unsupported_platform", "environment", "Windows is required")
        )
    if not python_ok:
        errors.append(
            _error("unsupported_python", "environment", "Python >=3.11 is required")
        )

    pyproject, pin_errors = check_pyproject_pins(project_root)
    reference, reference_errors = check_reference_commit(project_root)
    installed, missing, version_errors = check_installed_versions(version_getter)
    imports, offline, import_errors = check_imports_and_offline_settings(
        module_importer,
        temporary_parent=project_root,
    )
    adapter, adapter_errors = check_adapter_forbidden_apis(
        project_root / "src" / "open_deep_research" / "knowledge" / "paperqa_adapter.py"
    )
    errors.extend(pin_errors)
    errors.extend(reference_errors)
    errors.extend(version_errors)
    errors.extend(import_errors)
    errors.extend(adapter_errors)

    if missing:
        status = "missing_dependencies"
        exit_code = 2
    elif errors:
        status = "incompatible"
        exit_code = 1
    else:
        status = "compatible"
        exit_code = 0
    return {
        "schema_version": "1.0",
        "status": status,
        "exit_code": exit_code,
        "environment": {
            "system": current_system,
            "machine": platform.machine(),
            "python": ".".join(str(part) for part in current_python),
            "windows_ok": platform_ok,
            "python_ok": python_ok,
        },
        "expected_versions": EXPECTED_VERSIONS,
        "pyproject": pyproject,
        "reference": reference,
        "installed": installed,
        "missing_distributions": missing,
        "imports": imports,
        "offline_settings": offline,
        "adapter_static_check": adapter,
        "network_used": False,
        "installation_attempted": False,
        "errors": errors,
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"Phase 2 dependency smoke: {report['status']}")
    print(
        "Environment: "
        f"{report['environment']['system']} / Python {report['environment']['python']}"
    )
    for name, result in report["installed"].items():
        print(f"- {name}: expected={result['expected']} actual={result['actual']}")
    if report["errors"]:
        print("Errors:")
        for error in report["errors"]:
            print(f"- [{error['code']}] {error['component']}: {error['message']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Emit one machine-readable JSON report."
    )
    args = parser.parse_args(argv)
    report = collect_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
