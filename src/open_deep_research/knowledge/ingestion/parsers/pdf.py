"""Text-only PDF parsing from an immutable byte snapshot using PyMuPDF."""

from __future__ import annotations

from typing import ClassVar

from open_deep_research.knowledge.ingestion.parsers.base import (
    DocumentParseError,
    ParseErrorCode,
)
from open_deep_research.knowledge.ingestion.parsers.chunking import page_windows
from open_deep_research.knowledge.ingestion.parsers.models import (
    ChunkingConfig,
    DocumentInput,
    ParsedDocument,
    ParserChunk,
    ParserLocator,
    ParserLocatorType,
)


class PdfParser:
    """Parse selectable PDF text only; media, OCR, and enrichment are unsupported."""

    name: ClassVar[str] = "pymupdf_text"
    version: ClassVar[str] = "1"
    _media_types: ClassVar[frozenset[str]] = frozenset(
        {"application/pdf", "application/x-pdf"}
    )

    def supports(self, media_type: str, suffix: str) -> bool:
        return media_type.partition(";")[0].strip().lower() in self._media_types or suffix.lower() == ".pdf"

    def parse(
        self,
        document: DocumentInput,
        chunking: ChunkingConfig | None = None,
    ) -> ParsedDocument:
        if not document.raw_bytes:
            raise DocumentParseError(
                ParseErrorCode.EMPTY_INPUT, self.name, "PDF snapshot is empty"
            )
        try:
            import pymupdf
        except ImportError as exc:  # pragma: no cover - exercised by dependency smoke
            raise DocumentParseError(
                ParseErrorCode.UNSUPPORTED_FORMAT,
                self.name,
                "PyMuPDF is not installed",
            ) from exc

        try:
            pdf = pymupdf.open(stream=document.raw_bytes, filetype="pdf")
        except Exception as exc:
            raise DocumentParseError(
                ParseErrorCode.INVALID_DOCUMENT,
                self.name,
                "PDF snapshot cannot be opened",
            ) from exc

        try:
            pages = tuple(
                (index + 1, pdf.load_page(index).get_text("text", sort=True))
                for index in range(pdf.page_count)
            )
        except Exception as exc:
            raise DocumentParseError(
                ParseErrorCode.INVALID_DOCUMENT,
                self.name,
                "PDF page text extraction failed",
            ) from exc
        finally:
            pdf.close()

        windows = page_windows(pages, chunking or ChunkingConfig())
        if not windows:
            raise DocumentParseError(
                ParseErrorCode.NO_TEXT,
                self.name,
                "PDF contains no selectable text; OCR is not enabled",
            )
        chunks = tuple(
            ParserChunk(
                ordinal=ordinal,
                text=text,
                locator=ParserLocator(
                    type=ParserLocatorType.PAGE,
                    page_start=page_start,
                    page_end=page_end,
                ),
                metadata={"page_count": len(pages)},
            )
            for ordinal, (text, page_start, page_end) in enumerate(windows)
        )
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            media_type=document.normalized_media_type,
            title=document.display_name,
            canonical_uri=document.canonical_uri,
            chunks=chunks,
            metadata={
                "library": "PyMuPDF",
                "library_version": pymupdf.__version__,
                "page_count": len(pages),
                "parse_media": False,
                "ocr": False,
                "enrichment": False,
                "source_input_ref": document.input_ref,
            },
        )
