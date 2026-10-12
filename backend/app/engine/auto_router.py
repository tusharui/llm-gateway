import re
from app.schemas import ChatRequest
from app.config import settings


MODEL_TIERS = {
    "fast": {
        "description": "Simple, factual, or short queries",
        "models": [
            ("groq", "openai/gpt-oss-20b"),
            ("gemini", "gemini-2.5-flash-lite"),
            ("openrouter", "openai/gpt-4o-mini"),
        ],
        "cost_per_1k": 0.0001,
    },
    "balanced": {
        "description": "Moderate complexity, general purpose",
        "models": [
            ("groq", "qwen/qwen3.8-27b"),
            ("openrouter", "openai/gpt-4o-mini"),
            ("gemini", "gemini-2.5-flash"),
            ("openrouter", "mistralai/mistral-small-3.2-24b-instruct"),
        ],
        "cost_per_1k": 0.0005,
    },
    "powerful": {
        "description": "Complex reasoning, analysis, code, creative writing",
        "models": [
            ("groq", "openai/gpt-oss-120b"),
            ("gemini", "gemini-2.5-flash"),
            ("openrouter", "meta-llama/llama-3.3-70b-instruct"),
        ],
        "cost_per_1k": 0.002,
    },
}


COMPLEX_KEYWORDS = [
    r"\bexplain\b", r"\banalyz\w*\b", r"\bcompar\w*\b", r"\bevaluate\b",
    r"\bcritique\b", r"\breview\b", r"\bdesign\w*\b", r"\barchitect\w*\b",
    r"\bcode\b", r"\bimplement\w*\b", r"\bdebug\w*\b", r"\brefactor\w*\b",
    r"\boptimiz\w*\b", r"\bstep.by.step\b", r"\breason\w*\b", r"\blogic\b",
    r"\bproof\b", r"\bprove\b", r"\bderiv\w*\b", r"\bessay\b", r"\barticle\b",
    r"\bstory\b", r"\bcreativ\w*\b", r"\bnarrativ\w*\b", r"\bpoem\b",
    r"\brecommend\w*\b", r"\bmath\w*\b", r"\bequation\b", r"\bformula\w*\b",
    r"\bcalculat\w*\b", r"\bsolve\b", r"\bstrateg\w*\b", r"\broadmap\b",
    r"\bplans?\b|\bplanning\b|\bplanned\b", r"\btips?\b", r"\badvice\b",
    r"\bimprove\w*\b", r"\bsuggest\w*\b",
    # Real work verbs the list was missing. Failure mode 2 in
    # evals/ERROR_ANALYSIS.md: the 'howto' category scored 0/8 because
    # nothing here matched a request for instructions.
    r"\bsummari[sz]\w*\b", r"\bcondens\w*\b", r"\bhow do i\b",
]

COMPLEX_PHRASES = [
    r"\btrade.?offs?\b",
    r"\bpros and cons\b",
    r"\balternatives\b",
    r"\bsystem design\b",
    r"\bhow does .+ work\b",
    r"\bwhy does\b",
    r"\bwhat are the implications\b",
    r"\bwrite a program\b",
]

SIMPLE_PATTERNS = [
    r"^(hi|hello|hey|thanks|thank you|ok|yes|no|sure|cool|great)\s*[!.?]*$",
    r"^(what is|who is|when did|where is|define)\s+\w+\s*\??$",
    r"^\w+\s*\?$",
    r"^(1|2|3|4|5|6|7|8|9|10)\s*$",
]

# Nouns that name a concrete thing to produce. `poem`, `story`, `essay` and
# `article` are already standalone keywords above and are deliberately absent
# here, so they cannot be counted twice.
ARTIFACT_NOUNS = (
    r"function|script|query|migration|endpoint|hook|component|class|module|"
    r"tests?|program|algorithm|parser|implementation|lambda|regex|api|"
    r"server|plugin|extension|worker|pipeline|schema|boilerplate|"
    r"screenplay|scene"
)

# A request to produce a named artifact is decisive on its own, and is worth
# more than any other single signal here. Evidence, not taste: with this
# family weighted +2 it recovered 1 case, because "write a <artifact>" is a
# ~70-character prompt scoring 1 from length alone and 1 + 2 = 3 is still
# balanced. Weighted +4 it recovers 13 and breaks 0. Weights 5 and above are
# worse: they push "Write the migration." -- genuinely under-specified -- into
# powerful. The gap between +2 and +5 is the whole finding: the old score
# demanded several independent signals, so no single decisive one could reach
# the powerful threshold.
#
# `[^.?!]{0,40}?` lets modifiers sit between the article and the noun ("a
# debounce function", "a Postgres migration", "a semver-aware release
# script") without letting the match run across a sentence boundary.
CONSTRUCTION_PATTERNS = [
    rf"\b(?:write|build|create|draft|generate)\b[^.?!]{{0,40}}?\b(?:{ARTIFACT_NOUNS})\b",
]
CONSTRUCTION_WEIGHT = 4

