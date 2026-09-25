"""
src/generation/openai_client.py

Parallel to src/generation/client.py (LoggedClient for Anthropic), but for
OpenAI's API. Same logging schema and daily spend guard, sharing the same
logs/api_calls.jsonl file so total project spend is tracked in one place
regardless of provider.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    import openai
except ImportError as exc:
    raise SystemExit("pip install openai --break-system-packages") from exc

LOG_PATH = Path("logs/api_calls.jsonl")

# Approximate USD per million tokens -- update to match whichever model
# you select; check current pricing on platform.openai.com before running
# the full benchmark.
PRICE_PER_M_INPUT = 2.00
PRICE_PER_M_OUTPUT = 12.00

DAILY_SPEND_LIMIT_USD = float(os.environ.get("DAILY_SPEND_LIMIT_USD", "5.00"))


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _today_spend() -> float:
    """Sums cost across BOTH providers, since they share one log file --
    the spend guard protects total project spend, not per-provider spend."""
    if not LOG_PATH.exists():
        return 0.0
    today = datetime.now(timezone.utc).date().isoformat()
    total = 0.0
    with LOG_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("timestamp", "").startswith(today):
                total += rec.get("estimated_cost_usd", 0.0)
    return total


def _log_call(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class LoggedOpenAIClient:
    """OpenAI equivalent of LoggedClient. Same interface: .generate(prompt,
    temperature, max_tokens, purpose) -> dict, so runner scripts can swap
    providers with minimal change."""

    def __init__(self, model: str):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit("OPENAI_API_KEY not set in .env")
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model

    def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int = 200,
        purpose: str = "",
    ) -> dict:
        spend_so_far = _today_spend()
        if spend_so_far >= DAILY_SPEND_LIMIT_USD:
            raise RuntimeError(
                f"Daily spend limit reached (${spend_so_far:.2f} >= "
                f"${DAILY_SPEND_LIMIT_USD:.2f}). Raise DAILY_SPEND_LIMIT_USD "
                f"in .env if this is expected."
            )

        kwargs = dict(
            model=self.model,
            max_completion_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if temperature is not None:
            kwargs["temperature"] = temperature

        started = time.monotonic()
        response = self.client.chat.completions.create(**kwargs)
        elapsed = time.monotonic() - started

        text = response.choices[0].message.content or ""
        in_tok = response.usage.prompt_tokens
        out_tok = response.usage.completion_tokens
        cost = (in_tok / 1_000_000 * PRICE_PER_M_INPUT
                + out_tok / 1_000_000 * PRICE_PER_M_OUTPUT)

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "purpose": purpose,
            "provider": "openai",
            "model": self.model,
            "temperature": temperature if temperature is not None else "model_default(1)",
            "prompt_hash": _prompt_hash(prompt),
            "prompt_chars": len(prompt),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "estimated_cost_usd": round(cost, 6),
            "elapsed_seconds": round(elapsed, 2),
            "response_text": text,
        }
        _log_call(record)

        return {
            "text": text,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "estimated_cost_usd": cost,
            "elapsed_seconds": elapsed,
        }