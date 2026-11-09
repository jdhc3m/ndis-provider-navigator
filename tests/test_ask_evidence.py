"""The evidence adapter: claims with quotes from the extraction cache, page text from the
private pages directory, and graceful empties for missing files."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from ndis.adapters.ask_evidence import ExtractionEvidenceSource
from ndis.adapters.extraction_store import CachedExtractionStore
from ndis.adapters.sqlite_repository import FilePageStore, create_sqlite_engine
from ndis.domain.answer import ClaimedService
from ndis.domain.extraction import (
    PageAttributes,
    PageClaim,
    ProviderExtraction,
    ServiceClaim,
)
from ndis.domain.pages import FetchStatus, PageMeta, PageSource
from ndis.domain.taxonomy import Service

KEY = "16130055408"
URL = "https://aspire.example/"
PAGE_TEXT = (
    "Aspire offers speech pathology and occupational therapy for NDIS participants across Brisbane."
)
QUOTE = "speech pathology and occupational therapy for NDIS participants"


@pytest.fixture
def source(tmp_path: Path) -> ExtractionEvidenceSource:
    engine: Engine = create_sqlite_engine(tmp_path / "ndis.db")
    CachedExtractionStore(engine, tmp_path / "extraction")
    return ExtractionEvidenceSource(engine, tmp_path / "extraction", tmp_path / "pages")


def store_profile(source: ExtractionEvidenceSource, tmp_path: Path) -> None:
    store = CachedExtractionStore(source._engine, tmp_path / "extraction")
    store.save(
        ProviderExtraction(
            provider_key=KEY,
            claims=(
                PageClaim(
                    URL,
                    ServiceClaim(Service.SPEECH_PATHOLOGY, QUOTE, 0.9),
                ),
                PageClaim(
                    URL,
                    ServiceClaim(Service.OCCUPATIONAL_THERAPY, QUOTE, 0.5),
                ),
            ),
            attributes=PageAttributes(),
            extracted_at=datetime(2026, 11, 7, tzinfo=UTC),
            input_tokens=10,
            output_tokens=10,
        )
    )


def store_page(
    source: ExtractionEvidenceSource, tmp_path: Path, meta_kwargs: object = None
) -> None:
    pages = FilePageStore(source._engine, tmp_path / "pages")
    pages.save(
        PageMeta(
            url=URL,
            provider_key=KEY,
            fetched_at=datetime(2026, 11, 7, 3, 49, 42, tzinfo=UTC),
            status=FetchStatus.OK,
            source=PageSource.MAPS_CRAWL,
            text_hash="hash",
            text_path="abc123.txt",
            title="Aspire",
        ),
        PAGE_TEXT,
    )


def test_claims_are_read_back_with_quotes_and_fold_per_url_service(
    source: ExtractionEvidenceSource, tmp_path: Path
) -> None:
    store_profile(source, tmp_path)
    claims = source.claims_for([KEY, "99999999999"])
    assert "99999999999" not in claims
    folded = claims[KEY]
    assert len(folded) == 2  # one per (url, service), quotes kept
    assert all(claim.quote == QUOTE for claim in folded)
    assert sorted(claim.service.value for claim in folded) == [
        "occupational_therapy",
        "speech_pathology",
    ]


def test_page_text_comes_from_the_private_pages_directory(
    source: ExtractionEvidenceSource, tmp_path: Path
) -> None:
    store_page(source, tmp_path)
    assert source.page_text(KEY, URL) == PAGE_TEXT


def test_missing_pages_and_files_return_empty_text(
    source: ExtractionEvidenceSource, tmp_path: Path
) -> None:
    assert source.page_text(KEY, URL) == ""  # no page_meta row at all
    store_page(source, tmp_path)
    assert source.page_text("other-key", URL) == ""  # row is keyed by provider too


def test_claimed_services_map_to_claimed_service_values(
    source: ExtractionEvidenceSource, tmp_path: Path
) -> None:
    store_profile(source, tmp_path)
    claims = source.claims_for([KEY])
    assert claims[KEY][0].__class__ is ClaimedService
