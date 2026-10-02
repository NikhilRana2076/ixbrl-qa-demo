import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The unit tests always run against the stubs (deterministic fact IDs, no network).
# To check the adapter against the REAL dissertation package, run tests/smoke_real.py.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "stubs"))

from webapp import create_app, llm  # noqa: E402
from webapp.config import Settings, hash_code  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "fictional_filing.xhtml"
ORIGIN = {"Origin": "http://localhost"}


class ScriptedClient:
    """Fake provider: search terms and the JSON answer are scripted per test."""
    def __init__(self, terms, answer, cost=0.001, fail=False):
        self.terms, self.answer, self.cost, self.fail = terms, answer, cost, fail
        self.prompts = []

    def generate(self, prompt, temperature=None, max_tokens=300, purpose=""):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("provider down")
        if "RETRIEVED EVIDENCE" not in prompt:        # the search-term extraction call
            return {"text": self.terms if isinstance(self.terms, str) else json.dumps(self.terms),
                    "estimated_cost_usd": self.cost}
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return {"text": text, "estimated_cost_usd": self.cost}


@pytest.fixture
def settings(tmp_path):
    return Settings(env="testing", secret_key="x" * 40, access_codes={hash_code("GOOD-CODE"): 3},
                    public_questions_per_session=10, public_questions_per_ip_per_day=20,
                    samples_dir=str(tmp_path / "samples"), ask_per_minute=100, contact_email="a@b.c",
                    stats_db_path=str(tmp_path / "stats.sqlite"),
                    contact_linkedin="https://www.linkedin.com/in/x")


@pytest.fixture
def app(settings):
    a = create_app(settings)
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def script(monkeypatch):
    holder = {}

    def use(terms, answer, **kw):
        holder["client"] = ScriptedClient(terms, answer, **kw)
        monkeypatch.setattr("webapp.make_client",
                            lambda tier, s: llm.MeteredClient(holder["client"]))
        return holder["client"]
    return use


def upload(client, path=FIXTURE, name=None):
    data = {"file": (io.BytesIO(Path(path).read_bytes()), name or Path(path).name)}
    return client.post("/api/upload", data=data, headers=ORIGIN, content_type="multipart/form-data")


def ask(client, q="What was revenue?", model="public"):
    return client.post("/api/ask", json={"question": q, "model": model}, headers=ORIGIN)
