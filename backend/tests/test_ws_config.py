"""Websocket limit settings.

Defaults only. The behaviour these bound is tested in test_websocket_auth.py;
what matters here is that the values exist, are sane, and are overridable from
the environment, because a limit nobody can raise without a redeploy is a limit
that ends up being worked around in the handler.
"""

import pytest

from app.config import Settings


@pytest.fixture
def settings_obj():
    # Constructed directly rather than imported, so a developer's real .env
    # cannot make a test pass or fail.
    return Settings(_env_file=None)


def test_websocket_limits_have_defaults(settings_obj):
    assert settings_obj.ws_max_message_bytes == 65536
    assert settings_obj.ws_max_messages_per_request == 64
    assert settings_obj.ws_max_turns_per_connection == 200
    assert settings_obj.ws_rate_limit is True


def test_websocket_limits_are_overridable_from_the_environment(monkeypatch):
    monkeypatch.setenv("WS_MAX_MESSAGE_BYTES", "1024")
    monkeypatch.setenv("WS_MAX_TURNS_PER_CONNECTION", "5")
    monkeypatch.setenv("WS_RATE_LIMIT", "false")

    configured = Settings(_env_file=None)

    assert configured.ws_max_message_bytes == 1024
    assert configured.ws_max_turns_per_connection == 5
    assert configured.ws_rate_limit is False


def test_default_frame_cap_is_far_below_the_uvicorn_default():
    """uvicorn's own default is 16 MiB.

    That is a reasonable server-side ceiling and a terrible application limit:
    it permits a single 16 MiB JSON frame to be buffered and then parsed. The
    application default has to be small enough to keep the parse cheap.
    """
    assert Settings(_env_file=None).ws_max_message_bytes < 1_048_576


def test_http_rate_limit_settings_are_untouched():
    """The websocket shares the HTTP budget, so those settings must not move."""
    configured = Settings(_env_file=None)
    assert configured.rate_limit_window_ms == 60000
    assert configured.rate_limit_max_requests == 60