"""The extraction store: a private JSON cache of record plus a public-safe projection.

The full profile (with evidence quotes, which are page text) lives as one JSON file per
provider under the private extraction directory; a rerun never repeats a provider whose
file exists. The SQLite side holds the same claims without their quotes — service, page
URL and confidence per row, plus the provider-level attributes — so later tickets can
query them without touching the private files.
"""

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, delete, insert
from sqlalchemy.orm import Session

from ndis.adapters.sqlite_repository import Base, ProviderAttributeRow, ServiceClaimRow
from ndis.domain.extraction import (
    AgeGroup,
    PageAttributes,
    PageClaim,
    ProviderExtraction,
    ServiceClaim,
)
from ndis.domain.taxonomy import Service


class ExtractionCacheError(RuntimeError):
    """A cache file exists but cannot be parsed back into a profile."""


class CachedExtractionStore:
    """``ExtractionStore`` over a private JSON directory and the claims tables."""

    def __init__(self, engine: Engine, extraction_dir: Path) -> None:
        self._engine = engine
        self._extraction_dir = extraction_dir
        self._extraction_dir.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(engine)

    def cached_provider_keys(self) -> frozenset[str]:
        return frozenset(path.stem for path in self._extraction_dir.glob("*.json"))

    def save(self, profile: ProviderExtraction) -> None:
        path = self._path(profile.provider_key)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "provider_key": profile.provider_key,
                    "extracted_at": profile.extracted_at.isoformat(),
                    "claims": [
                        {
                            "url": claim.url,
                            "service": claim.claim.service.value,
                            "evidence_quote": claim.claim.evidence_quote,
                            "confidence": claim.claim.confidence,
                        }
                        for claim in profile.claims
                    ],
                    "attributes": {
                        "age_groups": [group.value for group in profile.attributes.age_groups],
                        "telehealth": profile.attributes.telehealth,
                        "home_visits": profile.attributes.home_visits,
                        "languages": list(profile.attributes.languages),
                    },
                    "input_tokens": profile.input_tokens,
                    "output_tokens": profile.output_tokens,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
        self._project(profile)

    def load(self, provider_key: str) -> ProviderExtraction | None:
        path = self._path(provider_key)
        if not path.is_file():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ExtractionCacheError(f"{path} is not a readable cache file: {exc}") from exc
        return _from_json(provider_key, document)

    def _path(self, provider_key: str) -> Path:
        return self._extraction_dir / f"{provider_key}.json"

    def _project(self, profile: ProviderExtraction) -> None:
        """Refresh the public-safe rows of one provider (no quotes, which are page text)."""
        with Session(self._engine) as session, session.begin():
            session.execute(
                delete(ServiceClaimRow).where(ServiceClaimRow.provider_key == profile.provider_key)
            )
            session.execute(
                delete(ProviderAttributeRow).where(
                    ProviderAttributeRow.provider_key == profile.provider_key
                )
            )
            rows = _claim_rows(profile)
            if rows:
                session.execute(
                    insert(ServiceClaimRow),
                    [
                        {
                            "provider_key": profile.provider_key,
                            "url": url,
                            "service": service.value,
                            "confidence": confidence,
                        }
                        for url, service, confidence in rows
                    ],
                )
            session.execute(
                insert(ProviderAttributeRow),
                {
                    "provider_key": profile.provider_key,
                    "telehealth": profile.attributes.telehealth,
                    "home_visits": profile.attributes.home_visits,
                    "age_groups": json.dumps([g.value for g in profile.attributes.age_groups]),
                    "languages": json.dumps(list(profile.attributes.languages)),
                    "extracted_at": profile.extracted_at.isoformat(),
                },
            )


def _claim_rows(profile: ProviderExtraction) -> list[tuple[str, Service, float]]:
    """One row per (page, service): the same service claimed twice on one page collapses
    to its most confident claim, matching the table's (provider, url, service) key. The
    private cache keeps every accepted claim with its evidence; only the projection folds
    duplicates away."""
    winner: dict[tuple[str, Service], int] = {}
    for index, claim in enumerate(profile.claims):
        key = (claim.url, claim.claim.service)
        current = winner.get(key)
        if current is None or claim.claim.confidence > profile.claims[current].claim.confidence:
            winner[key] = index
    return [
        (claim.url, claim.claim.service, claim.claim.confidence)
        for index, claim in enumerate(profile.claims)
        if winner[(claim.url, claim.claim.service)] == index
    ]


def _from_json(provider_key: str, document: object) -> ProviderExtraction:
    if not isinstance(document, dict):
        raise ExtractionCacheError(f"cache of {provider_key} is not an object")
    claims = tuple(
        PageClaim(
            url=str(item["url"]),
            claim=ServiceClaim(
                service=Service(str(item["service"])),
                evidence_quote=str(item["evidence_quote"]),
                confidence=float(item["confidence"]),
            ),
        )
        for item in document["claims"]
    )
    attributes = document["attributes"]
    return ProviderExtraction(
        provider_key=provider_key,
        claims=claims,
        attributes=PageAttributes(
            age_groups=tuple(AgeGroup(group) for group in attributes["age_groups"]),
            telehealth=attributes["telehealth"],
            home_visits=attributes["home_visits"],
            languages=tuple(attributes["languages"]),
        ),
        extracted_at=datetime.fromisoformat(str(document["extracted_at"])),
        input_tokens=int(document["input_tokens"]),
        output_tokens=int(document["output_tokens"]),
    )
