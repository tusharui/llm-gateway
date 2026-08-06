import pytest
from app.routes.chat import calculate_cost


def test_groq_cost_matches_rates():
    expected = 1000 * 0.00000015 + 500 * 0.0000006
    assert calculate_cost("groq", 1000, 500) == pytest.approx(expected)


def test_zero_tokens_zero_cost():
    assert calculate_cost("gemini", 0, 0) == 0.0


def test_unknown_provider_uses_default_rate():
    expected = 1000 * 0.0000005 + 1000 * 0.0000015
    assert calculate_cost("unknown-provider", 1000, 1000) == pytest.approx(expected)


def test_cost_grows_with_tokens():
    assert calculate_cost("groq", 2000, 0) > calculate_cost("groq", 1000, 0)
