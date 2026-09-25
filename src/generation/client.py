"""
src/generation/client.py

Thin wrapper around the Anthropic API with mandatory call logging and a
daily spend guard, per the reproducibility and cost-control commitments
in the project proposal.

Every call is logged to logs/api_calls.jsonl with: timestamp, model,
temperature, prompt hash, input/output tokens, and an estimated cost. The
log is append-only and is the audit trail for "which model, when, at what
temperature" that the Methods chapter commits to.
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
    import anthropic
except ImportError as exc:
    raise SystemExit(
        "pip install anthropic --break-system-packages"
    ) from exc

LOG_PATH = Path("logs/api_calls.jsonl")

# Approximate USD per million tokens (Sonnet-class pricing; update if the
# actual model/pricing differs — this is a cost estimate for the spend
# guard, not a billing-accurate figure).
PRICE_PER_M_INPUT = 3.00
PRICE_PER_M_OUTPUT = 15.00

# Daily spend guard, per proposal's stated risk mitigation.
DAILY_SPEND_LIMIT_USD = float(os.environ.get("DAILY_SPEND_LIMIT_USD", "5.00"))


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _today_spend() -> float:
    """Sum estimated cost of all calls logged today, for the spend guard."""
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


class LoggedClient:
    """Anthropic client wrapper that logs every call and enforces a daily
    spend limit. Use this everywhere in the pipeline rather than the raw
    SDK client, so no call is ever unlogged."""

    def __init__(self, model: str = "claude-sonnet-4-6"):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY not set in .env")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def generate(
        self,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        purpose: str = "",
    ) -> dict:
        """Send one message, log it, return a structured result.

        purpose: free-text tag (e.g. 'C1_baseline', 'C2_consistency_sample')
        so the log can be filtered by which experimental condition a call
        belongs to.
        """
        spend_so_far = _today_spend()
        if spend_so_far >= DAILY_SPEND_LIMIT_USD:
            raise RuntimeError(
                f"Daily spend limit reached (${spend_so_far:.2f} >= "
                f"${DAILY_SPEND_LIMIT_USD:.2f}). Raise DAILY_SPEND_LIMIT_USD "
                f"in .env if this is expected."
            )

        started = time.monotonic()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed = time.monotonic() - started

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        in_tok = response.usage.input_tokens
        out_tok = response.usage.output_tokens
        cost = (in_tok / 1_000_000 * PRICE_PER_M_INPUT
                + out_tok / 1_000_000 * PRICE_PER_M_OUTPUT)

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "purpose": purpose,
            "model": self.model,
            "temperature": temperature,
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