from typing import Dict, Optional
from app.providers.interface import AIProvider
from app.providers.groq import GroqProvider
from app.providers.gemini import GeminiProvider
from app.providers.openrouter import OpenRouterProvider
from app.config import settings

_providers: Dict[str, AIProvider] = {}


def initialize_providers():
    global _providers
    _providers = {
        "groq": GroqProvider(),
        "gemini": GeminiProvider(),
        "openrouter": OpenRouterProvider(),
    }


def get_provider(name: str) -> Optional[AIProvider]:
    return _providers.get(name)


def get_all_providers() -> list[AIProvider]:
    return list(_providers.values())


def get_available_providers() -> list[AIProvider]:
    cfg = settings.get_providers()
    return [
        p for p in _providers.values()
        if p.name in cfg and cfg[p.name].api_key
    ]