# Score at or above which a prompt goes to the most capable tier.
POWERFUL_THRESHOLD = 5
BALANCED_THRESHOLD = 2


def _last_user_message(messages: list) -> str:
    for m in reversed(messages):
        content = m.content if hasattr(m, "content") else (m.get("content", "") if isinstance(m, dict) else "")
        role = m.role if hasattr(m, "role") else (m.get("role", "") if isinstance(m, dict) else "")
        if role == "user":
            return content
    return ""


def _tier_for_score(score: int) -> str:
    if score >= POWERFUL_THRESHOLD:
        return "powerful"
    elif score >= BALANCED_THRESHOLD:
        return "balanced"
    return "fast"


def complexity_score(messages: list) -> int:
    """The number `classify_complexity` thresholds, exposed on its own.

    Extracted so the score can be inspected and tested without going through
    the tier, and so there is exactly one copy of the rule: a duplicate in a
    test or an analysis script would drift from the one the gateway runs, and
    the whole value is that this *is* the production number.
    """
    if not messages:
        return 0

    last_user_msg = _last_user_message(messages)
    if not last_user_msg:
        return 0

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

    for pattern in COMPLEX_KEYWORDS:
        if re.search(pattern, last_user_msg, re.IGNORECASE):
            score += 2

    for pattern in COMPLEX_PHRASES:
        if re.search(pattern, last_user_msg, re.IGNORECASE):
            score += 2

    for pattern in CONSTRUCTION_PATTERNS:
        if re.search(pattern, last_user_msg, re.IGNORECASE):
            score += CONSTRUCTION_WEIGHT

    for pattern in SIMPLE_PATTERNS:
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

    return score


def classify_complexity(messages: list) -> str:
    if not messages:
        return "fast"

    if not _last_user_message(messages):
        return "fast"

    return _tier_for_score(complexity_score(messages))


def select_model_for_tier(tier: str, available_providers: list) -> tuple[str, str]:
    tier_config = MODEL_TIERS.get(tier, MODEL_TIERS["balanced"])
    available_names = {p.name for p in available_providers}

    for provider_name, model_id in tier_config["models"]:
        if provider_name in available_names:
            return provider_name, model_id

    return "groq", "openai/gpt-oss-120b"


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


def tier_for_model(model: str) -> str | None:
    for name, cfg in MODEL_TIERS.items():
        if any(m == model for _, m in cfg["models"]):
            return name
    return None


def alternate_model(
    model: str, exclude_provider: str, target_provider: str | None = None
) -> str | None:
    """Same-tier model that `target_provider` actually hosts.

    Model ids are provider-specific, so failing over to another provider with
    the same id always 404s. Pick an equivalent model from the same tier
    instead.

    ``target_provider`` is what makes this correct. Without it the function
    returned the first tier entry that was not on ``exclude_provider``, and
    the caller filtered afterwards on whether the provider it happened to be
    looking at hosted that id. The consequence was silent: failover_chain
    would ask Gemini for ``openai/gpt-oss-120b`` and burn an upstream
    round-trip on a guaranteed 404 before reaching a provider that could have
    served the request. Verified against the shipped config:

        alternate_model('openai/gpt-oss-120b', 'gemini')  -> 'openai/gpt-oss-120b'
        # Gemini hosts no powerful-tier id at all, so the old signature
        # returned a Groq model and the chain opened with a 404.

    Passing ``target_provider`` makes "can this provider serve this model"
    part of the search rather than a filter applied afterwards. With no
    ``target_provider`` the old behaviour is preserved for callers that only
    want some other model.
    """
    tier = tier_for_model(model)
    if tier is None:
        return None

    cfg = settings.get_providers()
    # An unknown target provider must yield None, not fall through to "return
    # whatever comes first". Silently degrading to the old behaviour is how an
    # unconfigured or misspelled provider name reintroduces the 404 this
    # function exists to prevent.
    target_cfg = cfg.get(target_provider) if target_provider else None
    if target_provider and target_cfg is None:
        return None

    for provider_name, model_id in MODEL_TIERS[tier]["models"]:
        if provider_name == exclude_provider:
            continue
        if target_cfg is None:
            return model_id
        if model_id in target_cfg.models:
            return model_id
    return None
