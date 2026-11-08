"""Fetch script verification on small fixtures; nothing here touches the network."""

from pathlib import Path

import pytest

from scripts import fetch_public_data
from scripts.fetch_public_data import (
    REGISTER_MIN_ABNS,
    REGISTER_MIN_BYTES,
    fetch_register,
    verify_register,
)
from tests.conftest import GOOD_ROWS, write_register


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
    assert calls == [(fetch_public_data.REGISTER_URL, tmp_path / "public" / "register.csv")]
    assert not (tmp_path / "public" / "register.csv").exists()


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
    assert (tmp_path / "public" / "register.csv").exists()


def test_main_selects_datasets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fetched: list[str] = []
    monkeypatch.setitem(fetch_public_data.DATASETS, "register", lambda: fetched.append("register"))
    assert fetch_public_data.main(["--only", "register"]) == 0
    assert fetched == ["register"]
    with pytest.raises(SystemExit):
        fetch_public_data.main(["--only", "nonexistent"])
