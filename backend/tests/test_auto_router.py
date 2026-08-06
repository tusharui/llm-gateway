from app.engine.auto_router import classify_complexity, select_model_for_tier
from types import SimpleNamespace


def u(content):
    return [{"role": "user", "content": content}]


def test_empty_messages_are_fast():
    assert classify_complexity([]) == "fast"


def test_greeting_is_fast():
    assert classify_complexity(u("Hi")) == "fast"


def test_simple_question_is_fast():
    assert classify_complexity(u("What is the capital of France?")) == "fast"


def test_explanation_is_balanced():
    assert classify_complexity(u("Explain the water cycle in simple terms.")) == "balanced"


def test_comparison_is_balanced():
    assert classify_complexity(u("Compare React and Vue for a small project.")) == "balanced"


def test_proof_is_powerful():
    assert (
        classify_complexity(
            u("Prove that the square root of 2 is irrational using a step-by-step proof.")
        )
        == "powerful"
    )


def test_code_task_is_powerful():
    assert (
        classify_complexity(
            u("Write a Python function to implement quicksort and analyze its time complexity.")
        )
        == "powerful"
    )


def test_advice_seeking_is_at_least_balanced():
    assert classify_complexity(u("Give me 3 tips to improve my resume.")) in (
        "balanced",
        "powerful",
    )


def test_long_conversation_scales_up():
    messages = [{"role": "user", "content": "ok"}] * 12 + [u("Explain caching in detail")[-1]]
    tier = classify_complexity(messages)
    assert tier in ("balanced", "powerful")


def test_pydantic_style_messages_are_supported():
    from app.schemas import ChatRequest

    req = ChatRequest(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "Explain caching in detail"}],
    )
    assert classify_complexity(req.messages) == "balanced"


def test_select_model_prefers_tier_model_when_provider_available():
    provider, model = select_model_for_tier("fast", [SimpleNamespace(name="groq")])
    assert provider == "groq"
    assert model == "llama-3.1-8b-instant"


def test_select_model_falls_back_when_no_provider_available():
    provider, model = select_model_for_tier("powerful", [])
    assert (provider, model) == ("groq", "llama-3.3-70b-versatile")
