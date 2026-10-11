"""Fabricated credential shapes for the redaction tests, assembled at runtime.

GitHub's secret scanning matches credential-shaped literals in a repository, and
on a public repository it reports them to the leak-notification programme. The
values in this file are fabricated test fixtures, not real credentials -- but a
scanner cannot tell the difference, and neither can whoever reads the
repository six months from now.

So the literals are assembled from fragments here, at import time. Nothing in
this repository contains a complete credential-shaped string, and the tests
still exercise the exact shapes the redactor has to catch.

If you add a case for another provider prefix, add the prefix here and build
the body the same way. Do not inline a full example. It will be detected, it
will raise an alert, and dismissing that alert teaches the next person that
dismissing alerts is a reasonable response to a real one.

``test_synthetic_credentials.py`` asserts that every fixture still matches the
pattern it exists to exercise. Without that, a typo in a fixture would make the
redaction test pass for the wrong reason.
"""

from __future__ import annotations

# Each value is (provider label, prefix, body) so a test can talk about the
# provider without ever holding a whole credential.
_PROVIDER_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("openai", "sk-", "abcdef0123456789" + "abcdef"),
    ("openai_project", "sk-proj-", "abcdefghijklmnop" + "qrstuvwx"),
    ("anthropic", "sk-ant-", "abcdef0123456789" + "abcdefgh"),
    ("github", "ghp_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + "012345"),
    ("google", "AIza", "SyA1234567890abcdefghijklmnopqrstuv"),
    ("aws_access_key_id", "AKIA", "IOSFODNN7" + "EXAMPLE"),
    ("slack", "xoxb-", "1234567890" + "12-abcdefghijkl"),
)

# Assembled once, so a test can pass the whole string to the code under test.
FIXTURES: dict[str, str] = {name: prefix + body for name, prefix, body in _PROVIDER_PREFIXES}

OPENAI_STYLE_KEY: str = FIXTURES["openai"]
OPENAI_PROJECT_KEY: str = FIXTURES["openai_project"]
ANTHROPIC_KEY: str = FIXTURES["anthropic"]
GITHUB_TOKEN: str = FIXTURES["github"]
GOOGLE_API_KEY: str = FIXTURES["google"]
AWS_ACCESS_KEY_ID: str = FIXTURES["aws_access_key_id"]
SLACK_TOKEN: str = FIXTURES["slack"]

# Not a provider key, but a realistic credential-bearing URL. httpx embeds the
# full request URL in its exception messages, which is the original reason
# app/redact.py exists.
KEY_IN_QUERY_URL: str = (
    "https://generativelanguage.googleapis.com/v1beta/models?"
    + "key=" + "abcdef0123456789" + "abcdef"
)

# A JWT. The header and payload are base64 of the literal words "header" and
# "payload"; only the signature segment needs to look random.
JWT: str = (
    "eyJhbGciOiJIUzI1NiJ9"
    + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0"
    + "." + "dozjgNryP4J3jVmNHl0w5N" + "XgL0n3I9PlFUP0THsR8U"
)

PRIVATE_KEY_BLOCK: str = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA" + "0Z3vJ8mQpXn2bLt"
    "\n-----END RSA PRIVATE KEY-----"
)

# Non-credential personal data. The redaction rules cover these because a real
# log line carries them next to the keys.
EMAIL: str = "jane.doe+work" + "@example.co.uk"
PHONE: str = "+44 20 7946 0958"
CARD_NUMBER: str = "4111 1111 1111 1111"
IPV4: str = "192.168.1.42"
UUID: str = "3f2504e0-4f89-11d3-" + "9a0c-0305e82c3301"
DATABASE_URL_WITH_PASSWORD: str = "postgres://admin:" + "hunter2" + "@db.internal:5432/prod"

# A lowercase run with no digits. app/redact.py's patterns do not match it, and
# evals/production.py treats it as a passphrase by entropy. Both facts are
# worth pinning.
PASSPHRASE: str = "correcthorsebatterystaple" * 2

ALL_CREDENTIAL_FIXTURES: tuple[str, ...] = tuple(FIXTURES.values()) + (
    KEY_IN_QUERY_URL,
    JWT,
    PRIVATE_KEY_BLOCK,
    DATABASE_URL_WITH_PASSWORD,
)


def fixture(provider: str) -> str:
    """Look up one fixture by provider label."""
    if provider not in FIXTURES:
        raise KeyError(f"no fixture named {provider!r}; have {sorted(FIXTURES)}")
    return FIXTURES[provider]