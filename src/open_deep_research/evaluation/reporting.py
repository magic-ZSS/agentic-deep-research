"""Deterministic aggregation, artifact hashing, and Markdown rendering."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from open_deep_research.evaluation.experiment_models import (
    ArtifactEntry,
    ArtifactManifest,
    EvaluationStatus,
    ExperimentRun,
)


def aggregate_runs(runs: list[ExperimentRun]) -> dict[str, Any]:
    """Aggregate without dropping failed, skipped, or errored records."""
    groups: dict[tuple[str, str], list[ExperimentRun]] = {}
    for run in runs:
        groups.setdefault((run.variant_id, run.difficulty), []).append(run)
    rows: list[dict[str, Any]] = []
    for (variant, difficulty), records in sorted(groups.items()):
        passed = sum(item.status is EvaluationStatus.PASSED for item in records)
        scores = [
            metric.score
            for item in records
            for metric in item.metric_results
            if metric.metric_name == "offline_contract" and metric.score is not None
        ]
        mean = statistics.fmean(scores) if scores else None
        std = statistics.stdev(scores) if len(scores) > 1 else (0.0 if scores else None)
        ci95 = 1.96 * std / math.sqrt(len(scores)) if scores and std is not None else None
        rows.append(
            {
                "variant_id": variant,
                "difficulty": difficulty,
                "runs": len(records),
                "passed": passed,
                "failed": sum(item.status is EvaluationStatus.FAILED for item in records),
                "skipped": sum(item.status is EvaluationStatus.SKIPPED for item in records),
                "errors": sum(item.status is EvaluationStatus.ERROR for item in records),
                "mean": mean,
                "std": std,
                "ci95": ci95,
            }
        )
    return {
        "schema_version": "1.0",
        "mode": runs[0].mode if runs else "smoke",
        "experiment_id": runs[0].experiment_id if runs else None,
        "run_count": len(runs),
        "aggregates": rows,
        "limitations": [
            "Smoke records validate schemas and governance invariants only.",
            "Smoke records do not establish live quality, citation uplift, or cost savings.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a machine-result-driven table with explicit limitations."""
    lines = [
        "# Evaluation report",
        "",
        f"Mode: `{report['mode']}`  ",
        f"Runs: `{report['run_count']}`",
        "",
        "| Variant | Difficulty | Runs | Passed | Failed | Skipped | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["aggregates"]:
        lines.append(
            "| {variant_id} | {difficulty} | {runs} | {passed} | {failed} | {skipped} | {errors} |".format(**row)
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def render_readme_section(report: dict[str, Any]) -> str:
    """Render the compact README section from the same machine report."""
    variants = sorted({row["variant_id"] for row in report["aggregates"]})
    totals = {
        variant: {
            "runs": sum(row["runs"] for row in report["aggregates"] if row["variant_id"] == variant),
            "passed": sum(row["passed"] for row in report["aggregates"] if row["variant_id"] == variant),
        }
        for variant in variants
    }
    lines = [
        "<!-- phase7-eval:start -->",
        "### Evidence-governed evaluation (generated)",
        "",
        f"Latest committed artifact mode: `{report['mode']}`. Smoke validates contracts only; it is not evidence of live quality uplift or cost savings.",
        "",
        "| Variant | Runs | Contract passes |",
        "|---|---:|---:|",
    ]
    lines.extend(
        f"| {variant} | {totals[variant]['runs']} | {totals[variant]['passed']} |"
        for variant in variants
    )
    lines.extend(
        [
            "",
            "See `docs/evaluation.md` and `artifacts/evaluation/smoke/manifest.json` for reproducibility and limitations.",
            "<!-- phase7-eval:end -->",
        ]
    )
    return "\n".join(lines)


def update_readme_from_report(path: str | Path, report: dict[str, Any]) -> None:
    """Replace exactly one generated README block."""
    target = Path(path)
    content = target.read_text(encoding="utf-8")
    start, end = "<!-- phase7-eval:start -->", "<!-- phase7-eval:end -->"
    generated = render_readme_section(report)
    if start in content and end in content:
        prefix, remainder = content.split(start, 1)
        _, suffix = remainder.split(end, 1)
        content = prefix + generated + suffix
    else:
        content = content.rstrip() + "\n\n" + generated + "\n"
    target.write_text(content, encoding="utf-8")


def sha256_path(path: Path) -> str:
    """Hash raw file bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_artifact_manifest(
    directory: str | Path,
    *,
    experiment_id: str,
    dataset_version: str,
    project_commit: str,
) -> ArtifactManifest:
    """Hash every regular artifact except the manifest itself."""
    root = Path(directory)
    manifest_path = root / "manifest.json"
    entries = [
        ArtifactEntry(
            path=path.relative_to(root).as_posix(),
            sha256=sha256_path(path),
            size_bytes=path.stat().st_size,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != manifest_path
    ]
    manifest = ArtifactManifest(
        experiment_id=experiment_id,
        dataset_version=dataset_version,
        project_commit=project_commit,
        generated_at=datetime.now(UTC),
        files=entries,
    )
    manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def validate_artifact_manifest(directory: str | Path) -> list[str]:
    """Return deterministic integrity errors for a saved manifest."""
    root = Path(directory)
    payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest = ArtifactManifest.model_validate(payload)
    errors: list[str] = []
    for entry in manifest.files:
        path = root / entry.path
        if not path.is_file():
            errors.append(f"missing:{entry.path}")
        elif path.stat().st_size != entry.size_bytes:
            errors.append(f"size:{entry.path}")
        elif sha256_path(path) != entry.sha256:
            errors.append(f"sha256:{entry.path}")
    return errors
