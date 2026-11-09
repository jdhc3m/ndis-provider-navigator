"""Exercise the tooling through its command-line contracts."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_no_stubs import check_repository

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("layer", ["domain", "application", "adapters", "api"])
def test_layers_import(layer: str) -> None:
    assert importlib.import_module(f"ndis.{layer}").__name__ == f"ndis.{layer}"


@pytest.mark.parametrize(
    "source",
    [
        "def incomplete():\n    pass\n",
        'def incomplete():\n    """A description is not an implementation."""\n    pass\n',
        "async def incomplete():\n    pass\n",
        "def incomplete():\n    raise NotImplementedError('missing')\n",
        "# TODO: finish implementation\n",
    ],
)
def test_stub_guard_rejects_unfinished_code(tmp_path: Path, source: str) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "example.py").write_text(source)
    assert check_repository(tmp_path)


def test_stub_guard_requires_api_test_reference(tmp_path: Path) -> None:
    api = tmp_path / "src" / "ndis" / "api"
    api.mkdir(parents=True)
    (api / "routes.py").write_text("def providers():\n    return []\n")
    assert "has no test reference" in "\n".join(check_repository(tmp_path))
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_routes.py").write_text("def test_providers():\n    assert providers() == []\n")
    assert check_repository(tmp_path) == []


def test_repository_has_no_stubs() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/check_no_stubs.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_private_data_guard_checks_staged_and_tracked_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    private = tmp_path / "data" / "private"
    private.mkdir(parents=True)
    (private / "business.csv").write_text("synthetic test fixture,not customer data\n")
    (tmp_path / "safe.md").write_text("Public documentation\n")
    guard = [sys.executable, str(ROOT / "scripts" / "check_private_data.py")]
    subprocess.run(["git", "add", "safe.md"], cwd=tmp_path, check=True)
    assert subprocess.run(guard, cwd=tmp_path, capture_output=True).returncode == 0
    subprocess.run(["git", "add", "data/private/business.csv"], cwd=tmp_path, check=True)
    for args in ([], ["--tracked"]):
        result = subprocess.run(guard + args, cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 1
        assert "data/private/business.csv" in result.stdout


@pytest.mark.parametrize(
    ("staged", "blocked"),
    [
        ("data/private/maps_brisbane.db", True),
        ("data/private/maps/export/providers.sqlite", True),
        ("data/private.md", False),
        ("docs/data/private/notes.md", False),
    ],
)
def test_private_data_guard_matches_the_private_directory_exactly(
    tmp_path: Path, staged: str, blocked: bool
) -> None:
    """Any depth under ``data/private/`` is blocked even when the file is otherwise ignored
    (as the real Maps export is) and was force-added; look-alike paths elsewhere pass."""
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("data/\n*.db\n")
    target = tmp_path / staged
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("synthetic test fixture\n")
    subprocess.run(["git", "add", "--force", staged], cwd=tmp_path, check=True)
    guard = [sys.executable, str(ROOT / "scripts" / "check_private_data.py")]
    result = subprocess.run(guard, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == (1 if blocked else 0), result.stdout
    assert (staged in result.stdout) is blocked
