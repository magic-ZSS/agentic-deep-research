"""Validate the programmatic citation contract of a Phase 6 report artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

UNSAFE_PATH = re.compile(r"(?:[A-Za-z]:\\|\\\\|internal_storage_ref|storage_ref)")
FAILED_ENFORCE = {"unsupported", "contradicted", "not_checkable"}


def validate_payload(payload: dict[str, Any]) -> list[str]:
    """Return deterministic errors for orphan, unsafe, stale or failed citations."""
    errors: list[str] = []
    report = str(payload.get("final_report", ""))
    artifact = payload.get("artifact", payload)
    mode = artifact.get("mode", payload.get("mode"))
    registry = artifact.get("registry", payload.get("registry", []))
    results = artifact.get("results", payload.get("results", []))
    claims = artifact.get("claims", payload.get("claims", []))
    if mode not in {"audit", "enforce"}:
        errors.append("mode must be audit or enforce")
    numbers = [entry.get("display_number") for entry in registry]
    expected = list(range(1, len(registry) + 1))
    if numbers != expected:
        errors.append("registry numbers must be contiguous and ordered")
    body, separator, table = report.partition("### Sources")
    markers = {int(value) for value in re.findall(r"\[(\d+)\]", body)}
    registry_numbers = set(numbers)
    table_numbers = {
        int(value) for value in re.findall(r"^\[(\d+)\]", table, re.MULTILINE)
    }
    if markers != registry_numbers:
        errors.append("body markers and registry are not bidirectionally equal")
    if registry and not separator:
        errors.append("source table is missing")
    if table_numbers != registry_numbers:
        errors.append("source table and registry differ")
    claim_ids = {claim.get("claim_id") for claim in claims}
    for result in results:
        if result.get("claim_id") not in claim_ids:
            errors.append("validation result references a missing claim")
        status = result.get("status")
        if mode == "enforce" and status in FAILED_ENFORCE:
            errors.append(f"enforce artifact retains failed claim: {status}")
        for link in result.get("links", []):
            if link.get("accepted") and link.get("temporal_status") != "current":
                errors.append("accepted link is not temporally current")
    serialized = json.dumps(payload, ensure_ascii=False)
    if UNSAFE_PATH.search(serialized):
        errors.append("artifact exposes a local/internal storage path")
    return sorted(set(errors))


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone report validation CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Validate one JSON report and return a process-friendly status."""
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("top-level JSON value must be an object")
        errors = validate_payload(payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"INVALID: {exc}\n")
        return 2
    if errors:
        for error in errors:
            sys.stderr.write(f"INVALID: {error}\n")
        return 1
    sys.stdout.write("VALID: citation registry and enforce policy are consistent\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
