# Test report

Run with:
```bash
cd asktheco
pip install -r backend/requirements.txt pytest httpx
pytest tests/ -v
```

## What is tested (`tests/test_basic.py`)

| Test | Type | What it proves |
|---|---|---|
| `test_signup_and_login` | Integration | New workspace + user creation, JWT login works |
| `test_duplicate_signup_rejected` | Unit | Can't register the same email twice |
| `test_wrong_password_rejected` | Unit | Login fails with wrong credentials |
| `test_unauthenticated_request_rejected` | Unit | Protected routes reject requests with no token |
| `test_ingest_and_ask_end_to_end` | Integration | Full pipeline: upload markdown → chunk → embed → ask → cited answer |
| `test_duplicate_document_rejected` | Integration | Exact-duplicate file upload is rejected (409) |
| `test_near_duplicate_document_flagged_for_review` | Integration | A lightly revised policy document is stored as `needs_review` with the matched document ID |
| `test_different_document_not_flagged_as_near_duplicate` | Integration | A genuinely different document remains `ready` and searchable |
| `test_tenant_isolation` | Integration | **Critical**: Workspace B can never retrieve Workspace A's ingested content |
| `test_csv_row_level_citation` | Integration | Spreadsheet rows produce row-level citations, not merged blobs |
| `test_member_cannot_retrieve_hr_only_source` | Integration | A normal workspace member cannot retrieve chunks from a source visible only to `hr` |
| `test_admin_can_retrieve_hr_only_source` | Integration | Workspace admins can retrieve from restricted sources for audit/administration |
| `test_reranking_changes_hybrid_order` | Unit | Cross-encoder re-ranking can reorder the hybrid shortlist before final citations |

All 13 tests pass as of the last run (see CI badge in the README once CI is
wired up on your fork/repo).

## What is NOT yet covered (known gaps)

- OCR path for scanned PDFs (OCR itself is not implemented — see
  `docs/adr/ADR-002-ingestion-and-chunking.md`).
- Load/performance testing at large chunk counts (BM25/dense retrieval is
  currently `O(n)` in chunk count — see `docs/adr/ADR-001-hybrid-retrieval.md`).
- Live Hugging Face model-quality evaluation. The 100-question synthetic eval
  in `docs/eval_report.md` is now implemented, but it disables `HF_API_TOKEN`
  for reproducibility and therefore measures deterministic offline fallback
  behavior rather than hosted model quality.
- Frontend has no automated tests yet (manual QA only). A roadmap item is
  adding Playwright smoke tests for login → ask → citation-render.
