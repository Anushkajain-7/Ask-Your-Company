# ADR-007: Hugging Face embedding, re-ranking, and chat model choices

## Context
AskTheCompany is meant to be a credible enterprise RAG prototype that a reviewer
can run without GPUs, paid vector databases, or a closed-source LLM dependency.
That makes model choice part of the architecture, not just configuration
decoration. The models need to cover three jobs: embedding chunks for semantic
recall, re-ranking a short hybrid candidate set, and generating a grounded
answer from cited context.

The app also has to survive missing or rate-limited Hugging Face access. The
current `hf_client.py` contract is therefore: call real hosted models when
`HF_API_TOKEN` is configured, but use deterministic local fallbacks for demos,
CI, and evaluation runs. Model defaults should be good enough to explain in an
internship review, while remaining swappable through `.env`.

## Decision
Use Hugging Face Inference API models as the default hosted model layer:

- `BAAI/bge-small-en-v1.5` for dense embeddings (`HF_EMBEDDING_MODEL`).
- `BAAI/bge-reranker-base` for cross-encoder re-ranking
  (`HF_RERANKER_MODEL`).
- `meta-llama/Llama-3.1-8B-Instruct` for grounded answer generation
  (`HF_CHAT_MODEL`).

`bge-small-en-v1.5` is the default embedding model because it is small enough
for free-tier-friendly hosted inference, strong enough for English semantic
search, and a natural fit for chunk-level retrieval where exact-match BM25 is
already covering identifiers and policy terms. A larger embedding model might
improve recall, but the current bottleneck is not only semantic recall; it is
also citation precision and no-answer calibration.

`bge-reranker-base` is paired with the embedding model family and only runs
over the top `RERANK_CANDIDATE_K=20` hybrid candidates. That keeps the
cross-encoder in its intended role: precision over a bounded shortlist, not
brute-force scoring over the whole workspace.

`Llama-3.1-8B-Instruct` is the default chat model because it is open-weight,
instruction-tuned, widely available on Hugging Face, and large enough to follow
a grounded-answer prompt while still being plausible on low-cost hosted
inference. The system prompt explicitly tells it to answer only from retrieved
context.

No latency or dollar-cost numbers are claimed here because this repo has not
run a controlled live Hugging Face benchmark. The synthetic 100-question eval
in `docs/eval_report.md` intentionally disables `HF_API_TOKEN` for
reproducibility, so it measures retrieval/offline-fallback behavior rather than
hosted model latency or generation quality.

## Consequences
**Positive:**
- The project remains runnable on commodity local hardware; model serving is
  delegated to Hugging Face.
- All model choices are `.env` settings, so a deployment can swap providers or
  larger models without changing retrieval or router code.
- The embedding/reranker/generator split makes the RAG pipeline auditable:
  recall, precision, and answer synthesis can be tested separately.
- Open-weight defaults are easier to discuss and replace than proprietary
  black-box defaults.

**Negative / trade-offs:**
- Hosted inference adds network latency, cold starts, and rate-limit risk.
- Free-tier availability is not a production SLA. The app must keep its
  fallback behavior, and production deployments need monitoring.
- `Llama-3.1-8B-Instruct` is not guaranteed to abstain just because the prompt
  says so; retrieval needs an explicit relevance gate before generation.
- The current evaluation does not prove live model quality because it uses
  offline deterministic fallbacks.

## Alternatives considered
- **OpenAI or Anthropic hosted models**: likely stronger generation and tooling,
  but they add a paid/proprietary dependency to a project intentionally designed
  around Hugging Face and open-weight models.
- **Local sentence-transformers and local LLM inference**: removes network
  dependency, but makes setup heavier and risks excluding reviewers without GPU
  resources.
- **Larger BGE or E5 embedding models**: may improve recall, but increase
  latency and hosted inference cost. The current priority is better abstention,
  citation filtering, and multi-hop query decomposition before larger models.
- **No generator, retrieval-only answer snippets**: more deterministic and
  safer, but fails the product goal of a concise assistant answer with cited
  support.
