class LoggedOpenAIClient:
    """Stub: tests monkeypatch webapp.llm.make_client instead of calling this."""
    def __init__(self, model): self.model = model
    def generate(self, prompt, temperature=None, max_tokens=300, purpose=""):
        raise RuntimeError("stub client: no network in tests")
