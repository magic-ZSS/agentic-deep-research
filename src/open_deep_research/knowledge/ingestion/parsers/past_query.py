"""Strict parser for previously verified, evidence-bound query records."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from open_deep_research.knowledge.ingestion.parsers.base import (
    DocumentParseError,
    ParseErrorCode,
    decode_utf8,
)
from open_deep_research.knowledge.ingestion.parsers.models import (
    ChunkingConfig,
    DocumentInput,
    ParsedDocument,
    ParserChunk,
    ParserLocator,
    ParserLocatorType,
)


class PastQueryParser:
    """Accept only verified facts with explicit scope, Source, and Evidence IDs."""

    name: ClassVar[str] = "verified_past_query"
    version: ClassVar[str] = "1"
    _media_types: ClassVar[frozenset[str]] = frozenset(
        {"application/json", "application/jsonl", "application/x-ndjson"}
    )

    def supports(self, media_type: str, suffix: str) -> bool:
        return media_type.partition(";")[0].strip().lower() in self._media_types or suffix.lower() in {".json", ".jsonl", ".ndjson"}

    def parse(
        self,
        document: DocumentInput,
        chunking: ChunkingConfig | None = None,
    ) -> ParsedDocument:
        del chunking  # Facts are already atomic evidence-bound records.
        text = decode_utf8(document, self.name)
        records = self._load_records(text, document.suffix)
        chunks: list[ParserChunk] = []
        seen_record_ids: set[str] = set()
        for record in records:
            record_id = self._required_string(record, "record_id")
            if record_id in seen_record_ids:
                self._raise(ParseErrorCode.INVALID_DOCUMENT, f"duplicate record_id {record_id!r}")
            seen_record_ids.add(record_id)
            if record.get("verified") is not True:
                self._raise(
                    ParseErrorCode.UNVERIFIED_RECORD,
                    f"record {record_id!r} is not explicitly verified",
                )
            scope = record.get("scope")
            if not isinstance(scope, dict) or not self._nonblank(scope.get("tenant_id")) or not self._nonblank(scope.get("project_id")):
                self._raise(
                    ParseErrorCode.MISSING_SCOPE,
                    f"record {record_id!r} lacks tenant/project scope",
                )
            if scope.get("visibility") == "private" and not self._nonblank(scope.get("owner_user_id")):
                self._raise(
                    ParseErrorCode.MISSING_SCOPE,
                    f"private record {record_id!r} lacks owner_user_id",
                )
            facts = record.get("facts")
            if not isinstance(facts, list) or not facts:
                self._raise(
                    ParseErrorCode.MISSING_EVIDENCE,
                    f"record {record_id!r} has no evidence-bound facts",
                )
            for fact_index, fact in enumerate(facts):
                if not isinstance(fact, dict):
                    self._raise(ParseErrorCode.MISSING_EVIDENCE, "fact must be an object")
                fact_text = self._required_string(fact, "text")
                source_id = self._required_string(fact, "source_id", ParseErrorCode.MISSING_EVIDENCE)
                evidence_id = self._required_string(fact, "evidence_id", ParseErrorCode.MISSING_EVIDENCE)
                chunks.append(
                    ParserChunk(
                        ordinal=len(chunks),
                        text=fact_text,
                        locator=ParserLocator(
                            type=ParserLocatorType.QUERY_RECORD,
                            record_id=record_id,
                        ),
                        metadata={
                            "record_id": record_id,
                            "fact_index": fact_index,
                            "query": self._required_string(record, "query"),
                            "scope": scope,
                            "source_id": source_id,
                            "evidence_id": evidence_id,
                            "verified": True,
                        },
                    )
                )
        if not chunks:
            self._raise(ParseErrorCode.NO_TEXT, "past-query snapshot has no facts")
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            media_type=document.normalized_media_type,
            title=document.display_name,
            canonical_uri=document.canonical_uri,
            chunks=tuple(chunks),
            metadata={
                "record_count": len(records),
                "verified_only": True,
                "source_input_ref": document.input_ref,
            },
        )

    def _load_records(self, text: str, suffix: str) -> list[dict[str, Any]]:
        try:
            if suffix in {".jsonl", ".ndjson"}:
                values = [json.loads(line) for line in text.splitlines() if line.strip()]
            else:
                payload = json.loads(text)
                if isinstance(payload, dict) and "records" in payload:
                    values = payload["records"]
                elif isinstance(payload, list):
                    values = payload
                else:
                    values = [payload]
        except json.JSONDecodeError as exc:
            raise DocumentParseError(
                ParseErrorCode.INVALID_DOCUMENT,
                self.name,
                f"past-query snapshot is invalid JSON at line {exc.lineno}",
            ) from exc
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            self._raise(ParseErrorCode.INVALID_DOCUMENT, "records must be JSON objects")
        return values

    @staticmethod
    def _nonblank(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def _required_string(
        self,
        value: dict[str, Any],
        field: str,
        code: ParseErrorCode = ParseErrorCode.INVALID_DOCUMENT,
    ) -> str:
        item = value.get(field)
        if not self._nonblank(item):
            self._raise(code, f"field {field!r} must be a nonblank string")
        return item.strip()

    def _raise(self, code: ParseErrorCode, message: str) -> None:
        raise DocumentParseError(code, self.name, message)
