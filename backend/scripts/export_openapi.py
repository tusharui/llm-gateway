"""Dump the FastAPI OpenAPI schema to openapi.json.

The committed openapi.json is the source of truth for the typed contract in
frontend/src/types/backend.d.ts. Regenerate after any schema change:

    python -m scripts.export_openapi
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

OUT = ROOT / "openapi.json"

if __name__ == "__main__":
    OUT.write_text(
        json.dumps(app.openapi(), indent=2, default=str),
        encoding="utf-8",
    )
    print(f"OpenAPI schema written to {OUT}")
