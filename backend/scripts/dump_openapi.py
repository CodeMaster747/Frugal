"""Write the OpenAPI document to a file without starting a server.

The frontend's `schema.d.ts` is generated from this. Generating it from a live
process meant it could only be checked by a job that had the whole stack up,
which is why `docs/06-project-structure.md` claimed CI failed on a diff while
nothing checked it at all. `app.openapi()` needs no port and no database.

Usage: python -m scripts.dump_openapi [path]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    from app.core.config import get_settings
    from app.main import create_app

    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    app = create_app(get_settings())
    # Not sorted: `openapi-typescript` emits paths in document order, so
    # sorting here would rewrite the whole of schema.d.ts on the first run
    # and make a real contract change indistinguishable from the reorder.
    destination.write_text(json.dumps(app.openapi(), indent=2) + "\n")
    print(f"wrote {destination} ({destination.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
