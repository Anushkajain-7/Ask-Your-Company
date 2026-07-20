# AskTheCompany

**Enterprise RAG your team can actually verify** — ask a question, get a
grounded answer with inline citations, confidence scoring, role-gated source
visibility, and full audit logging. Multi-tenant: any company signs up and
gets its own isolated knowledge base.

- **Problem statement:** E3 — RAG over Enterprise Mess (Segment 5: LLM
  Systems & Applied GenAI), extended to self-serve multi-tenancy
- **Segment:** LLM Systems & Applied GenAI
- **Target roles:** LLM Engineer, GenAI Engineer, AI Product Engineer

---

## Demo

- **Local URL:** http://127.0.0.1:8000/login.html
- **Demo workspace:** `Demo Company`
- **Demo email:** `admin@demo.com`
- **Demo password:** `supersecret1`
- **Live deployed:** (https://my-project-4-indol.vercel.app/index.html)

The prepared demo workspace contains 5 sources, 7 uploaded documents, 2
near-duplicate revision uploads marked `needs_review`, and at least 20 demo
questions visible in the Evaluations tab.

To recreate the demo data in a fresh local SQLite database, start the server
and run:

```bash
python scripts/seed_demo.py
```

Then log in with the credentials above, open **Sources** to inspect the
uploaded corpus, and open **Evaluations** to see the 20-question demo query
history.

---

## Problem statement

Enterprises accumulate years of internal knowledge across wikis, PDFs
(some scanned), Slack threads, and spreadsheets — and off-the-shelf RAG
fails on it because generic chunking cuts across tables and threads,
citations are vague ("source 3"), and there's no tenant isolation if more
than one team or company needs to use the same tool. **AskTheCompany**
solves this with per-source-type structural chunking, hybrid (BM25 +
dense) retrieval, real citations down to the page/section/row, a
confidence score per answer, workspace-level tenant isolation, and
source-level role ACLs so companies can separate broad knowledge from
restricted sources.

---

## What is implemented

- Email/password auth with JWT sessions and one isolated workspace per company
- Workspace-scoped retrieval, query history, source listing, and audit log
- Source visibility labels: `all`, `admin`, or a custom role such as `hr`
- Structure-aware ingestion for markdown/text, PDF, Slack-style JSON, CSV, and XLSX
- Exact duplicate rejection plus fuzzy near-duplicate detection for revisions
- Hybrid BM25 + dense retrieval with optional cross-encoder re-ranking
- Hugging Face-backed embeddings/generation with deterministic local fallbacks
- Cited answers, confidence scoring, source coverage UI, and an Evaluations tab
- 100-question synthetic evaluation report plus demo seeding corpus

---

## Architecture

![Architecture diagram](docs/architecture.svg)

See `docs/architecture.svg` for the full container diagram, and
`docs/adr/` for the reasoning behind each major decision.

---

## Tech stack

| Component | Choice | Why |
|---|---|---|
| API framework | FastAPI | Async, typed, auto-generates OpenAPI docs at `/docs` |
| Auth | JWT (python-jose) + bcrypt | Stateless, standard, no session store needed |
| Permissions | Workspace isolation + source role ACLs | Every retrieval query filters by tenant and source visibility before scoring |
| Database | SQLite (dev) / Postgres (prod-ready via `DATABASE_URL`) | Zero-config locally, drop-in swap for production |
| Lexical retrieval | `rank_bm25` | No external search cluster needed at this scale |
| Dense retrieval | Hugging Face Inference API (`BAAI/bge-small-en-v1.5`) | Free-tier friendly, swappable via `.env`, no local GPU needed |
| Re-ranking | Hugging Face cross-encoder (`BAAI/bge-reranker-base`) | Improves final ordering over the hybrid shortlist; toggleable with `ENABLE_RERANKING=false` |
| Generation | Hugging Face router (`meta-llama/Llama-3.1-8B-Instruct`) | Open-weight, cheap, swappable via `.env`; resilient extractive fallback if hosted generation is unavailable |
| Frontend | Vanilla HTML/CSS/JS | Zero build step, deploys as static files alongside the API |
| Tests | pytest + FastAPI TestClient | Fast, no external services needed to run CI |

---

## Quickstart

### Prerequisites
- Python 3.11+
- A free Hugging Face account + API token: https://huggingface.co/settings/tokens
  (the app also runs without one, in a reduced-quality offline fallback mode —
  see "Known limitations")

### Install
```bash
git clone <your-repo-url>
cd asktheco
cp backend/.env.example backend/.env
# edit backend/.env and paste your own HF_API_TOKEN and a random JWT_SECRET
pip install -r backend/requirements.txt
```

### Run
```bash
cd backend
uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000/login.html — sign up, create a source, upload a
document, ask a question.

To load the prepared demo workspace instead of clicking through setup:

```bash
python scripts/seed_demo.py
```

Then log in as `admin@demo.com` with password `supersecret1`.

**Or with Docker:**
```bash
docker compose up --build
```

### Deploy on Vercel

This repo includes `vercel.json`, `api/index.py`, root `requirements.txt`, and
`.python-version` so Vercel can discover the FastAPI app. In Vercel:

1. Import the GitHub repository.
2. Keep **Root Directory** as the repository root.
3. Keep **Framework Preset** as `Other` if Vercel asks.
4. Add environment variables:
   - `HF_API_TOKEN`: your Hugging Face token for live embeddings/generation.
   - `JWT_SECRET`: a long random string.
   - `DATABASE_URL`: recommended for persistence. Use a free Postgres provider
     such as Neon or Supabase. Without this, Vercel uses temporary SQLite under
     `/tmp`, which can reset between serverless cold starts.
   - Optional: `ENABLE_RERANKING=true`, `TOP_K=6`, `ENABLE_DEMO_SEED=true`.
5. Deploy. On Vercel, `ENABLE_DEMO_SEED` defaults to true, so a fresh database
   automatically gets the review workspace:

```txt
Email: admin@demo.com
Password: supersecret1
```

The automatic seed creates 5 sources, 7 documents, and 20 evaluation-log
questions. To seed or repair a non-Vercel deployment manually:

```bash
ASKTHECOMPANY_BASE_URL=https://your-vercel-domain.vercel.app python scripts/seed_demo.py
```

The Vercel deployment URL should open the login page at `/login.html` or `/`.

### Test
```bash
pip install pytest httpx
pytest tests/ -v
```
See `docs/test_report.md` for what's covered.

---

## Data

The production posture is still bring-your-own-data: each workspace uploads
its own files and retrieval is scoped to that workspace. For demo and review,
the repo includes synthetic, non-sensitive data in `docs/demo/corpus/`:

- People Ops handbook markdown, including a v2 revision flagged for review
- Security policy PDFs, including a v2 revision flagged for review
- Engineering production runbook markdown
- Slack-style JSON threads
- Vendor spend CSV

See `docs/data.md` for supported file types, the Slack-JSON schema, visibility
rules, and the duplicate-review behavior.

---

## Evaluation results

The full synthetic evaluation was run through `scripts/run_eval.py` with 100
questions across factual, multi-hop, table-lookup, and no-answer tiers. Results
are stored in `docs/eval/eval_results.json` and summarized in
`docs/eval_report.md`.

| Metric | Result |
|---|---:|
| Answer accuracy | 66.0% |
| Citation precision | 34.5% |
| Citation recall | 65.8% |
| Factual accuracy | 88.0% |
| Multi-hop accuracy | 76.0% |
| Table lookup accuracy | 100.0% |
| No-answer accuracy | 0.0% |

The weakest area is no-answer handling: the retriever currently returns a best
match whenever a workspace has chunks, so out-of-scope questions can still get
overconfident citations. The next fix is a relevance gate before generation.

---

## Security note on API keys

Never commit `backend/.env` or paste a real API token into a chat, issue,
or commit. If a token is ever exposed, rotate it immediately at
https://huggingface.co/settings/tokens. This repo's `.gitignore` excludes
`.env` for this reason.

`HF_API_TOKEN` is used for embeddings, re-ranking, and generation when
available. Generation first calls the current Hugging Face router endpoint and
falls back to a concise extractive answer with citations if the hosted model is
unavailable. The app should not expose raw provider errors to end users.

---

## ADRs

See `docs/adr/`:
- [ADR-001: Hybrid retrieval](docs/adr/ADR-001-hybrid-retrieval.md)
- [ADR-002: Ingestion and chunking](docs/adr/ADR-002-ingestion-and-chunking.md)
- [ADR-003: Permissions model](docs/adr/ADR-003-permissions-model.md)
- [ADR-004: Cross-encoder re-ranking](docs/adr/ADR-004-reranking.md)
- [ADR-005: Document-level ACLs](docs/adr/ADR-005-document-level-acls.md)
- [ADR-006: Fuzzy deduplication](docs/adr/ADR-006-fuzzy-deduplication.md)
- [ADR-007: Embedding and chat model choice](docs/adr/ADR-007-embedding-and-chat-model-choice.md)

---

## Known limitations

- **No OCR yet.** Scanned PDF pages are flagged, not read. (See ADR-002.)
- **Retrieval is `O(n)` in chunk count** — fine for one company's knowledge
  base, not for web-scale search. (See ADR-001.)
- **ACL granularity is source-level.** A source can be visible to all roles,
  admins, or one custom role such as `hr`; individual documents inside the
  same source inherit that source's visibility. (See ADR-005.)
- **Near-duplicate review is automatic, but resolution is manual.** A likely
  revision is marked `needs_review` and excluded from retrieval until a human
  decides whether to keep it or supersede the prior document. (See ADR-006.)
- **The offline fallback mode** (no `HF_API_TOKEN` set) uses a
  non-semantic hashing embedding, lexical re-ranking fallback, and a
  concise extractive answer, so the app never crashes without a key, but answer
  quality is materially lower than with a real HF token configured.
- **No-answer detection is weak.** The 100-question eval scored 0.0% on the
  no-answer tier because the current system retrieves the closest available
  passage instead of abstaining when relevance is too low.

## Roadmap (next 2 weeks)

1. OCR for scanned PDFs (Tesseract or PaddleOCR) feeding the existing
   page-aware chunker.
2. Add a relevance/abstention gate for no-answer questions before generation.
3. Finer-grained per-document ACLs and role management UI.
4. Review workflow actions for near-duplicates (keep both / supersede old).
5. Move BM25 + dense scoring to a real index (OpenSearch + Qdrant/pgvector)
   once a workspace's corpus exceeds ~5,000 chunks.
6. Re-run the 100-question eval with live Hugging Face generation and an LLM
   judge in addition to deterministic keyword scoring.
7. "Invite a teammate" flow (multiple users per workspace, not just the
   founding admin).

---

## License & acknowledgements

MIT — see `LICENSE`. Built during a 5-week individual internship sprint
(Segment 5: LLM Systems & Applied GenAI). Retrieval and generation are
powered by the Hugging Face Inference API and open-weight models from the
Hugging Face Hub.
