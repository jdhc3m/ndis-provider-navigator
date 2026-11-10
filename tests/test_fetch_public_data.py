"""Fetch script verification on small fixtures; nothing here touches the network."""

from pathlib import Path

import pytest

from scripts import fetch_public_data
from scripts.fetch_public_data import (
    CATALOGUE_MIN_BYTES,
    CATALOGUE_MIN_ITEMS,
    POSTCODES_MIN_BYTES,
    POSTCODES_MIN_COUNT,
    PRICING_MIN_BYTES,
    PRICING_MIN_PAGES,
    REGISTER_MIN_ABNS,
    REGISTER_MIN_BYTES,
    fetch_catalogue,
    fetch_postcodes,
    fetch_pricing_pdf,
    fetch_register,
    verify_catalogue,
    verify_postcodes,
    verify_pricing_pdf,
    verify_register,
)
from tests.conftest import (
    CURRENT_ROWS,
    GOOD_ROWS,
    LEGACY_ROWS,
    POSTCODE_ROWS,
    write_catalogue,
    write_postcodes,
    write_register,
)


def test_small_fixture_is_rejected_for_size_and_abn_count(register_csv: Path) -> None:
    problems = verify_register(register_csv)
    assert len(problems) == 2
    assert f"exceeds {REGISTER_MIN_BYTES} bytes" in problems[0]
    assert f"a full register exceeds {REGISTER_MIN_ABNS}" in problems[1]


def test_thresholds_are_strict_greater_than(register_csv: Path) -> None:
    size = register_csv.stat().st_size
    assert verify_register(register_csv, min_bytes=size - 1, min_abns=4) == []
    assert "5 distinct ABNs" in verify_register(register_csv, min_bytes=0, min_abns=5)[0]
    assert f"{size} bytes" in verify_register(register_csv, min_bytes=size, min_abns=0)[0]


def test_non_register_file_is_rejected(tmp_path: Path) -> None:
    junk = tmp_path / "junk.csv"
    junk.write_text("<html>blocked</html>\n", encoding="utf-8")
    problems = verify_register(junk, min_bytes=0, min_abns=0)
    assert any("not a register CSV" in problem for problem in problems)


def test_fetch_register_deletes_truncated_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_download(url: str, destination: Path) -> int:
        calls.append((url, destination))
        destination.parent.mkdir(parents=True)
        write_register(destination, GOOD_ROWS)
        return destination.stat().st_size

    monkeypatch.setattr(fetch_public_data, "download", fake_download)
    with pytest.raises(SystemExit, match="rejected and deleted"):
        fetch_register(tmp_path / "public")
    assert calls == [(fetch_public_data.REGISTER_URL, tmp_path / "public" / "register.part")]
    assert list((tmp_path / "public").iterdir()) == []


def test_fetch_register_removes_partial_file_when_download_breaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_download(url: str, destination: Path) -> int:
        destination.parent.mkdir(parents=True)
        destination.write_text("half a file", encoding="utf-8")
        raise ConnectionResetError("dropped")

    monkeypatch.setattr(fetch_public_data, "download", broken_download)
    with pytest.raises(ConnectionResetError):
        fetch_register(tmp_path / "public")
    assert list((tmp_path / "public").iterdir()) == []


def test_fetch_register_keeps_verified_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_download(url: str, destination: Path) -> int:
        destination.parent.mkdir(parents=True)
        write_register(destination, GOOD_ROWS)
        return destination.stat().st_size

    monkeypatch.setattr(fetch_public_data, "download", fake_download)
    monkeypatch.setattr(fetch_public_data, "REGISTER_MIN_BYTES", 0)
    monkeypatch.setattr(fetch_public_data, "REGISTER_MIN_ABNS", 1)
    assert fetch_register(tmp_path / "public") == tmp_path / "public" / "register.csv"
    assert [p.name for p in (tmp_path / "public").iterdir()] == ["register.csv"]


def test_small_catalogue_is_rejected_for_size_and_item_count(catalogue_xlsx: Path) -> None:
    problems = verify_catalogue(catalogue_xlsx)
    assert len(problems) == 2
    assert f"exceeds {CATALOGUE_MIN_BYTES} bytes" in problems[0]
    assert f"a full catalogue exceeds {CATALOGUE_MIN_ITEMS}" in problems[1]


def test_catalogue_thresholds_are_strict_greater_than(catalogue_xlsx: Path) -> None:
    size = catalogue_xlsx.stat().st_size
    items = len(CURRENT_ROWS) + len(LEGACY_ROWS)
    assert verify_catalogue(catalogue_xlsx, min_bytes=size - 1, min_items=items - 1) == []
    assert f"{items} distinct" in verify_catalogue(catalogue_xlsx, min_bytes=0, min_items=items)[0]
    assert f"{size} bytes" in verify_catalogue(catalogue_xlsx, min_bytes=size, min_items=0)[0]


