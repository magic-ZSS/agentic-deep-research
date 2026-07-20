"""Run the project Knowledge MCP over stdio with trusted CLI identity."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from open_deep_research.knowledge.models import (  # noqa: E402
    KnowledgeAccessContext,
    KnowledgeScope,
)
from open_deep_research.knowledge.retrieval.repository_retriever import (  # noqa: E402
    RepositoryKnowledgeRetriever,
    RepositoryRetrievalCatalog,
)
from open_deep_research.knowledge.sqlite_repository import SQLiteRepository  # noqa: E402
from open_deep_research.mcp_servers.knowledge_server import create_knowledge_server  # noqa: E402
from open_deep_research.mcp_servers.schemas import KnowledgeMCPContext  # noqa: E402
from open_deep_research.mcp_servers.services import KnowledgeMCPService  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--user")
    parser.add_argument("--run-id")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    scope = KnowledgeScope(
        tenant_id=args.tenant,
        project_id=args.project,
        owner_user_id=args.user,
        visibility="private" if args.user else "project",
    )
    access = KnowledgeAccessContext(
        trusted_tenant_id=args.tenant,
        trusted_project_id=args.project,
        trusted_user_id=args.user,
        auth_source="knowledge-mcp-cli",
        request_id=args.run_id or "knowledge-mcp-stdio",
    )
    repository = SQLiteRepository(str(args.database.resolve()))
    retriever = RepositoryKnowledgeRetriever(RepositoryRetrievalCatalog(repository))
    service = KnowledgeMCPService(
        retriever=retriever,
        repository=repository,
        context=KnowledgeMCPContext(
            access=access,
            scope=scope,
            actor="knowledge-mcp-cli",
            run_id=args.run_id,
        ),
    )
    create_knowledge_server(service).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
