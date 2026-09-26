from app.redact import redact, REDACTED

# Synthetic credential — never put a real provider key in a test fixture.
FAKE_GEMINI_KEY = "AQ.FAKEKEYFORTESTS0000000000000000000000"
GEMINI_URL = (
    "Client error '404 Not Found' for url "
    "'https://generativelanguage.googleapis.com/v1beta/models/x:generateContent"
    f"?alt=sse&key={FAKE_GEMINI_KEY}'"
)


def test_gemini_query_param_key_is_redacted():
    out = redact(GEMINI_URL)
    assert FAKE_GEMINI_KEY not in out
    assert REDACTED in out


def test_bearer_token_is_redacted():
    assert "sk-gateway-dev-key" not in redact("Authorization: Bearer sk-gateway-dev-key")


def test_provider_key_prefixes_are_redacted():
    for secret in ("sk-proj-abcdefghijklmnop", "AIzaSyA1234567890abcdefg"):
        assert secret not in redact(f"auth failed for {secret}")


def test_connection_string_password_is_redacted():
    out = redact("could not connect to postgresql+asyncpg://user:hunter2@host/db")
    assert "hunter2" not in out
    assert "user:" in out and "@host/db" in out


def test_plain_error_is_untouched():
    msg = "All providers failed: gemini: timeout"
    assert redact(msg) == msg
