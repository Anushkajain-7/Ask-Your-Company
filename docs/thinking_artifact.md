# Why Hybrid Retrieval Is Not Enough, And What Re-ranking Actually Buys You

AskTheCompany started with a familiar RAG shape: parse internal documents into
chunks, embed them, retrieve the most relevant chunks, and ask a language model
to answer with citations. That is the version of RAG that fits on a whiteboard.
It is also the version that quietly fails in product settings.

The most non-obvious decision in this project was not "use vectors" or "add
BM25." It was deciding that hybrid retrieval is still only a recall layer, not
the final relevance layer. The extra re-ranking step looks small in the code:
take the top 20 hybrid candidates, score each `(question, chunk)` pair with a
cross-encoder re-ranker, then return the final `TOP_K`. But architecturally it
changes what retrieval is responsible for. Hybrid search says "find plausible
evidence." Re-ranking says "put the evidence that actually answers this question
first."

That distinction mattered in the final evaluation. On the synthetic
100-question run, with re-ranking enabled, the system reached 66.0% keyword
accuracy, 34.5% citation precision, and 65.8% citation recall. When I reran the
same harness with re-ranking disabled, the system landed at 64.0% accuracy,
31.0% citation precision, and 61.8% citation recall. That is not a miraculous
jump. It is a modest, real gain. More importantly, the tier breakdown shows
where re-ranking helps and where it absolutely does not.

| Slice | Hybrid only accuracy | Re-ranking enabled accuracy | What changed |
|---|---:|---:|---|
| Overall | 64.0% | 66.0% | Small improvement |
| Factual | 84.0% | 88.0% | Better ordering for direct facts |
| Multi-hop | 80.0% | 76.0% | Slightly worse |
| Table lookup | 92.0% | 100.0% | Strong improvement |
| Opinion/no-answer | 0.0% | 0.0% | No improvement |

That table is the honest version of the decision. Re-ranking is useful. It is
not magic. It is not a substitute for query planning, abstention, or confidence
calibration.

## The problem

Enterprise retrieval has two different failure modes that often get collapsed
into one word: relevance.

The first failure mode is recall. The system has to find the relevant evidence
somewhere in the candidate set. Pure vector search is bad at exact identifiers:
ticket IDs, employee names, SKU codes, policy form names, incident severity
labels. BM25 is good at those exact tokens. Pure BM25 is bad at paraphrase:
"Can I work from another country?" should find "international remote work
requires Legal and Payroll approval." Dense embeddings are good at that. This
is why ADR-001 chose hybrid BM25 plus dense retrieval.

The second failure mode is ordering. Once I have plausible chunks, which ones
should be shown to the answer model, and in what order? Hybrid scoring is a
weighted sum of two signals that were never designed to be directly comparable.
BM25 rewards token overlap. Dense cosine rewards embedding closeness. The code
normalizes both per query and combines them with `BM25_WEIGHT=0.4` and
`DENSE_WEIGHT=0.6`. That is a reasonable first-stage ranking strategy. It is
not the same as asking, "Does this passage answer this question?"

The distinction shows up in citation behavior. In the re-ranked eval run,
factual accuracy was 88.0% and factual citation recall was 100.0%, but factual
citation precision was only 41.3%. In plain English: the right source was
usually present, but it was surrounded by distracting citations. The app often
found the answer somewhere in the top 6, while still asking the user to sift
through too much extra context.

That is the kind of problem a product engineer should care about. A citation
interface is not just a debug panel. It is part of the trust contract. If the
right answer is cited beside five loosely related chunks, the user learns that
the system is noisy even when it is technically correct.

## The options considered

The first option was pure dense retrieval. I rejected it early because it fails
on the exact-match cases that internal company data is full of. If someone asks
about `HR-PL-2026`, `NIMBUS-GA-2026`, or a specific employee name, token overlap
is not a nice-to-have. It is the shortest path to the evidence.

The second option was pure BM25. That would have kept the system simple and
fully local, but it would miss paraphrases and policy concepts. A user should
not have to know the exact phrase "international remote work" to ask whether
working from another country is allowed.

The third option was to jump straight to a production search stack:
OpenSearch, Qdrant, pgvector, maybe a hosted retrieval service. That is the
direction a real enterprise deployment would eventually go, but it was too much
infrastructure for this stage. The project is an internship submission meant to
be runnable on free-tier-friendly infra. Adding two stateful services before the
core product was measured would have made the architecture look mature while
making the demo more fragile.

The fourth option was hybrid retrieval only. This was the alpha choice, and it
was a good one. Hybrid search gave the project robust first-stage recall without
extra services. It also created an obvious next question: if hybrid gives us a
candidate set, how do we pick the final citations?

That led to the fifth option: cross-encoder re-ranking over the hybrid
shortlist. This is the decision captured in ADR-004. The retriever still does
workspace-scoped hybrid scoring first. Instead of truncating immediately to
`TOP_K`, it keeps about 20 candidates (`RERANK_CANDIDATE_K=20`) and scores each
question/chunk pair with `BAAI/bge-reranker-base` through the Hugging Face
Inference API. If Hugging Face is unavailable, the app uses a deterministic
lexical fallback, so demos and tests still run.

The key constraint is that re-ranking is second-stage only. It never sees the
whole workspace. It only sees chunks that have already passed tenant filtering,
document status filtering, source ACL filtering, and first-stage retrieval.

## The choice made

