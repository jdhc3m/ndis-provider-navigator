"""Streaming reader and loader for the Australian postcodes CSV (one row per locality)."""

import csv
import logging
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ndis.application.providers import PostcodeCentroids
from ndis.domain.geo import (
    POSTCODE_COLUMNS,
    Coordinates,
    PostcodeCentroid,
    PostcodeRowError,
    parse_postcode_row,
    postcode_centroid,
)

logger = logging.getLogger(__name__)

MIN_PARSE_RATIO = 0.90


class PostcodesFormatError(ValueError):
    """The file does not have the postcode CSV's header row, or too few rows parse."""


def iter_postcode_rows(path: Path) -> Iterator[Mapping[str, str | None]]:
    """Yield postcode rows one at a time as column-name mappings (UTF-8 with BOM)."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        missing = [column for column in POSTCODE_COLUMNS if column not in header]
        if missing:
            raise PostcodesFormatError(f"postcode header is missing columns: {missing}")
        yield from reader


@dataclass
class PostcodeLoadReport:
    """Counts produced by one load of the postcode CSV."""

    input_rows: int = 0
    parsed_rows: int = 0
    rejected_rows: int = 0
    rejection_reasons: Counter[str] = field(default_factory=Counter)
    postcodes_persisted: int = 0

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
            f"parse_ratio={self.parse_ratio:.4f} postcodes_persisted={self.postcodes_persisted}"
        )


def parse_postcodes(path: Path) -> tuple[list[PostcodeCentroid], PostcodeLoadReport]:
    """Read the file row by row and reduce it to one centroid per postcode (the mean of
    every located locality row; see ``postcode_centroid``)."""
    points: dict[str, list[Coordinates]] = {}
    report = PostcodeLoadReport()
    for row in iter_postcode_rows(path):
        report.input_rows += 1
        try:
            postcode, point = parse_postcode_row(row)
        except PostcodeRowError as exc:
            report.rejected_rows += 1
            report.rejection_reasons[exc.reason] += 1
            continue
        report.parsed_rows += 1
        points.setdefault(postcode, []).append(point)
    centroids = [postcode_centroid(postcode, located) for postcode, located in points.items()]
    return centroids, report


def load_postcodes(path: Path, repository: PostcodeCentroids) -> PostcodeLoadReport:
    """Parse the CSV at ``path`` and replace the stored centroids with it."""
    centroids, report = parse_postcodes(path)
    if report.parse_ratio < MIN_PARSE_RATIO:
        raise PostcodesFormatError(
            f"only {report.parse_ratio:.1%} of {report.input_rows} rows parsed; "
            f"rejections: {dict(report.rejection_reasons)}"
        )
    repository.replace_all(centroids)
    report.postcodes_persisted = len(centroids)
    logger.info("postcode load: %s", report.summary())
    return report
