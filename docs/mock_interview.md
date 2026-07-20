# Mock interview Q&A

## 1. What problem does AskTheCompany solve, and why is it not just another RAG demo?

AskTheCompany targets the messy parts of enterprise RAG: mixed source types,
citations that users can verify, tenant isolation, and operational fallback
behavior. The backend is a FastAPI app with JWT auth, SQLAlchemy models, and
workspace-scoped data. The interesting part is that retrieval is not just vector
search. `backend/app/services/retrieval.py` combines BM25 and dense similarity,
then optionally re-ranks a shortlist. Ingestion in
`backend/app/services/ingestion.py` also treats markdown, PDFs, Slack JSON, and
spreadsheets differently so citations point to sections, pages, threads, or
rows instead of anonymous chunks.

## 2. Why did you choose hybrid BM25+dense retrieval?

ADR-001 explains the reasoning: enterprise questions often include exact
identifiers such as employee names, incident severities, policy form IDs, or
support macro names. Dense retrieval can miss those. BM25 catches exact terms,
while embeddings catch paraphrase. The code normalizes BM25 and dense scores per
query and combines them with configurable weights. That gave a simple first
stage that worked without OpenSearch, Qdrant, or pgvector.

## 3. What did cross-encoder re-ranking actually buy you?

ADR-004 frames re-ranking as a precision layer over the hybrid shortlist, not a
replacement for retrieval. In the eval harness, hybrid-only scored 64.0%
answer accuracy, 31.0% citation precision, and 61.8% citation recall. With
re-ranking enabled, the run improved to 66.0% accuracy, 34.5% citation
precision, and 65.8% citation recall. The biggest lift was table lookup:
accuracy moved from 92.0% to 100.0%. The caveat is that this was the
deterministic offline fallback path, not a live benchmark of
`BAAI/bge-reranker-base`.

## 4. How do you enforce tenant isolation?

Tenant isolation is enforced in the database query before scoring. Every tenant
row has a `workspace_id`, and `retrieve()` filters on
`Chunk.workspace_id == workspace_id` before BM25, dense scoring, re-ranking,
generation, or audit logging sees the chunks. ADR-003 documents that choice.
`tests/test_basic.py::test_tenant_isolation` verifies that one workspace cannot
retrieve another workspace's document, even when the question asks for the exact
secret.

## 5. How do document-level ACLs work?

They are source-level role ACLs, documented in ADR-005. `Source.visible_to_roles`
can be `all`, `admin`, or a custom role like `hr`. Admins can retrieve every
source in their workspace; non-admin users can retrieve sources marked `all` or
matching their `User.role`. The ACL filter lives in `retrieve()` next to tenant
filtering, so restricted chunks never reach ranking or generation. The tests
cover both sides: a member cannot retrieve an HR-only source, and an admin can.

## 6. How did you handle duplicate documents?

Exact duplicates still use SHA-256 content hashes and return `409`. For fuzzy
near-duplicates, ADR-006 describes a deterministic SimHash plus token-cosine
score over parsed chunk text. If a new upload is similar enough to an existing
ready document, it is saved as `status="needs_review"` and the `error` field
includes the matched document ID and similarity score. Retrieval already filters
to `Document.status == "ready"`, so reviewed-out documents are not searchable.

## 7. What was the most surprising eval result?

No-answer behavior was the sharpest failure. The 100-question eval in
`docs/eval_report.md` scored 66.0% overall, with 100.0% table-lookup accuracy
and 88.0% factual accuracy, but the opinion/no-answer slice was 0.0%. The
retriever always returns the best available chunks, and confidence is normalized
within a query, so irrelevant matches can look confident. The 90-100 confidence
bucket had only 60.3% accuracy, worse than the 80-90 bucket at 86.4%.

## 8. What would you have done differently with more time?

I would add an absolute relevance gate before generation. Right now ranking is
relative: the top chunk can look strong because it is the best bad match. The
retrieval layer should preserve raw BM25, dense, and re-ranker signals and
return no citations when all candidates are below an answerability threshold.
That would directly address the 0.0% no-answer eval result and improve
confidence calibration.

## 9. What else would you have done differently?

I would add query decomposition for multi-hop questions. Multi-hop accuracy was
76.0% with re-ranking enabled, and source recall was only 63.3%. Questions like
"For Daniel Cho, what department is he in and how long are payroll records
retained?" need two retrieval acts: find Daniel Cho's roster row and find the
payroll retention policy. A single candidate list is not the right shape.

## 10. What breaks at 10x scale?

The retrieval implementation is `O(n)` over a workspace's chunks. BM25 is built
in process, embeddings are JSON blobs in the relational database, and dense
similarity is a Python-side linear scan. That is fine for the project-sized
corpus and is documented in ADR-001 and the README Known Limitations, but at
10x or 100x it becomes the first scaling bottleneck. I would move lexical
search to OpenSearch and dense retrieval to pgvector or Qdrant, then keep the
existing re-ranking layer over a bounded shortlist.