The implementation preserves a single retrieval boundary:
`backend/app/services/retrieval.py::retrieve`. That function joins `Chunk`,
`Document`, and `Source`, filters to the authenticated workspace, filters to
ready documents, applies source visibility ACLs, scores BM25 and dense
similarity, then optionally re-ranks the top candidates.

Keeping re-ranking inside that function matters. It means the permission model
does not get scattered across routers or answer generation. A restricted HR
source cannot accidentally reach the re-ranker or the language model because it
never enters the candidate rows. The re-ranker is not an authorization layer. It
is a precision layer.

The code also makes re-ranking operationally reversible. `ENABLE_RERANKING` can
be set to false if latency or hosted inference limits become a problem. That
toggle is not just a convenience flag. It lets the system degrade to the
previous hybrid-only behavior without rewriting the retrieval stack.

The final evaluation used `HF_API_TOKEN=""` for reproducibility, so the run did
not measure the live Hugging Face cross-encoder. It measured the same second
stage using the offline lexical fallback. That is an important caveat. I would
not claim the eval proves `bge-reranker-base` specifically improved production
answer quality. What it does prove is that a second-stage ordering pass changes
retrieval behavior in the expected direction on this corpus: overall accuracy
rose from 64.0% to 66.0%, citation precision from 31.0% to 34.5%, and citation
recall from 61.8% to 65.8%.

Those gains were concentrated where the query could be answered by one strong
piece of evidence. Factual questions improved from 84.0% to 88.0%. Table lookup
improved from 92.0% to 100.0%. That makes sense: row-level CSV chunks are
compact, and a re-ranker can reward the row that contains the exact employee
and requested field.

Multi-hop moved the other way, from 80.0% down to 76.0%. That also makes sense.
A re-ranker evaluates individual chunks. It does not know that the final answer
requires one roster row plus one policy chunk. If the question mentions both a
person and a policy concept, a single-chunk relevance scorer may over-prefer
the policy chunk and under-retrieve the row, or vice versa. Re-ranking can make
the best single chunk better. It cannot plan a multi-evidence answer by itself.

## The trade-offs

The obvious trade-off is latency. With hosted inference enabled, re-ranking adds
another model call between embeddings and answer generation. Bounded candidate
sets keep that manageable, but there is no free lunch. Every query now has more
network surface area, more rate-limit exposure, and more cold-start risk.

The less obvious trade-off is confidence. The evaluation exposed a serious
calibration issue. In the re-ranked run, the 80-90 confidence bucket had 86.4%
accuracy, while the 90-100 bucket had only 60.3% accuracy. The model looked more
confident when it was often more wrong. Why? Because the current confidence
heuristic is based on normalized within-query scores. If every candidate is bad,
the best bad candidate can still normalize to a high score.

This is where re-ranking can make a product worse if you treat it as a truth
detector. Re-ranking improves relative ordering. It does not create an absolute
"is this answerable?" signal unless you preserve and calibrate raw relevance
scores. The eval's no-answer tier makes that painfully clear: both hybrid-only
and re-ranking-enabled runs scored 0.0% on no-answer questions. The system
returned irrelevant citations for questions like "What is Northstar's cafeteria
menu for next Tuesday?" because something in the corpus always looked closest.

That failure has nothing to do with whether BM25, dense retrieval, or re-ranking
is better. It is a missing abstention layer. If the product promise is "answers
your team can verify," then saying "I do not have enough information" is not a
fallback behavior. It is a core feature.

There is also a citation UX trade-off. Returning six citations makes sense for
debugging recall, but it can be too much for user trust. In the re-ranked run,
table lookup had 100.0% answer accuracy and 100.0% citation recall, but only
43.3% citation precision. The answer was right and the right row was present,
yet the user still saw extra evidence. That is not an algorithmic disaster, but
it is a product problem.

## What I would do with more time

First, I would add an absolute relevance gate before generation. The retrieval
layer should preserve raw BM25, dense, and re-ranker scores, not only normalized
per-query values. If the top candidate is weak in absolute terms, `/api/ask`
should return no citations and a clear "I do not have enough information"
answer. That one change would target the eval's largest failure mode.

Second, I would split multi-hop questions into subqueries. A question like
"For Daniel Cho, what department is he in and how long are payroll records
retained?" asks for two retrieval acts: find Daniel Cho's roster row, and find
the payroll retention policy. A single re-ranked candidate list is the wrong
shape. I would add lightweight query decomposition for entity-plus-policy
questions, merge the evidence sets, and make citation recall a first-class
objective.

Third, I would make citation selection stricter than context selection. It is
reasonable to send several chunks to the answer model. It is not always
reasonable to show all of them as citations. The UI should cite the smallest
set of high-confidence chunks that support the answer, deduplicated by
source/locator and filtered by relevance threshold.

Fourth, I would run a live Hugging Face evaluation with `bge-reranker-base` and
`Llama-3.1-8B-Instruct`, then compare it against the deterministic offline run.
The current numbers are useful for regression testing, but they are not the
last word on model quality. The honest claim today is narrower: hybrid retrieval
was necessary for recall, re-ranking modestly improved final ordering, and
neither solved abstention or multi-hop planning.

That is the lesson I would take into a product codebase. RAG quality is not one
knob. It is a stack of contracts: tenant filtering, source ACLs, chunking,
candidate recall, re-ranking, citation selection, confidence calibration, and
generation. Hybrid retrieval is one good contract in that stack. Re-ranking is
another. The system only starts to feel trustworthy when each layer is honest
about what it can and cannot prove.
