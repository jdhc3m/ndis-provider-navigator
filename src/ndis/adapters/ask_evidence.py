"""The evidence source for combined answers: provider service claims with their evidence
quotes, read back from the private extraction cache, plus the stored page text the quotes
must be verbatim spans of, read from the private pages directory over ``page_meta``.

Nothing here decides what the answer may say — that is the domain guard's job. This
adapter only fetches the data the guard needs, and a missing cache file or page file is
an empty result rather than an error, so one lost private file cannot fail a question.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from ndis.adapters.extraction_store import CachedExtractionStore
from ndis.adapters.sqlite_repository import PageMetaRow
from ndis.domain.answer import ClaimedService


class ExtractionEvidenceSource:
    """``ClaimEvidenceSource`` over the extraction cache and the stored pages."""

    def __init__(self, engine: Engine, extraction_dir: Path, pages_dir: Path) -> None:
        self._engine = engine
        self._cache = CachedExtractionStore(engine, extraction_dir)
        self._pages_dir = pages_dir

    def claims_for(self, provider_keys: Sequence[str]) -> Mapping[str, tuple[ClaimedService, ...]]:
        claims: dict[str, tuple[ClaimedService, ...]] = {}
        for key in provider_keys:
            profile = self._cache.load(key)
            if profile is None:
                continue
            claims[key] = tuple(
                ClaimedService(
                    service=page_claim.claim.service,
                    url=page_claim.url,
                    quote=page_claim.claim.evidence_quote,
                    confidence=page_claim.claim.confidence,
                )
                for page_claim in profile.claims
            )
        return claims

    def page_text(self, provider_key: str, url: str) -> str:
        with Session(self._engine) as session:
            row = session.get(PageMetaRow, (provider_key, url))
        text_path = row.text_path if row is not None else None
        if not text_path:
            return ""
        file = self._pages_dir / text_path
        if not file.is_file():
            return ""
        try:
            return file.read_text(encoding="utf-8")
        except OSError:
            return ""
