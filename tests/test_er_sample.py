"""The hand-check sample file: round trip, verdict preservation, public-safe content."""

from dataclasses import replace
from pathlib import Path

import pytest

from ndis.adapters.er_sample import (
    SampleFormatError,
    SampleRow,
    merge_verdicts,
    place_key,
    read_sample,
    sample_row,
    write_sample,
)
from ndis.domain.entity_resolution import (
    Candidate,
    EntityMatch,
    MatchStatus,
    MatchStrategy,
    ProviderLink,
)
from tests.conftest import make_place

LINK = ProviderLink(
    make_place("ChIJ-secret-place-id", 'Smith, Jones "Care" | Brisbane: NDIS', website="https://x.example"),
    EntityMatch(
        "ChIJ-secret-place-id", "11222333444", MatchStatus.REGISTERED, MatchStrategy.NAME, 0.9,
        "Smith, Jones Care Pty Ltd",
    ),
)  # fmt: skip
UNMATCHED = ProviderLink(
    make_place("other-place", "Nobody: Known"),
    EntityMatch("other-place", None, MatchStatus.UNREGISTERED, MatchStrategy.NONE, 0.4, None),
)
AMBIGUOUS = ProviderLink(
    make_place("franchise-place", "Franchise West", website="https://franchise.example"),
    EntityMatch(
        "franchise-place", None, MatchStatus.AMBIGUOUS, MatchStrategy.DOMAIN, 0.3, None,
        (
            Candidate("11000000001", "Franchise South Pty Ltd"),
            Candidate("11000000002", 'Franchise "North"'),
        ),
    ),
)  # fmt: skip


def test_round_trip_preserves_rows_and_hides_the_place_id(tmp_path: Path) -> None:
    path = tmp_path / "er_sample.yaml"
    rows = (sample_row(LINK), sample_row(UNMATCHED, verdict="unsure"), sample_row(AMBIGUOUS))
    write_sample(path, rows, seed=42)
    text = path.read_text(encoding="utf-8")
    assert "ChIJ-secret-place-id" not in text
    assert "seed: 42" in text
    assert text.startswith("# Entity-resolution hand-check sample")
    assert (
        '  candidates: ["11000000001 Franchise South Pty Ltd", "11000000002 Franchise \\"North\\""]'
        in text
    )
    assert read_sample(path) == rows
    assert rows[1].candidates == ()
    assert rows[0] == SampleRow(
        key=place_key("ChIJ-secret-place-id"),
        maps_title='Smith, Jones "Care" | Brisbane: NDIS',
        match_status="registered",
        strategy="name",
        confidence=0.9,
        abn="11222333444",
        register_name="Smith, Jones Care Pty Ltd",
        candidates=(),
        verdict=None,
    )
    assert len(place_key("x")) == 12 and place_key("x") != place_key("y")


def test_sample_written_before_candidates_existed_still_reads(tmp_path: Path) -> None:
    path = tmp_path / "er_sample.yaml"
    write_sample(path, [sample_row(LINK, verdict="correct")], seed=1)
    lines = [
        line for line in path.read_text(encoding="utf-8").splitlines() if "candidates" not in line
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert read_sample(path) == (sample_row(LINK, verdict="correct"),)


def test_hand_edited_bare_verdict_is_read(tmp_path: Path) -> None:
    path = tmp_path / "er_sample.yaml"
    write_sample(path, [sample_row(LINK)], seed=1)
    edited = path.read_text(encoding="utf-8").replace("verdict: null", "verdict: correct")
    path.write_text(edited, encoding="utf-8")
    assert read_sample(path)[0].verdict == "correct"


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (("verdict: null", "verdict: yes"), "allowed"),
        (("abn:", "abn_number:"), "unknown field"),
        (("  verdict: null", "verdict: null"), "unexpected line"),
        (("  abn:", "  # removed"), "is missing"),
        (("candidates: []", 'candidates: "none"'), "must be a list"),
    ],
)
def test_malformed_sample_is_rejected(tmp_path: Path, edit: tuple[str, str], message: str) -> None:
    path = tmp_path / "er_sample.yaml"
    write_sample(path, [sample_row(LINK)], seed=1)
    path.write_text(path.read_text(encoding="utf-8").replace(*edit), encoding="utf-8")
    with pytest.raises(SampleFormatError, match=message):
        read_sample(path)


def test_merge_verdicts_keeps_filled_ones_by_key() -> None:
    fresh = (sample_row(LINK), sample_row(UNMATCHED))
    previous = (
        replace(sample_row(LINK), verdict="incorrect", confidence=0.1),
        replace(sample_row(UNMATCHED), key="gone", verdict="correct"),
    )
    merged = merge_verdicts(fresh, previous)
    assert merged[0] == replace(fresh[0], verdict="incorrect")
    assert merged[1] == fresh[1]
