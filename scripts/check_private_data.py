"""Reject private data in the Git index or tracked tree."""

import argparse
import subprocess
from pathlib import PurePosixPath


def private_paths(paths: list[str]) -> list[str]:
    """Recognise the private-data directory, including symlink entries."""
    private = PurePosixPath("data/private")
    return [path for path in paths if PurePosixPath(path) == private or private in PurePosixPath(path).parents]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked", action="store_true", help="Check the full index (CI)")
    args = parser.parse_args()
    command = (
        ["git", "ls-files", "-z"]
        if args.tracked
        else ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
    )
    result = subprocess.run(command, check=True, capture_output=True)
    paths = [path.decode("utf-8", errors="surrogateescape") for path in result.stdout.split(b"\0") if path]
    blocked = private_paths(paths)
    if blocked:
        print("Private data must not be committed:\n" + "\n".join(blocked))
        return 1
    print("Private-data guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
