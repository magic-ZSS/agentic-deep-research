"""Render a Markdown evaluation summary from machine JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from open_deep_research.evaluation.reporting import (  # noqa: E402
    render_markdown,
    update_readme_from_report,
)


def main(argv: list[str] | None = None) -> int:
    """Render Markdown and optionally synchronize the README block."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readme", type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(render_markdown(report).encode("utf-8"))
    if args.readme is not None:
        update_readme_from_report(args.readme, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
