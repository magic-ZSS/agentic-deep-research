"""Read-only local memory inspection helper."""
# ruff: noqa: D103

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from open_deep_research.memory.models import MemoryType
from open_deep_research.memory.sqlite_repository import SQLiteMemoryRepository
from open_deep_research.runtime.identity import RuntimeIdentity


async def run(args):
    identity = RuntimeIdentity(tenant_id=args.tenant, user_id=args.user, project_id=args.project, thread_id="inspection", auth_source="local_cli")
    repo = SQLiteMemoryRepository(args.db)
    output = []
    for kind in MemoryType:
        output.extend(item.model_dump(mode="json") for item in await repo.search(identity.namespace(kind.value), query=args.query, limit=args.limit))
    sys.stdout.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=10)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
