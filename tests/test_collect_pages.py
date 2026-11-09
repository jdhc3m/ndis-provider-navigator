"""The collect_pages script end to end: seeding from a synthetic Maps export, one live fetch
from the local HTTP site, dry runs, missing inputs, and the private-data guards."""

import logging
import subprocess
from pathlib import Path

import pytest

from ndis.adapters.sqlite_repository import (
    FilePageStore,
    SqlAlchemyProviderLinkRepository,
    SqlAlchemyProviderRepository,
    create_sqlite_engine,
)
from ndis.domain.entity_resolution import EntityMatch, MatchStatus, MatchStrategy, ProviderLink
from ndis.domain.pages import FetchStatus, PageSource, text_hash
from scripts.collect_pages import DEFAULT_PAGES_DIR, ROOT, main
from tests.conftest import LocalSite, make_place


def write_links(db_path: Path, live_url: str) -> None:
    """Alpha is registered and cached; Beta's only crawl page is blank and it has no website;
    the local place is unregistered with a Maps website on the fixture server; Hire Co is not
    a provider."""
    SqlAlchemyProviderLinkRepository(create_sqlite_engine(db_path)).replace_all(
        [
            ProviderLink(
                make_place("place-alpha", "Alpha Therapy"),
                EntityMatch("place-alpha", "10000000001", MatchStatus.REGISTERED,
                            MatchStrategy.NAME, 0.9, "Alpha Therapy"),
            ),
            ProviderLink(
                make_place("place-beta", "Beta Supports"),
                EntityMatch("place-beta", None, MatchStatus.UNREGISTERED, MatchStrategy.NONE,
                            0.4, None),
            ),
            ProviderLink(
                make_place("place-local", "Local Care", website=live_url),
                EntityMatch("place-local", None, MatchStatus.UNREGISTERED, MatchStrategy.NONE,
                            0.6, None),
            ),
            ProviderLink(
                make_place("place-recruit", "Hire Co", category="Recruitment Agency",
                           website="https://hireco.example.com"),
                EntityMatch("place-recruit", None, MatchStatus.NOT_A_PROVIDER,
                            MatchStrategy.CATEGORY, 0.9, None),
            ),
        ]
    )  # fmt: skip


def run(maps: Path, db: Path, pages_dir: Path, *extra: str) -> int:
    return main(["--maps", str(maps), "--db", str(db), "--pages-dir", str(pages_dir), *extra])


def test_script_seeds_the_crawl_and_fetches_the_gap_live(
    tmp_path: Path,
    maps_export: Path,
    seeded_repository: SqlAlchemyProviderRepository,
    db_path: Path,
    local_site: LocalSite,
    caplog: pytest.LogCaptureFixture,
) -> None:
    live_url = local_site.url("/page")
    write_links(db_path, live_url)
    pages_dir = tmp_path / "private" / "pages"
    with caplog.at_level(logging.INFO, logger="collect_pages"):
        assert run(maps_export, db_path, pages_dir) == 0
    assert (
        "collected 3 providers, 2 with a website; seeded 3 pages for 2 providers "
        "(ok=2, empty=1); skipped 1 already cached; fetched 1 live (ok=1); planned 0"
    ) in caplog.text
    assert local_site.paths() == ["/robots.txt", "/page"]

    metas = FilePageStore(create_sqlite_engine(db_path), pages_dir).all()
    assert [(m.provider_key, m.url, m.status, m.source) for m in metas] == [
        ("10000000001", "https://www.10000000001.example.com.au/", FetchStatus.OK,
         PageSource.MAPS_CRAWL),
        ("10000000001", "https://www.10000000001.example.com.au/about", FetchStatus.OK,
         PageSource.MAPS_CRAWL),
        ("place-beta", "https://beta.example/", FetchStatus.EMPTY, PageSource.MAPS_CRAWL),
        ("place-local", live_url, FetchStatus.OK, PageSource.LIVE),
    ]  # fmt: skip
    live = metas[-1]
    assert live.title == "Acme Care page"
    assert live.text_path is not None and live.text_hash is not None
    live_text = (pages_dir / live.text_path).read_text(encoding="utf-8")
    assert "support coordination and daily personal activities" in live_text
    assert "About us" not in live_text
    assert text_hash(live_text) == live.text_hash
    assert len(list(pages_dir.iterdir())) == 3

    # A second run finds everything cached and makes no request.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="collect_pages"):
        assert run(maps_export, db_path, pages_dir) == 0
    assert "skipped 2 already cached; fetched 0 live (); planned 0" in caplog.text
    assert local_site.paths() == ["/robots.txt", "/page"]


def test_dry_run_lists_the_gap_without_requests_and_refresh_includes_cached(
    tmp_path: Path,
    maps_export: Path,
    seeded_repository: SqlAlchemyProviderRepository,
    db_path: Path,
    local_site: LocalSite,
    caplog: pytest.LogCaptureFixture,
) -> None:
    live_url = local_site.url("/page")
    write_links(db_path, live_url)
    pages_dir = tmp_path / "pages"
    with caplog.at_level(logging.INFO, logger="collect_pages"):
        assert run(maps_export, db_path, pages_dir, "--dry-run") == 0
    assert f"would fetch {live_url}" in caplog.text
    assert "would fetch https://www.10000000001.example.com.au/" not in caplog.text
    assert "fetched 0 live (); planned 1" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="collect_pages"):
        assert run(maps_export, db_path, pages_dir, "--dry-run", "--refresh", "--limit", "1") == 0
    assert "would fetch https://www.10000000001.example.com.au/" in caplog.text
    assert f"would fetch {live_url}" not in caplog.text
    assert local_site.requests == []
    # Seeding from the crawl still happens on a dry run (it is local); only live text is absent.
    assert len(list(pages_dir.iterdir())) == 2


def test_missing_inputs_stop_with_a_message(
    tmp_path: Path, maps_export: Path, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    pages_dir = tmp_path / "pages"
    with caplog.at_level(logging.ERROR, logger="collect_pages"):
        assert run(tmp_path / "nope.db", db_path, pages_dir) == 1
        assert "Maps export not found" in caplog.text
        assert run(maps_export, tmp_path / "no-links.db", pages_dir) == 1
        assert "database not found" in caplog.text
    assert not pages_dir.exists()


def test_page_text_lives_under_ignored_private_data_only() -> None:
    assert DEFAULT_PAGES_DIR.relative_to(ROOT) == Path("data/private/pages")
    tracked = subprocess.run(
        ["git", "ls-files", "data"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    assert tracked.stdout == ""
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "data/private/pages/0123456789abcdef.txt"], cwd=ROOT
    )
    assert ignored.returncode == 0