def test_older_catalogue_counts_no_items(tmp_path: Path) -> None:
    old_year = [dict(row, **{"Start date": 20250701}) for row in CURRENT_ROWS + LEGACY_ROWS]
    path = write_catalogue(tmp_path / "old.xlsx", old_year, [])
    assert "0 distinct 2026-27 support items" in verify_catalogue(path, min_bytes=0, min_items=0)[0]


def test_non_workbook_catalogue_is_rejected(tmp_path: Path) -> None:
    junk = tmp_path / "junk.part"
    junk.write_text("<html>blocked</html>\n", encoding="utf-8")
    problems = verify_catalogue(junk, min_bytes=0, min_items=0)
    assert any("not a support catalogue workbook" in problem for problem in problems)


def test_fetch_catalogue_deletes_truncated_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_download(url: str, destination: Path) -> int:
        calls.append((url, destination))
        destination.parent.mkdir(parents=True)
        write_catalogue(destination, CURRENT_ROWS, LEGACY_ROWS)
        return destination.stat().st_size

    monkeypatch.setattr(fetch_public_data, "download", fake_download)
    with pytest.raises(SystemExit, match="catalogue download rejected and deleted"):
        fetch_catalogue(tmp_path / "public")
    expected = (fetch_public_data.CATALOGUE_URL, tmp_path / "public" / "support_catalogue.part")
    assert calls == [expected]
    assert list((tmp_path / "public").iterdir()) == []


def test_fetch_catalogue_removes_partial_file_when_download_breaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_download(url: str, destination: Path) -> int:
        destination.parent.mkdir(parents=True)
        destination.write_text("half a file", encoding="utf-8")
        raise ConnectionResetError("dropped")

    monkeypatch.setattr(fetch_public_data, "download", broken_download)
    with pytest.raises(ConnectionResetError):
        fetch_catalogue(tmp_path / "public")
    assert list((tmp_path / "public").iterdir()) == []


def test_fetch_catalogue_keeps_verified_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_download(url: str, destination: Path) -> int:
        destination.parent.mkdir(parents=True)
        write_catalogue(destination, CURRENT_ROWS, LEGACY_ROWS)
        return destination.stat().st_size

    monkeypatch.setattr(fetch_public_data, "download", fake_download)
    monkeypatch.setattr(fetch_public_data, "CATALOGUE_MIN_BYTES", 0)
    monkeypatch.setattr(fetch_public_data, "CATALOGUE_MIN_ITEMS", 1)
    assert fetch_catalogue(tmp_path / "public") == tmp_path / "public" / "support_catalogue.xlsx"
    assert [p.name for p in (tmp_path / "public").iterdir()] == ["support_catalogue.xlsx"]


def test_main_selects_datasets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fetched: list[str] = []
    monkeypatch.setitem(fetch_public_data.DATASETS, "register", lambda: fetched.append("register"))
    monkeypatch.setitem(
        fetch_public_data.DATASETS, "catalogue", lambda: fetched.append("catalogue")
    )
    monkeypatch.setitem(
        fetch_public_data.DATASETS, "pricing_pdf", lambda: fetched.append("pricing_pdf")
    )
    monkeypatch.setitem(
        fetch_public_data.DATASETS, "postcodes", lambda: fetched.append("postcodes")
    )
    assert fetch_public_data.main(["--only", "register"]) == 0
    assert fetched == ["register"]
    assert fetch_public_data.main(["--only", "pricing_pdf"]) == 0
    assert fetched == ["register", "pricing_pdf"]
    assert fetch_public_data.main([]) == 0
    assert fetched == [
        "register",
        "pricing_pdf",
        "catalogue",
        "postcodes",
        "pricing_pdf",
        "register",
    ]
    with pytest.raises(SystemExit):
        fetch_public_data.main(["--only", "nonexistent"])


def write_mini_pdf(path: Path, pages: int = 60) -> Path:
    """A real, tiny PDF with ``pages`` blank pages: readable by pymupdf, small on disk."""
    import pymupdf

    document = pymupdf.open()
    for _ in range(pages):
        document.new_page()
    document.save(path)
    document.close()
    return path


def test_small_pricing_download_is_rejected_for_size_and_pages(tmp_path: Path) -> None:
    small = write_mini_pdf(tmp_path / "small.pdf", pages=10)
    problems = verify_pricing_pdf(small)
    assert len(problems) == 2
    assert f"exceeds {PRICING_MIN_BYTES} bytes" in problems[0]
    assert f"at least {PRICING_MIN_PAGES}" in problems[1]


def test_non_pdf_pricing_download_is_rejected(tmp_path: Path) -> None:
    junk = tmp_path / "junk.part"
    junk.write_text("<html>Just a moment...</html>" * 60_000, encoding="utf-8")
    problems = verify_pricing_pdf(junk)
    assert "file does not start with the %PDF magic bytes" in problems


