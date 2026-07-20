# ADR-001: Hybrid retrieval (BM25 + dense) over pure vector search

## Context
Off-the-shelf RAG tutorials use pure dense (embedding) retrieval. That fails
on enterprise data in specific, predictable ways: exact identifiers (ticket
numbers, product SKUs, error codes, person names) often sit far apart in
embedding space even though a keyword match would find them instantly.
Pure BM25, conversely, misses paraphrases and synonyms. This project's
target users (anyone asking "what's our policy on X") need both.

## Decision
Retrieve with both BM25 (`rank_bm25`, in-process, no external service) and
dense cosine similarity over embeddings from the Hugging Face Inference API
(`BAAI/bge-small-en-v1.5` by default). Normalize both score distributions
per-query (min-max) and combine with a weighted sum (`BM25_WEIGHT=0.4`,
`DENSE_WEIGHT=0.6` by default, both tunable via `.env` with no code change).

## Consequences
**Positive:**
- Exact-match queries (IDs, names, numbers) are not silently dropped.
- Paraphrased/semantic queries still work via the dense half.
- No extra infrastructure (no Elasticsearch/OpenSearch cluster) — BM25 runs
  in-process over the workspace's chunks, which is fine at this project's
  scale (a single tenant's knowledge base, not web-scale search).

**Negative / trade-offs:**
- BM25 re-tokenizes and re-scores the *entire* workspace corpus on every
  query (`O(n)` in chunk count). Fine up to tens of thousands of chunks;
  would need a real inverted index (Elasticsearch/OpenSearch) or an ANN
  index (Qdrant/pgvector) beyond that. This is a known scaling limit,
  documented in the README roadmap.
- Embeddings are stored as JSON blobs in the relational DB rather than a
  dedicated vector store, so similarity search is a Python-side linear scan.
  Same scaling caveat as above.

## Alternatives considered
- **Pure dense retrieval only**: simpler, but fails on exact-match queries
  (rejected — this is precisely the failure mode the problem statement
  calls out).
- **Elasticsearch/OpenSearch + Qdrant from day one**: the "correct" enterprise
  answer, but adds two stateful services to run and deploy for what is,
  at this stage, a single-tenant-at-a-time knowledge base. Deferred to the
  roadmap once a workspace's corpus outgrows in-process scoring.
- **Cross-encoder re-ranking on top of hybrid retrieval**: valuable (the
  original problem spec calls for `bge-reranker` or similar), but adds
  another model call per query. This was deferred for the alpha build and
  added later as the toggleable second-stage scorer documented in ADR-004.
