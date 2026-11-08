"""Streaming reader and loader for the NDIS Commission provider register CSV."""

import csv
import logging
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from ndis.application.providers import ProviderRepository
from ndis.domain.entities import Outlet, Provider
from ndis.domain.register import COLUMN_ABN, REGISTER_COLUMNS, RegisterRowError, parse_register_row

logger = logging.getLogger(__name__)

MIN_PARSE_RATIO = 0.95


class RegisterFormatError(ValueError):
    """The file does not have the register's header row."""


def iter_register_rows(path: Path) -> Iterator[Mapping[str, str | None]]:
    """Yield register rows one at a time as column-name mappings (UTF-8 with BOM)."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        missing = [column for column in REGISTER_COLUMNS if column not in header]
        if missing:
            raise RegisterFormatError(f"register header is missing columns: {missing}")
        yield from reader


def count_distinct_abns(path: Path) -> int:
    """Number of distinct non-empty ABN values in the file, read row by row."""
    abns: set[str] = set()
    for row in iter_register_rows(path):
        abn = (row.get(COLUMN_ABN) or "").strip()
        if abn:
            abns.add(abn)
    return len(abns)


@dataclass
class LoadReport:
    """Counts produced by one load of the register."""

    input_rows: int = 0
    parsed_rows: int = 0
    rejected_rows: int = 0
    rejection_reasons: Counter[str] = field(default_factory=Counter)
    brisbane_rows: int = 0
    brisbane_providers: int = 0
    providers_persisted: int = 0
    outlets_persisted: int = 0

    @property
    def parse_ratio(self) -> float:
        return self.parsed_rows / self.input_rows if self.input_rows else 0.0

    def summary(self) -> str:
        reasons = ", ".join(
            f"{reason}={count}" for reason, count in sorted(self.rejection_reasons.items())
        )
        return (
            f"input_rows={self.input_rows} parsed_rows={self.parsed_rows} "
            f"rejected_rows={self.rejected_rows} ({reasons or 'none'}) "
            f"parse_ratio={self.parse_ratio:.4f} brisbane_rows={self.brisbane_rows} "
            f"brisbane_providers={self.brisbane_providers} "
            f"providers_persisted={self.providers_persisted} "
            f"outlets_persisted={self.outlets_persisted}"
        )


def parse_register(path: Path) -> tuple[dict[str, Provider], list[Outlet], LoadReport]:
    """Stream the file, aggregate one Provider per ABN and one Outlet per row.

    Provider-level fields come from the first row seen for an ABN; the Brisbane flag is
    true when any row for that ABN has a head office or outlet in the Brisbane ranges.
    """
    providers: dict[str, Provider] = {}
    outlets: list[Outlet] = []
    report = LoadReport()
    for row in iter_register_rows(path):
        report.input_rows += 1
        try:
            provider, outlet = parse_register_row(row)
        except RegisterRowError as exc:
            report.rejected_rows += 1
            report.rejection_reasons[exc.reason] += 1
            continue
        report.parsed_rows += 1
        if provider.brisbane_area:
            report.brisbane_rows += 1
        existing = providers.get(provider.abn)
        if existing is None:
            providers[provider.abn] = provider
        elif provider.brisbane_area and not existing.brisbane_area:
            providers[provider.abn] = replace(existing, brisbane_area=True)
        outlets.append(outlet)
    report.brisbane_providers = sum(1 for provider in providers.values() if provider.brisbane_area)
    return providers, outlets, report


def load_register(path: Path, repository: ProviderRepository) -> LoadReport:
    """Parse the register at ``path`` and replace the repository contents with it."""
    providers, outlets, report = parse_register(path)
    if report.parse_ratio < MIN_PARSE_RATIO:
        raise RegisterFormatError(
            f"only {report.parse_ratio:.1%} of {report.input_rows} rows parsed; "
            f"rejections: {dict(report.rejection_reasons)}"
        )
    repository.replace_all(providers.values(), outlets)
    report.providers_persisted = len(providers)
    report.outlets_persisted = len(outlets)
    logger.info("register load: %s", report.summary())
    return report
