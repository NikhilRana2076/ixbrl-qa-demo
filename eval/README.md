# Evaluation

The 102-question benchmark from the dissertation (`benchmark.csv`), run against the **live app's** pipeline so that any change to parsing, retrieval, prompts or verification is measured before it ships.

| Mode | What it measures | Cost | When it runs |
|---|---|---|---|
| `retrieval` | Where the gold fact ranks when the store is searched with the gold concept name (recall@15) | Free | Every PR touching `src/`, `webapp/answering.py` or `eval/` |
| `answer` | End-to-end accuracy through `answer_question()`: search terms, retrieval, structured answer, server-side verification | API spend, capped by `--max-usd` | 20-question subset on those PRs; all 102 on demand |

## Setup: add the filings

The 12 benchmark filings are public (Companies House, Open Government Licence v3.0). Put one file per company in `eval/filings/`, named with the company number first:

```
eval/filings/00185647_Sainsbury.xhtml
eval/filings/SC095000_Lloyds.zip
...
```

Run `python eval/run_eval.py retrieval` and it will list any that are missing. Large FTSE 350 filings should be tracked with Git LFS (`git lfs track "eval/filings/*"`) so CI can check them out.

## Run it

```bash
make eval-retrieval                      # free
make eval-ci                             # 20 questions, public model, $0.50 cap
python eval/run_eval.py answer --tier locked --out eval/reports/full_locked.json
```

Each run prints one line per question and writes a JSON report with the outcome, the verified value, the search terms the model chose, and timing.

## Baseline Results

Evaluated against the full 102-question benchmark on all 12 companies (September 2026).

**Retrieval Phase (free):**
- recall@15: **93.14%** (95/102 gold facts ranked in top-15)
- all_inputs_rank_1: **45.1%** (46/102 ranked at position #1)
- gold_not_in_store: 0 (all facts found in database)

**Answer Phase (API, $0.4337 spent):**
- Overall accuracy: **65.69%** (67/102 correct)
  - Extraction (70 questions): 70% accuracy
  - Computation (20 questions): 65% accuracy
  - Disclosure (12 questions): 41.67% accuracy
- Error rate when answered: 6.94% (5 wrong + 3 errors / 72 answered)
- Abstained: 25.49% (26/102) — model preferred honest abstention over hallucination

**Benchmark companies:** 12 real UK firms from Companies House (FTSE 350 and FRS102 private, mix of IFRS and local GAAP).

See `retrieval.json` and `answer.json` in `eval/reports/` for detailed per-question results.

## How answers are scored

| Outcome | Meaning |
|---|---|
| correct | The value the server verified matches the gold answer or an accepted alternative (within 0.5%). For disclosures: the app cites the gold disclosure concept and its quote is found verbatim. |
| wrong | A verified answer that does not match (e.g. the prior-year figure) |
| abstained | "Not in tagged data" |
| unverified | The app could not verify the model's reply |
| error | The pipeline raised |

Scoring uses the value the server read or computed, never the model's free-text summary. Gold facts are located by concept + context (or concept + value for computation inputs), so the fact ids in `benchmark.csv`, which come from the research database, are not needed.

## Relation to the dissertation figures

The dissertation reports C3 (tag-aware retrieval) at **92.2%** against 45.1% for the text baseline (+47.1 pp, 95% CI 37.3 to 56.9), scored by hand. This harness measures the **deployed** variant (LLM-extracted search terms, structured output, automatic scoring), so its numbers are a regression signal for the live site and are not expected to equal the thesis results.

## Setting the CI thresholds

1. Run the full benchmark once from **Actions > Evaluation > Run workflow**.
2. Set repository variables `EVAL_MIN_RECALL` and `EVAL_MIN_ACCURACY` a little below the baseline (e.g. baseline 0.85 → threshold 0.80).
3. From then on, a PR that drops below either threshold fails.

Secrets needed: `OPENAI_API_KEY` (public tier) and/or `ANTHROPIC_API_KEY` (locked tier). Use the capped demo keys described in `DEPLOY.md`, not the research keys.
