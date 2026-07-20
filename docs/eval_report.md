# Evaluation report: synthetic 100-question RAG run

Run date: July 20, 2026 IST  
Workspace: `Northstar Synthetic Eval`  
Runner: `scripts/run_eval.py`  
Question set: `docs/eval/eval_questions.json`  
Detailed outputs: `docs/eval/eval_results.json`, `docs/eval/eval_summary.json`

## Technical summary

This is now an actual 100-question run, not just a methodology. The app was
evaluated through the real API path: create workspace, create sources, upload
documents, then call `POST /api/ask` for every question.

Overall keyword accuracy was **66.0%**. Table lookup was strongest (**100.0%**)
and factual lookup was solid (**88.0%**). Multi-hop was weaker (**76.0%**) and
source recall for multi-hop questions was only **63.3%**, usually because the
retriever found the policy chunk but missed the matching roster row. The biggest
failure is no-answer behavior: **0.0%** accuracy on unanswerable questions. The
retriever always returns a best match when a workspace has chunks, so the system
answers with irrelevant high-confidence context instead of abstaining.

Hugging Face was disabled for this deterministic run (`HF_API_TOKEN=""`), so
the app used its offline embedding/generation fallback. Scoring used keyword
matching over answer text plus citation previews; no LLM judge was used. This
means the run is reproducible and retrieval-focused, but it is not a claim about
live HF generation quality.

## Synthetic corpus

The generated corpus is realistic but fictional, with one source per supported
ingestion type:

| Source | File | Shape | Notes |
|---|---|---:|---|
| People Wiki | `docs/eval/corpus/eval_hr_wiki.md` | 10 heading sections | HR policies, remote work, leave, benefits, onboarding |
| Security PDF | `docs/eval/corpus/eval_security_policy.pdf` | 1 rendered PDF page | Access control, data classification, expense, vendor, incident, retention |
| Slack Export | `docs/eval/corpus/eval_slack_threads.json` | 7 threads | People Ops, security review, finance ops, IT, product launch, incidents, benefits |
| HR Roster | `docs/eval/corpus/eval_hr_roster.csv` | 30 employee rows | Department, location, manager, role, access role, training date, laptop refresh |

The PDF was rendered with Poppler and visually checked; text extraction via
`pypdf` also confirms the policy content is readable by the ingestion parser.

## Question set

`docs/eval/eval_questions.json` contains exactly 100 rows with the requested
fields: `question`, `expected_answer_summary`, `tier`, and `expected_source`.

| Tier | Count | Purpose |
|---|---:|---|
| Factual | 25 | Single-source policy and Slack facts |
| Multi-hop | 25 | Employee row plus policy/source combination |
| Table-lookup | 25 | Specific HR roster row lookups |
| Opinion/no-answer | 25 | Unsupported or intentionally sensitive questions |

For multi-hop expected sources, `expected_source` uses a pipe-delimited list,
for example `eval_hr_roster.csv|eval_security_policy.pdf`.

## Scoring method

Answer accuracy used deterministic keyword scoring. For answerable questions,
the scorer extracted non-stopword tokens from `expected_answer_summary` and
checked how many appeared in the answer text plus citation previews. A question
was marked correct at >= 60% keyword coverage, or >= 55% for multi-hop. For
no-answer questions, the scorer expected either no citations or an explicit
"not enough information" style response.

Citation precision was the fraction of returned citations whose source matched
an expected source. Citation recall was the fraction of expected source files
that appeared in the returned citations. For no-answer questions, both metrics
are 1 only when no citations are returned.

This is intentionally simple and auditable. A stronger next version should add
an LLM judge with a fixed rubric, but the current numbers are useful because
they reveal retrieval and abstention failure modes without judge variance.

## Results

| Slice | N | Answer accuracy | Citation precision | Citation recall | Avg confidence |
|---|---:|---:|---:|---:|---:|
| Overall | 100 | 66.0% | 34.5% | 65.8% | 93.9 |
| Factual | 25 | 88.0% | 41.3% | 100.0% | 90.2 |
| Multi-hop | 25 | 76.0% | 53.3% | 63.3% | 94.2 |
| Table-lookup | 25 | 100.0% | 43.3% | 100.0% | 95.3 |
| Opinion/no-answer | 25 | 0.0% | 0.0% | 0.0% | 95.8 |

## Confidence calibration

| Confidence bucket | N | Accuracy | Avg confidence |
|---|---:|---:|---:|
| 80-90 | 22 | 86.4% | 88.2 |
| 90-100 | 78 | 60.3% | 95.5 |

Confidence is not calibrated. The highest-confidence bucket performs worse
because many unanswerable questions receive confident but irrelevant citations.
This comes from the current confidence heuristic: scores are normalized within
each query, so even a bad top match can look strong when there is no absolute
relevance threshold.

## Findings

**Table lookup is the strongest path.** CSV ingestion creates one chunk per row,
so employee-specific questions retrieve clean, citable evidence. All 25 table
questions passed keyword scoring, and recall was 100.0%.

**Factual lookup is usable but noisy.** Factual accuracy reached 88.0%, but
citation precision was only 41.3%. The relevant source was usually present, but
the top 6 citations often included extra Slack threads or neighboring policy
sections. This is a presentation-quality problem: users see the right evidence
mixed with too much irrelevant evidence.

**Multi-hop needs query decomposition.** Multi-hop accuracy was 76.0%, but
source recall was 63.3%. Misses often retrieved the policy source while missing
the employee row, especially for questions combining a person-specific roster
fact with a broad policy. The current retriever treats the whole question as one
query; it does not split "find employee row" from "find matching policy."

**No-answer behavior is the main blocker.** All 25 no-answer questions failed.
Examples such as "What is Northstar's cafeteria menu for next Tuesday?" still
received high-confidence citations from unrelated sources. The generation guard
prompt says to abstain when context is insufficient, but the offline fallback
and retriever do not enforce an abstention threshold.

## Weak spots I would fix with two more weeks

1. Add an absolute relevance gate before generation. Keep the normalized hybrid
   score for ranking, but also track raw BM25, dense, and reranker scores so the
   app can return "I don't have enough information" when every candidate is weak.
2. Improve confidence calibration. Confidence should penalize low absolute
   relevance, citation disagreement, and no-answer uncertainty instead of
   treating each query's top normalized score as inherently strong.
3. Add query decomposition for multi-hop questions. For questions with an
   employee/entity plus a policy ask, run one retrieval pass for the entity row
   and another for the policy concept, then merge citations.
4. Tighten citation presentation. Return fewer citations by default, deduplicate
   by source/locator, and apply a minimum citation relevance threshold so users
   do not see correct evidence surrounded by noise.
5. Re-run with live Hugging Face generation and an LLM judge. This deterministic
   run is good for regression testing, but a final production-quality eval
   should separately score generated answer quality with the real model path.

## How to reproduce

```bash
cd asktheco
pip install -r backend/requirements.txt pytest httpx
python scripts/run_eval.py
```

The script resets `docs/eval/eval_run.db`, regenerates the synthetic corpus and
question set, ingests all sources into a fresh eval workspace, calls `/api/ask`
100 times, and writes:

- `docs/eval/eval_questions.json`
- `docs/eval/eval_results.json`
- `docs/eval/eval_summary.json`
