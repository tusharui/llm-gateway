"""Deployment consistency: the app limit and the server limit must agree.

WS_MAX_MESSAGE_BYTES is checked after a frame has already been read into
memory. uvicorn's --ws-max-size is what refuses it beforehand. If the two drift
apart the smaller one is doing nothing useful, and the operator believes a
limit is in force that is not.

These tests read the deployment file rather than duplicating its contents, so
the number is checked where it is actually configured.
"""

from pathlib import Path

import pytest
import yaml

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_YAML = REPO_ROOT / "render.yaml"


@pytest.fixture(scope="module")
def render_config():
    if not RENDER_YAML.is_file():
        pytest.skip("render.yaml not present")
    return yaml.safe_load(RENDER_YAML.read_text(encoding="utf-8"))


def _start_command(render_config) -> str:
    service = next(
        s for s in render_config["services"] if s.get("runtime") == "python"
    )
    return " ".join(service["startCommand"].split())


def test_uvicorn_bounds_the_websocket_frame_size(render_config):
    """uvicorn's own default is 16 MiB.

    A 16 MiB frame is buffered and then handed to json.loads, which is the
    denial of service the application-level check cannot prevent: by the time
    WS_MAX_MESSAGE_BYTES runs, the bytes are already in memory.
    """
    command = _start_command(render_config)

    assert "--ws-max-size" in command, (
        "startCommand does not set --ws-max-size, so oversized frames are "
        "buffered at uvicorn's 16 MiB default before the app ever sees them"
    )


def test_server_frame_cap_matches_the_application_cap(render_config):
    command = _start_command(render_config)
    server_bytes = int(command.split("--ws-max-size")[1].split()[0])
    app_bytes = Settings(_env_file=None).ws_max_message_bytes

    assert server_bytes == app_bytes, (
        f"--ws-max-size is {server_bytes} but WS_MAX_MESSAGE_BYTES is {app_bytes}. "
        "The smaller of the two is the one that applies; if the server value is "
        "larger the app check fires after the frame is already resident."
    )


def test_healthcheck_is_readiness_not_liveness(render_config):
    """Unrelated to websockets, but this file is the deploy contract."""
    service = next(s for s in render_config["services"] if s.get("runtime") == "python")
    assert service["healthCheckPath"] == "/ready"


def test_production_still_requires_tls_to_postgres(render_config):
    service = next(s for s in render_config["services"] if s.get("runtime") == "python")
    env = {item["key"]: item.get("value") for item in service["envVars"]}
    assert env["DATABASE_SSL"] == "require"