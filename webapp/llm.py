"""
Thin wrapper around the dissertation's own provider clients
(src.generation.client.LoggedClient / src.generation.openai_client.LoggedOpenAIClient),
so every demo call still goes through the same JSONL call log and daily
spend guard used in the study.

The wrapper adds two things:
  * it records the estimated cost of every call made during one question
    (including the search-term extraction call made inside
    extract_search_terms), so the global daily USD cap can be enforced;
  * provider-specific settings live in one place (GPT-5.6 Terra rejects
    temperature != default and needs a larger completion budget because it
    reasons before answering -- see decision log 2026-09-02).
"""
from __future__ import annotations

PROVIDERS = {
    "public": {"provider": "openai", "temperature": None, "max_tokens": 2000},
    "locked": {"provider": "anthropic", "temperature": 0.0, "max_tokens": 500},
}


class MeteredClient:
    def __init__(self, inner):
        self._inner = inner
        self.spent_usd = 0.0
        self.calls = 0

    def generate(self, *args, **kwargs):
        result = self._inner.generate(*args, **kwargs)
        self.calls += 1
        try:
            self.spent_usd += float(result.get("estimated_cost_usd") or 0)
        except (AttributeError, TypeError, ValueError):
            pass
        return result

    def __getattr__(self, name):          # anything else -> underlying client
        return getattr(self._inner, name)


def make_client(tier: str, settings) -> MeteredClient:
    if tier == "locked":
        from src.generation.client import LoggedClient
        return MeteredClient(LoggedClient(model=settings.locked_model))
    from src.generation.openai_client import LoggedOpenAIClient
    return MeteredClient(LoggedOpenAIClient(model=settings.public_model))
