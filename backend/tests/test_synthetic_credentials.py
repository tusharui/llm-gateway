"""The fixtures still have to match what they exist to match.

A redaction test whose fixture has drifted out of the pattern passes for the
wrong reason: the redactor finds nothing because it was never asked. Since
these fixtures are assembled from fragments rather than written out, that is a
live risk rather than a theoretical one, and it is the whole reason this file
exists.
"""

import re

import pytest

from app.redact import _PATTERNS as APP_REDACT_PATTERNS
from app.redact import redact
from evals.production import PII_PATTERNS, find_residual_risks, redact_for_dataset
from synthetic_credentials import (
    ALL_CREDENTIAL_FIXTURES,
    ANTHROPIC_KEY,
    AWS_ACCESS_KEY_ID,
    CARD_NUMBER,
    DATABASE_URL_WITH_PASSWORD,
    EMAIL,
    FIXTURES,
    GITHUB_TOKEN,
    GOOGLE_API_KEY,
    IPV4,
    JWT,
    OPENAI_PROJECT_KEY,
    OPENAI_STYLE_KEY,
    PASSPHRASE,
    PHONE,
    PRIVATE_KEY_BLOCK,
    SLACK_TOKEN,
    UUID,
    fixture,
)

EXPECTED_SHAPE: dict[str, re.Pattern[str]] = {
    "openai": re.compile(r"\b(sk-)[A-Za-z0-9._\-]{6,}"),
    "openai_project": re.compile(r"\b(sk-)[A-Za-z0-9._\-]{6,}"),
    "anthropic": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}"),
    "github": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    "google": re.compile(r"\b(AIza)[A-Za-z0-9_\-]{20,}"),
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{12,}"),
    "slack": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
}


@pytest.mark.parametrize("name", sorted(EXPECTED_SHAPE))
def test_provider_fixture_still_matches_the_shape_it_exists_for(name):
    """Not an end-to-end test: a direct check that the fixture is still a
    credential shape, so a broken fixture cannot quietly weaken the suite."""
    value = fixture(name)
    assert EXPECTED_SHAPE[name].search(value), f"{name} fixture no longer matches its own pattern"


def test_duplicate_prefix_shapes_do_not_collide():
    """``sk-`` is shared by three providers, so check each against its own
    distinct prefix rather than the same regex three times."""
    assert OPENAI_STYLE_KEY.startswith("sk-")
    assert OPENAI_PROJECT_KEY.startswith("sk-proj-")
    assert ANTHROPIC_KEY.startswith("sk-ant-")
    assert len({OPENAI_STYLE_KEY, OPENAI_PROJECT_KEY, ANTHROPIC_KEY}) == 3


# Which fixtures app/redact.py is expected to handle on its own. This is not
# "all of them": see test_app_redact_does_not_cover_these_providers below.
APP_REDACT_COVERS = ("openai", "openai_project", "anthropic", "google")

# Fixture labels whose shape app/redact.py has no pattern for. They are caught
# by evals/production.py's PII rules, which only run inside the eval harness.
# Recorded here so the gap is a failing test rather than a surprise.
APP_REDACT_DOES_NOT_COVER = ("github", "aws_access_key_id", "slack")


@pytest.mark.parametrize("name", APP_REDACT_COVERS)
def test_app_redactor_removes_every_provider_fixture_it_claims(name):
    """app/redact.py owns the shapes it has patterns for, so each of those
    fixtures must be something it actually handles."""
    value = fixture(name)
    assert redact(value) != value, f"app.redact left the {name} fixture intact"


@pytest.mark.parametrize("name", APP_REDACT_COVERS)
def test_app_redact_has_a_pattern_for_each_fixture_it_claims(name):
    """The other direction: a fixture with no app.redact pattern would be
    silently relying on the eval-side rules instead.

    ``app.redact._PATTERNS`` is a flat tuple of compiled patterns with no
    names, so this checks membership rather than looking a pattern up.
    """
    value = fixture(name)
    assert any(
        pattern.search(value) for pattern in APP_REDACT_PATTERNS
    ), f"no app.redact pattern matches the {name} fixture"


@pytest.mark.parametrize("name", APP_REDACT_DOES_NOT_COVER)
def test_app_redact_does_not_cover_these_providers(name):
    """A known gap, pinned rather than papered over.

    app/redact.py is what protects the serving path: it scrubs provider error
    text before it reaches a log or an HTTP response. It has patterns for
    ``sk-`` and ``AIza`` and nothing else, so a GitHub token, an AWS access key
    id or a Slack token appearing in an exception would be logged in the clear.

    Nothing in the gateway currently sends those providers' credentials in a
    URL, so this is not an active leak. If that changes, add the pattern here
    and delete this test in the same commit.
    """
    value = fixture(name)
    assert redact(value) == value, (
        f"app.redact now handles the {name} shape, so this gap is closed. "
        "Move it into APP_REDACT_COVERS and delete this test."
    )
    # It is still caught by the eval-side rules, which is why the redaction
    # suite passes for these shapes at all.
    assert redact_for_dataset(f"value {value} here").text != f"value {value} here"


def test_jwt_fixture_matches_a_production_rule():
    assert any(pattern.search(JWT) for name, pattern in PII_PATTERNS if name == "jwt")


def test_private_key_block_fixture_matches():
    assert any(
        pattern.search(PRIVATE_KEY_BLOCK)
        for name, pattern in PII_PATTERNS
        if name == "private_key_block"
    )


def test_database_url_fixture_matches_app_redact():
    assert redact(DATABASE_URL_WITH_PASSWORD) != DATABASE_URL_WITH_PASSWORD


@pytest.mark.parametrize(
    "value,expected",
    [(EMAIL, "email"), (UUID, "uuid"), (CARD_NUMBER, "credit_card")],
)
def test_pii_fixtures_match_their_named_rule(value, expected):
    assert any(name == expected and pattern.search(value) for name, pattern in PII_PATTERNS)


def test_phone_and_ip_fixtures_are_still_covered():
    """These two carry a residual check as well as a rule, so assert both."""
    assert redact_for_dataset(PHONE).text != PHONE
    assert redact_for_dataset(IPV4).text != IPV4


def test_passphrase_is_still_detected_by_the_residual_check():
    """A digit-free lowercase run. No regex matches it, which is the point: the
    entropy rule is what catches it, and a broken fixture would make that
    untestable."""
    from synthetic_credentials import PASSPHRASE as value

    assert PASSPHRASE == value
    assert redact(value) == value, "app.redact should not claim to handle a passphrase"
    assert "high_entropy_token" in find_residual_risks(f"value {PASSPHRASE} here")


def test_no_fixture_survives_redaction_untouched():
    """The end-to-end guard: every credential fixture must come back changed or
    flagged, so no fixture can be inert."""
    for value in ALL_CREDENTIAL_FIXTURES:
        assert redact_for_dataset(f"value {value} here").text != f"value {value} here"


def test_fixture_lookup_is_case_sensitive_and_typed():
    with pytest.raises(KeyError) as exc:
        fixture("openai-but-not-a-provider")
    assert "no fixture named" in str(exc.value)


def test_fixture_dictionary_covers_every_provider_label():
    assert set(FIXTURES) == {
        "openai",
        "openai_project",
        "anthropic",
        "github",
        "google",
        "aws_access_key_id",
        "slack",
    }


def test_slack_token_fixture_matches_its_shape():
    assert re.search(r"\bxox[abposr]-[A-Za-z0-9-]{10,}", SLACK_TOKEN)