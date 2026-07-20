"""Import authorized local snapshots as candidate knowledge without network access."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from open_deep_research.knowledge.ingestion.parsers import (  # noqa: E402
    ChunkingConfig,
    DocumentInput,
    HtmlSnapshotParser,
    MarkdownParser,
    PastQueryParser,
    PdfParser,
)
from open_deep_research.knowledge.ingestion.service import (  # noqa: E402
    IngestionService,
    IngestionServiceError,
)
from open_deep_research.knowledge.models import (  # noqa: E402
    KnowledgeAccessContext,
    KnowledgeScope,
    SourceKind,
    Visibility,
)
from open_deep_research.knowledge.sqlite_repository import (  # noqa: E402
    SQLiteRepository,
)
from open_deep_research.storage.blob_repository import (  # noqa: E402
    LocalBlobRepository,
)


SUPPORTED_SUFFIXES = frozenset({".pdf", ".md", ".markdown", ".html", ".htm", ".json", ".jsonl", ".ndjson"})


def build_parser() -> argparse.ArgumentParser:
    """Build the local-only administrative import CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--db", type=Path, default=Path("data/knowledge/knowledge.db"))
    parser.add_argument("--blob-dir", type=Path)
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument("--tenant", default="local")
    parser.add_argument("--scope", dest="project_id", required=True)
    parser.add_argument("--visibility", choices=("project", "private"), default="project")
    parser.add_argument("--owner")
    parser.add_argument("--chunk-size", type=int, default=4000)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _scope(args: argparse.Namespace) -> tuple[KnowledgeScope, KnowledgeAccessContext]:
    visibility = Visibility(args.visibility)
    scope = KnowledgeScope(
        tenant_id=args.tenant,
        project_id=args.project_id,
        owner_user_id=args.owner,
        visibility=visibility,
    )
    access = KnowledgeAccessContext(
        trusted_tenant_id=scope.tenant_id,
        trusted_project_id=scope.project_id,
        trusted_user_id=scope.owner_user_id,
        allowed_visibilities=(scope.visibility,),
        auth_source="knowledge-import-cli",
        request_id=f"import-{uuid4().hex}",
    )
    return scope, access


def _authorized_files(source: Path) -> tuple[Path, tuple[Path, ...]]:
    """Resolve the explicit root and reject symlinks/path escapes."""
    resolved = source.resolve(strict=True)
    root = resolved if resolved.is_dir() else resolved.parent
    candidates = (
        sorted(path for path in resolved.rglob("*") if path.is_file())
        if resolved.is_dir()
        else [resolved]
    )
    authorized: list[Path] = []
    for candidate in candidates:
        if candidate.is_symlink():
            raise ValueError(f"symbolic links are not accepted: {candidate.name}")
        actual = candidate.resolve(strict=True)
        if actual != root and root not in actual.parents:
            raise ValueError("source path escaped the explicitly allowed root")
        if actual.suffix.lower() in SUPPORTED_SUFFIXES:
            authorized.append(actual)
    if not authorized:
        raise ValueError("source contains no supported local knowledge files")
    return root, tuple(authorized)


def _input_for(path: Path, root: Path) -> DocumentInput:
    suffix = path.suffix.lower()
    relative = path.relative_to(root).as_posix()
    if suffix == ".pdf":
        media_type, source_kind = "application/pdf", SourceKind.LOCAL_FILE
    elif suffix in {".md", ".markdown"}:
        media_type, source_kind = "text/markdown", SourceKind.LOCAL_FILE
    elif suffix in {".html", ".htm"}:
        media_type, source_kind = "text/html", SourceKind.WEB
    else:
        media_type, source_kind = "application/json", SourceKind.PAST_QUERY
    return DocumentInput(
        source_kind=source_kind,
        media_type=media_type,
        input_ref=relative,
        display_name=path.stem,
        raw_bytes=path.read_bytes(),
        metadata={"administrative_import": True},
    )


def _parser_for(document: DocumentInput):
    parsers = (PdfParser(), MarkdownParser(), HtmlSnapshotParser(), PastQueryParser())
    matches = [
        parser
        for parser in parsers
        if parser.supports(document.normalized_media_type, document.suffix)
    ]
    if len(matches) != 1:
        raise ValueError("exactly one deterministic parser must accept each input")
    return matches[0]


async def _run(args: argparse.Namespace) -> tuple[int, dict]:
    scope, access = _scope(args)
    root, files = _authorized_files(args.source)
    chunking = ChunkingConfig(max_chars=args.chunk_size, overlap=args.chunk_overlap)
    inputs = tuple(_input_for(path, root) for path in files)
    if args.dry_run:
        parsed = [
            {
                "input_ref": document.input_ref,
                "parser": result.parser_name,
                "parser_version": result.parser_version,
                "chunks": len(result.chunks),
                "media_type": result.media_type,
            }
            for document in inputs
            for result in [_parser_for(document).parse(document, chunking)]
        ]
        return 0, {
            "status": "dry_run",
            "scope_id": scope.scope_id,
            "writes": 0,
            "documents": parsed,
        }

    blob_dir = args.blob_dir or args.db.parent / "blobs"
    repository = SQLiteRepository(str(args.db))
    service = IngestionService(repository, LocalBlobRepository(blob_dir))
    results = []
    for document in inputs:
        result = await service.ingest(
            document,
            access=access,
            scope=scope,
            chunking=chunking,
        )
        results.append(
            {
                "job_id": result.job.job_id,
                "source_id": result.source.source_id,
                "document_id": result.document.document_id,
                "version_id": result.version.version_id,
                "blob_id": result.job.blob_id,
                "lifecycle_status": result.version.lifecycle_status.value,
                "index_status": result.job.index_status.value,
                "chunk_ids": [chunk.chunk_id for chunk in result.chunks],
                "evidence_ids": [item.evidence_id for item in result.evidence],
            }
        )
    _ = args.index_dir  # Phase 2 derives PaperQA state by repository rehydration.
    return 0, {
        "status": "succeeded",
        "scope_id": scope.scope_id,
        "index_strategy": "repository_rehydrate_on_demand",
        "documents": results,
    }


def main(argv: list[str] | None = None) -> int:
    """Run one deterministic import batch and emit a machine-readable summary."""
    args = build_parser().parse_args(argv)
    try:
        code, payload = asyncio.run(_run(args))
    except IngestionServiceError as exc:
        payload = {
            "status": "failed",
            "job_id": exc.job.job_id,
            "error": exc.job.error.model_dump(mode="json") if exc.job.error else None,
        }
        code = 1
    except (OSError, ValueError) as exc:
        payload = {
            "status": "rejected",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        code = 2
    rendered = json.dumps(payload, ensure_ascii=False, indent=2 if args.as_json else None)
    (sys.stdout if code == 0 else sys.stderr).write(rendered + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
