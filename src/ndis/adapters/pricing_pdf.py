"""pymupdf-backed reader for the NDIS Pricing Arrangements PDF.

Hands the domain what it needs for chunking — per-page cleaned text and the document's
table-of-contents headings — and nothing else. Raises :class:`PricingPdfError` for a file
that is not a readable PDF so callers (the fetch verifier, the index builder) can reject
a truncated or wrong download before any model call is paid for.
"""

from pathlib import Path

import pymupdf

from ndis.domain.pricing import PdfHeading, PdfPage


class PricingPdfError(RuntimeError):
    """The file is not a readable PDF document."""


class PricingPdf:
    """One opened pricing PDF; page numbers are the 1-based printed pages."""

    def __init__(self, path: Path) -> None:
        try:
            self._doc = pymupdf.open(path)
        except (pymupdf.FileDataError, RuntimeError) as exc:
            raise PricingPdfError(f"{path}: {exc}") from exc
        # pymupdf happily opens plain-text and HTML files as text documents; only a real
        # PDF is acceptable here, so a Cloudflare block page is rejected, not parsed.
        if not self._doc.is_pdf:
            self._doc.close()
            raise PricingPdfError(f"{path}: not a PDF document")

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> "PricingPdf":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def pages(self) -> tuple[PdfPage, ...]:
        """Every page with text, in printed order; blank pages are kept (empty text)."""
        pages: list[PdfPage] = []
        for number in range(self._doc.page_count):
            text = self._doc[number].get_text("text")
            pages.append(PdfPage(number + 1, text))
        return tuple(pages)

    def headings(self) -> tuple[PdfHeading, ...]:
        """The document's table-of-contents entries in document order."""
        return tuple(
            PdfHeading(page=page, level=level, title=title)
            for level, title, page in self._doc.get_toc()
        )


def count_pdf_pages(path: Path) -> int:
    """The page count of a readable PDF; ``PricingPdfError`` for anything else."""
    with PricingPdf(path) as document:
        return document.page_count