def test_unreadable_pricing_pdf_is_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"%PDF-1.4 not really a pdf")
    problems = verify_pricing_pdf(fake)
    assert any("not a readable PDF" in problem for problem in problems)


def test_fetch_pricing_pdf_deletes_truncated_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_download(url: str, destination: Path) -> int:
        calls.append((url, destination))
        destination.parent.mkdir(parents=True)
        write_mini_pdf(destination, pages=10)
        return destination.stat().st_size

    monkeypatch.setattr(fetch_public_data, "download", fake_download)
    with pytest.raises(SystemExit, match="pricing PDF download rejected and deleted"):
        fetch_pricing_pdf(tmp_path / "public")
    expected = (
        fetch_public_data.PRICING_PDF_URL,
        tmp_path / "public" / "pricing_arrangements.part",
    )
    assert calls == [expected]
    assert list((tmp_path / "public").iterdir()) == []


def test_fetch_pricing_pdf_keeps_verified_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_download(url: str, destination: Path) -> int:
        destination.parent.mkdir(parents=True)
        write_mini_pdf(destination)
        return destination.stat().st_size

    monkeypatch.setattr(fetch_public_data, "download", fake_download)
    monkeypatch.setattr(fetch_public_data, "PRICING_MIN_BYTES", 0)
    monkeypatch.setattr(fetch_public_data, "PRICING_MIN_PAGES", 1)
    destination = fetch_pricing_pdf(tmp_path / "public")
    assert destination == tmp_path / "public" / "pricing_arrangements.pdf"
    assert [p.name for p in (tmp_path / "public").iterdir()] == ["pricing_arrangements.pdf"]


def test_small_postcodes_fixture_is_rejected_for_size_and_count(postcodes_csv: Path) -> None:
    problems = verify_postcodes(postcodes_csv)
    assert len(problems) == 2
    assert f"exceeds {POSTCODES_MIN_BYTES} bytes" in problems[0]
    assert f"a full file exceeds {POSTCODES_MIN_COUNT}" in problems[1]
    assert verify_postcodes(postcodes_csv, min_bytes=0, min_count=6) == []
    assert "7 located postcodes" in verify_postcodes(postcodes_csv, min_bytes=0, min_count=7)[0]


def test_postcodes_without_the_expected_queensland_rows_are_rejected(tmp_path: Path) -> None:
    cairns_only = write_postcodes(tmp_path / "p.csv", [POSTCODE_ROWS[-1]])
    problems = verify_postcodes(cairns_only, min_bytes=0, min_count=0)
    assert problems == ["file lacks located rows for postcodes ['4000', '4101', '4350']"]


def test_non_postcodes_file_is_rejected(tmp_path: Path) -> None:
    junk = tmp_path / "junk.csv"
    junk.write_text("<html>blocked</html>\n", encoding="utf-8")
    problems = verify_postcodes(junk, min_bytes=0, min_count=0)
    assert problems == [
        "file is not a postcodes CSV: postcode header is missing columns: "
        "['postcode', 'lat', 'long']"
    ]


def test_fetch_postcodes_deletes_truncated_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, Path]] = []

    def fake_download(url: str, destination: Path) -> int:
        calls.append((url, destination))
        destination.parent.mkdir(parents=True)
        write_postcodes(destination, POSTCODE_ROWS)
        return destination.stat().st_size

    monkeypatch.setattr(fetch_public_data, "download", fake_download)
    with pytest.raises(SystemExit, match="postcodes download rejected and deleted"):
        fetch_postcodes(tmp_path / "public")
    assert calls == [
        (fetch_public_data.POSTCODES_URL, tmp_path / "public" / "australian_postcodes.part")
    ]
    assert list((tmp_path / "public").iterdir()) == []


def test_fetch_postcodes_keeps_verified_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_download(url: str, destination: Path) -> int:
        write_postcodes(destination, POSTCODE_ROWS)
        return destination.stat().st_size

    def broken_download(url: str, destination: Path) -> int:
        destination.parent.mkdir(parents=True)
        destination.write_text("half", encoding="utf-8")
        raise ConnectionResetError("dropped")

    monkeypatch.setattr(fetch_public_data, "download", broken_download)
    with pytest.raises(ConnectionResetError):
        fetch_postcodes(tmp_path / "public")
    assert list((tmp_path / "public").iterdir()) == []
    monkeypatch.setattr(fetch_public_data, "download", fake_download)
    monkeypatch.setattr(fetch_public_data, "POSTCODES_MIN_BYTES", 0)
    monkeypatch.setattr(fetch_public_data, "POSTCODES_MIN_COUNT", 1)
    assert fetch_postcodes(tmp_path / "public") == tmp_path / "public" / "australian_postcodes.csv"
    assert [p.name for p in (tmp_path / "public").iterdir()] == ["australian_postcodes.csv"]
