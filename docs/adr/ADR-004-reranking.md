# ADR-004: Cross-encoder re-ranking over the hybrid shortlist

## Context
ADR-001 deliberately combines BM25 and dense retrieval because each catches
different enterprise-search failure modes. That first-stage retrieval is good
at finding a broad candidate set, but its score is still a coarse weighted sum:
BM25 sees token overlap, dense retrieval sees embedding similarity, and neither
directly judges whether a specific passage answers the specific question.

The E3 problem statement calls for a re-ranking step such as `bge-reranker`.
That is the right second-stage pattern for this project: keep the existing
workspace-scoped hybrid retrieval as the recall layer, then ask a cross-encoder
to make a more precise final ordering over only the top candidates.

## Decision
After the existing workspace-filtered hybrid scoring step, retrieve up to
`RERANK_CANDIDATE_K=20` candidates instead of immediately truncating to
`TOP_K`. If `ENABLE_RERANKING=true`, score those candidate `(question, chunk)`
pairs with a Hugging Face cross-encoder re-ranker
(`BAAI/bge-reranker-base` by default, configurable via `HF_RERANKER_MODEL`).
Normalize those scores and return the final `TOP_K` ordered by the re-ranker,
using the original hybrid score only as a tie-breaker.

If Hugging Face is unavailable, rate-limited, cold-starting, or no
`HF_API_TOKEN` is configured, retrieval does not fail. It falls back to a
deterministic lexical overlap scorer for the second stage, matching the rest
of the app's offline-demo posture.

The tenant boundary remains unchanged: `retrieval.py::retrieve` still fetches
only chunks where `Chunk.workspace_id == workspace_id` before any scoring or
re-ranking happens.

## Consequences
**Positive:**
- Final citations are ranked by a model that sees the question and candidate
  passage together, which usually improves precision over a weighted
  BM25+dense score.
- The expensive model call is bounded to a small shortlist (`20` candidates by
  default), not the full workspace corpus.
- Re-ranking is operationally reversible with `ENABLE_RERANKING=false` if a
  deployment needs lower latency or fewer external model calls.
- Offline and CI behavior remains deterministic because the fallback never
  calls the network.

**Negative / trade-offs:**
- Every normal query with re-ranking enabled adds another Hugging Face
  inference call after embeddings and before generation. That increases
  latency, can hit free-tier rate limits sooner, and may create extra cost in
  a paid deployment.
- Cross-encoder scoring is only as good as the first-stage shortlist. If the
  relevant chunk is not in the top `RERANK_CANDIDATE_K`, the re-ranker cannot
  recover it.
- The fallback lexical scorer is not a semantic cross-encoder; it preserves
  availability and deterministic tests, but it is not equivalent to the real
  model.

## Alternatives considered
- **Keep hybrid-only retrieval**: simpler and faster, but leaves final ranking
  to a coarse weighted score and misses the problem-spec requirement for a
  re-ranker.
- **Run the cross-encoder over every workspace chunk**: potentially more
  precise, but too slow and expensive for the current in-process architecture.
  It would also duplicate the scaling problem documented in ADR-001.
- **Use an external managed search stack with built-in re-ranking**: attractive
  for production, but it adds paid infrastructure and operational complexity.
  The current design keeps the project free-tier friendly while preserving a
  clean path to swap the first-stage index later.
