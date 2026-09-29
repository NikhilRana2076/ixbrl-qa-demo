"""Offline UI preview: real web layer + stub pipeline + canned model replies. No API keys needed.
    python tests/preview_server.py  -> http://127.0.0.1:5055"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests" / "stubs")]
import webapp
from webapp import llm
from webapp.config import Settings, hash_code


class Canned:
    def generate(self, prompt, temperature=None, max_tokens=0, purpose=""):
        q = prompt.split("Question:")[-1].lower() if not prompt.startswith("TERMS:") else prompt.lower()
        if prompt.startswith("TERMS:"):
            if "overview" in q: t = "SUMMARY"
            elif "recogni" in q: t = json.dumps(["RecognitionOfRevenue"])
            elif "goodwill" in q: t = json.dumps(["Goodwill"])
            elif "profit" in q: t = json.dumps(["ProfitLossBeforeTax"])
            else: t = json.dumps(["Revenue"])
            return {"text": t, "estimated_cost_usd": 0.0002}
        if "change" in q:
            a = {"answer_type": "computation", "fact_ids": ["F1", "F2"], "operation": "difference",
                 "summary": "Revenue increased year on year, comparing the two undimensioned consolidated totals."}
        elif "recogni" in q:
            a = {"answer_type": "narrative", "narrative_id": "N2",
                 "quote": "Revenue from the sale of components is recognised when control passes to the customer, which is on delivery to the customer's site.",
                 "summary": "Component sales are recognised on delivery; service fees are recognised straight-line over the maintenance contract."}
        elif "profit" in q:
            a = {"answer_type": "fact", "fact_ids": ["F4"], "summary": "Profit before tax for the year ended 31 December 2025, from the consolidated total."}
        else:
            a = {"answer_type": "fact", "fact_ids": ["F1"], "summary": "Consolidated revenue (undimensioned total). An underlying revenue figure is also tagged; see other tagged values."}
        return {"text": json.dumps(a), "estimated_cost_usd": 0.001}

webapp.make_client = lambda tier, s: llm.MeteredClient(Canned())
samples = ROOT / "tests" / "fixtures"
app = webapp.create_app(Settings(env="development", secret_key="p" * 40, access_codes={hash_code("DEMO-CODE"): 5},
                                 samples_dir=str(samples), contact_email="nikhilrana2076@gmail.com",
                                 contact_linkedin="https://www.linkedin.com/", ask_per_minute=50))
if __name__ == "__main__":
    app.run(port=5055, debug=False)
