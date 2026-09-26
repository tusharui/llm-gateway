import re

_PATTERNS = (
    re.compile(r"([?&]key=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"([?&]api[-_]?key=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"([?&]access[-_]?token=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(\b[a-z][a-z0-9+.\-]*://[^:/\s\"']+:)[^@\s\"']+(@)"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"\b(sk-)[A-Za-z0-9._\-]{6,}"),
    re.compile(r"\b(AQ\.)[A-Za-z0-9._\-]{6,}"),
    re.compile(r"\b(AIza)[A-Za-z0-9._\-]{6,}"),
)

REDACTED = "***REDACTED***"


def redact(text: str) -> str:
    """Strip provider credentials out of text bound for logs or HTTP responses.

    httpx embeds the full request URL in its exception messages, and Gemini
    authenticates with an `?key=...` query param, so raw provider errors leak
    live API keys to API clients and to log files.
    """
    if not text:
        return text
    for pattern in _PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(2)}", text)
        else:
            text = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    return text


def redact_error(err: BaseException) -> str:
    return redact(str(err)) or type(err).__name__
