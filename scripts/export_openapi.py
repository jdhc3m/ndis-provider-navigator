"""Write the API's OpenAPI document as JSON to a path, for the front end's generated types.

The output has sorted keys and a fixed indent, so re-running it on an unchanged API is
byte-identical and CI can diff the generated ``web/src/api/schema.d.ts`` against it.
"""

import argparse
import json
from pathlib import Path

from ndis.api.app import create_app


def export_openapi(destination: Path) -> int:
    """Write the schema to ``destination`` and return the number of paths it documents."""
    document = create_app().openapi()
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(document.get("paths", {}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="where to write the JSON document")
    args = parser.parse_args()
    count = export_openapi(args.destination)
    print(f"wrote {count} paths to {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
