"""Read the stored page text back: ``page_meta`` rows from the public database joined to
the text files under the private pages directory, as ``StoredPage`` values for extraction.

Only ``ok`` rows carry text; the rest are skipped because there is nothing to extract
from. A missing text file is reported as an empty page rather than an error, so one lost
file cannot stop a run.
"""

from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ndis.adapters.sqlite_repository import PageMetaRow
from ndis.domain.extraction import StoredPage
from ndis.domain.pages import FetchStatus


class FilePageTextSource:
    """``PageEvidenceSource`` over a ``FilePageStore``'s database and pages directory."""

    def __init__(self, engine: Engine, pages_dir: Path) -> None:
        self._engine = engine
        self._pages_dir = pages_dir

    def ok_pages(self) -> tuple[StoredPage, ...]:
        pages: list[StoredPage] = []
        with Session(self._engine) as session:
            rows = session.execute(
                select(
                    PageMetaRow.provider_key,
                    PageMetaRow.url,
                    PageMetaRow.title,
                    PageMetaRow.text_path,
                )
                .where(PageMetaRow.status == FetchStatus.OK.value)
                .order_by(PageMetaRow.provider_key, PageMetaRow.url)
            )
            for provider_key, url, title, text_path in rows:
                pages.append(
                    StoredPage(
                        provider_key=provider_key,
                        url=url,
                        title=title,
                        text=self._read(text_path),
                    )
                )
        return tuple(pages)

    def _read(self, text_path: str | None) -> str:
        if not text_path:
            return ""
        file = self._pages_dir / text_path
        if not file.is_file():
            return ""
        try:
            return file.read_text(encoding="utf-8")
        except OSError:
            return ""
