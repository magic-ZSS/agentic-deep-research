"""Search imported knowledge through the trusted inspection-only contract."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from open_deep_research.knowledge.models import (  # noqa: E402
    KnowledgeAccessContext,
    KnowledgeScope,
    Visibility,
)
from open_deep_research.knowledge.paperqa_adapter import (  # noqa: E402
    DeterministicHashEmbedding,
    NativePaperQABackend,
    PaperQAAdapterError,
    PaperQAKnowledgeRetriever,
    create_offline_paperqa_settings,
)
from open_deep_research.knowledge.retrieval.models import (  # noqa: E402
    KnowledgeSearchRequest,
)
from open_deep_research.knowledge.retrieval.repository_retriever import (  # noqa: E402
    RepositoryKnowledgeRetriever,
    RepositoryRetrievalCatalog,
)
from open_deep_research.knowledge.sqlite_repository import (  # noqa: E402
    SQLiteRepository,
)
from open_deep_research.tools.knowledge import (  # noqa: E402
    KnowledgeInspectionService,
    knowledge_search,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the direct-call inspection CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=Path("data/knowledge/paperqa-index"),
    )
    parser.add_argument("--tenant", default="local")
    parser.add_argument("--scope", dest="project_id", required=True)
    parser.add_argument("--visibility", choices=("project", "private"), default="project")
    parser.add_argument("--owner")
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--as-of")
    parser.add_argument("--include-candidate", action="store_true")
    parser.add_argument(
        "--paperqa",
        action="store_true",
        help="Opt in to repository-rehydrated PaperQA raw retrieval with a local embedding.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    scope = KnowledgeScope(
        tenant_id=args.tenant,
        project_id=args.project_id,
        owner_user_id=args.owner,
        visibility=Visibility(args.visibility),
    )
    access = KnowledgeAccessContext(
        trusted_tenant_id=scope.tenant_id,
        trusted_project_id=scope.project_id,
        trusted_user_id=scope.owner_user_id,
        allowed_visibilities=(scope.visibility,),
        auth_source="knowledge-inspection-cli",
        request_id=f"search-{uuid4().hex}",
    )
    repository = SQLiteRepository(str(args.db))
    catalog = RepositoryRetrievalCatalog(repository)
    if args.paperqa:
        settings = create_offline_paperqa_settings(args.index_dir)
        retriever = PaperQAKnowledgeRetriever(
            catalog,
            backend=NativePaperQABackend(
                settings=settings,
                embedding_model=DeterministicHashEmbedding(),
            ),
            enabled=True,
            fallback_on_error=False,
        )
    else:
        retriever = RepositoryKnowledgeRetriever(catalog)
    service = KnowledgeInspectionService(
        retriever,
        allow_candidate_inspection=args.include_candidate,
    )
    request = KnowledgeSearchRequest(
        query=args.query,
        limit=args.limit,
        as_of=datetime.fromisoformat(args.as_of) if args.as_of else None,
        include_candidate=args.include_candidate,
    )
    result = await knowledge_search(service, request, access=access, scope=scope)
    return result.model_dump(mode="json")


def main(argv: list[str] | None = None) -> int:
    """Search without registering a production Researcher tool."""
    args = build_parser().parse_args(argv)
    try:
        payload = asyncio.run(_run(args))
    except (
        ImportError,
        LookupError,
        OSError,
        PaperQAAdapterError,
        PermissionError,
        ValueError,
    ) as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        return 1
    rendered = json.dumps(payload, ensure_ascii=False, indent=2 if args.as_json else None)
    sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
