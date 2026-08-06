import re
from app.schemas import ChatRequest
from app.config import settings


MODEL_TIERS = {
    "fast": {
        "description": "Simple, factual, or short queries",
        "models": [
            ("groq", "llama-3.1-8b-instant"),
            ("groq", "gemma2-9b-it"),
            ("gemini", "gemini-2.0-flash-lite"),
            ("gemini", "gemini-2.0-flash"),
        ],
        "cost_per_1k": 0.0001,
    },
    "balanced": {
        "description": "Moderate complexity, general purpose",
        "models": [
            ("groq", "llama-3.3-70b-versatile"),
            ("groq", "mixtral-8x7b-32768"),
            ("gemini", "gemini-1.5-flash"),
            ("openrouter", "openai/gpt-4o-mini"),
            ("openrouter", "anthropic/claude-3.5-haiku"),
        ],
        "cost_per_1k": 0.0005,
    },
    "powerful": {
        "description": "Complex reasoning, analysis, code, creative writing",
        "models": [
            ("gemini", "gemini-1.5-pro"),
            ("openrouter", "meta-llama/llama-3.3-70b-instruct"),
            ("openrouter", "google/gemini-2.0-flash-001"),
        ],
        "cost_per_1k": 0.002,
    },
}


def classify_complexity(messages: list) -> str:
    if not messages:
        return "fast"

    last_user_msg = ""
    for m in reversed(messages):
        content = m.content if hasattr(m, "content") else (m.get("content", "") if isinstance(m, dict) else "")
        role = m.role if hasattr(m, "role") else (m.get("role", "") if isinstance(m, dict) else "")
        if role == "user":
            last_user_msg = content
            break

    if not last_user_msg:
        return "fast"

    word_count = len(last_user_msg.split())
    char_count = len(last_user_msg)

    score = 0

    if char_count > 500:
        score += 3
    elif char_count > 200:
        score += 2
    elif char_count > 50:
        score += 1

    if word_count > 100:
        score += 3
    elif word_count > 40:
        score += 2
    elif word_count > 15:
        score += 1

    complex_keywords = [
        r"\bexplain\b", r"\banalyz\w*\b", r"\bcompar\w*\b", r"\bevaluate\b",
        r"\bcritique\b", r"\breview\b", r"\bdesign\w*\b", r"\barchitect\w*\b",
        r"\bcode\b", r"\bimplement\w*\b", r"\bdebug\w*\b", r"\brefactor\w*\b",
        r"\boptimiz\w*\b", r"\bstep.by.step\b", r"\breason\w*\b", r"\blogic\b",
        r"\bproof\b", r"\bprove\b", r"\bderiv\w*\b", r"\bessay\b", r"\barticle\b",
        r"\bstory\b", r"\bcreativ\w*\b", r"\bnarrativ\w*\b", r"\bpoem\b",
        r"\brecommend\w*\b", r"\bmath\w*\b", r"\bequation\b", r"\bformula\w*\b",
        r"\bcalculat\w*\b", r"\bsolve\b", r"\bstrateg\w*\b", r"\broadmap\b",
        r"\bplan\w*\b", r"\btips?\b", r"\badvice\b", r"\bimprove\w*\b",
        r"\bsuggest\w*\b",
    ]
    for pattern in complex_keywords:
        if re.search(pattern, last_user_msg, re.IGNORECASE):
            score += 2

    complex_phrases = [
        r"\btrade.?offs?\b",
        r"\bpros and cons\b",
        r"\balternatives\b",
        r"\bsystem design\b",
        r"\bhow does .+ work\b",
        r"\bwhy does\b",
        r"\bwhat are the implications\b",
        r"\bwrite a program\b",
    ]
    for pattern in complex_phrases:
        if re.search(pattern, last_user_msg, re.IGNORECASE):
            score += 2

    simple_patterns = [
        r"^(hi|hello|hey|thanks|thank you|ok|yes|no|sure|cool|great)\s*[!.?]*$",
        r"^(what is|who is|when did|where is|define)\s+\w+\s*\??$",
        r"^\w+\s*\?$",
        r"^(1|2|3|4|5|6|7|8|9|10)\s*$",
    ]
    for pattern in simple_patterns:
        if re.match(pattern, last_user_msg.strip(), re.IGNORECASE):
            score -= 3

    total_messages = len(messages)
    if total_messages > 10:
        score += 1
    if total_messages > 20:
        score += 1

    has_system = any(
        (m.role == "system" if hasattr(m, "role") else m.get("role") == "system")
        for m in messages
    )
    if has_system:
        score += 1

    if score >= 5:
        return "powerful"
    elif score >= 2:
        return "balanced"
    else:
        return "fast"


def select_model_for_tier(tier: str, available_providers: list) -> tuple[str, str]:
    tier_config = MODEL_TIERS.get(tier, MODEL_TIERS["balanced"])
    available_names = {p.name for p in available_providers}

    for provider_name, model_id in tier_config["models"]:
        if provider_name in available_names:
            return provider_name, model_id

    return "groq", "llama-3.3-70b-versatile"


def auto_route(req: ChatRequest, available_providers: list) -> tuple[str, str, str]:
    tier = classify_complexity(req.messages)
    provider_name, model_id = select_model_for_tier(tier, available_providers)

    reasoning = f"Complexity={tier} | msgs={len(req.messages)} | chars={sum(len(m.content) for m in req.messages)}"
    return provider_name, model_id, reasoning


def get_tier_info() -> dict:
    return {
        "tiers": {
            name: {
                "description": cfg["description"],
                "cost_per_1k_tokens": cfg["cost_per_1k"],
                "models": [{"provider": p, "model": m} for p, m in cfg["models"]],
            }
            for name, cfg in MODEL_TIERS.items()
        },
        "classification_rules": {
            "fast": "Short factual queries, greetings, simple questions (< 50 chars, < 15 words)",
            "balanced": "General purpose, moderate complexity, multi-turn conversations",
            "powerful": "Complex reasoning, code generation, analysis, creative writing, system design (> 200 chars or contains analytical keywords)",
        },
    }
