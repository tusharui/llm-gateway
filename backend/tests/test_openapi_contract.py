"""The committed OpenAPI contract must match the application.

`backend/openapi.json` is not documentation that happens to be stale. It is
the source of truth the TypeScript contract in
`frontend/src/types/backend.d.ts` mirrors, so drift is not cosmetic: a route
added or a field renamed without regenerating leaves the frontend typed
against a contract the server no longer honours.

The committed file was missing `/ready`, and nothing in the suite noticed.
This compares the two documents structurally rather than byte-for-byte, so
irrelevant ordering noise does not fail the build while a real route change
does.
"""

import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
COMMITTED = BACKEND / "openapi.json"


@pytest.fixture(scope="module")
def committed():
    if not COMMITTED.is_file():
        pytest.skip("openapi.json not present")
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def live():
    from app.main import app

    return app.openapi()


def test_committed_paths_match_the_application(committed, live):
    missing = sorted(set(live["paths"]) - set(committed["paths"]))
    extra = sorted(set(committed["paths"]) - set(live["paths"]))

    assert not missing, (
        "routes exist in the application but not in openapi.json: "
        f"{missing}. Run: python -m scripts.export_openapi"
    )
    assert not extra, (
        "openapi.json documents routes the application does not serve: "
        f"{extra}. Run: python -m scripts.export_openapi"
    )


def test_committed_operations_match(committed, live):
    """Same path, different verb, is still drift.

    A route that moved from POST to PUT, or gained a DELETE, would pass a
    path-only comparison and break the frontend.
    """
    drift = []
    for path in sorted(set(committed["paths"]) & set(live["paths"])):
        committed_methods = {
            m.upper() for m in committed["paths"][path] if m.lower() != "parameters"
        }
        live_methods = {
            m.upper() for m in live["paths"][path] if m.lower() != "parameters"
        }
        if committed_methods != live_methods:
            drift.append(
                f"{path}: committed {sorted(committed_methods)} "
                f"vs live {sorted(live_methods)}"
            )

    assert not drift, (
        "HTTP methods drifted:\n" + "\n".join(drift) + "\nRun: python -m scripts.export_openapi"
    )


def test_committed_schema_components_exist(committed, live):
    """Every schema the committed document references must still be defined."""
    committed_schemas = set(committed.get("components", {}).get("schemas", {}))
    live_schemas = set(live.get("components", {}).get("schemas", {}))

    missing = sorted(committed_schemas - live_schemas)
    assert not missing, (
        f"openapi.json references schemas the app no longer defines: {missing}. "
        "Run: python -m scripts.export_openapi"
    )


def test_authenticated_routes_are_present_in_the_contract(committed):
    """A guard against publishing the security boundary as an accident.

    Not a behavioural test -- test_authz.py owns that. This exists so a
    reviewer reading openapi.json can see which routes the gateway considers
    privileged, rather than having to reconstruct it from the middleware.
    """
    for path in ("/chat", "/api-keys", "/analytics/summary", "/chat-history/sessions"):
        assert path in committed["paths"], f"{path} vanished from the contract"


def test_ready_endpoint_is_documented(committed):
    """The one route the deploy healthcheck depends on.

    render.yaml points healthCheckPath at /ready. It was missing from the
    committed contract, which is the exact drift this file exists to catch.
    """
    assert "/ready" in committed["paths"]
    assert "get" in committed["paths"]["/ready"]