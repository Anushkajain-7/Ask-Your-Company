# ADR-006: Fuzzy near-duplicate detection with human review

## Context
The alpha build rejected only exact duplicate files using a SHA-256 content
hash. That catches accidental re-uploads of the same bytes, but it misses the
more realistic enterprise case: revision A and revision B of the same policy
document, where one sentence or paragraph changed.

Hard-rejecting these near-duplicates would be too aggressive because the newer
revision may be the one the company wants to keep. Silently ingesting both is
also risky: retrieval could cite stale and current versions side by side,
lowering trust in the answer.

## Decision
Keep exact duplicate behavior unchanged: identical file bytes are rejected with
`409`.

For non-identical uploads, parse the file into the same structure-aware chunks
used for ingestion, concatenate the chunk text, and compare it against existing
`ready` documents in the same workspace. The fuzzy score uses deterministic
lexical signals:
- 64-bit SimHash over normalized three-word shingles.
- Token-count cosine similarity over normalized document tokens.

The document similarity is the stronger of those two scores. If it is greater
than or equal to `FUZZY_DEDUP_THRESHOLD=0.92`, the new upload is saved with
`status="needs_review"` and `Document.error` includes the matched document ID
and similarity score. Chunks and embeddings are still stored so a future review
action can make the document searchable without re-uploading it, but retrieval
continues to filter `Document.status == "ready"`, so `needs_review` chunks are
not scored, re-ranked, cited, or sent to generation.

## Consequences
**Positive:**
- Lightly edited policy revisions are caught even when the raw file hash
  changes.
- Users get a reviewable record instead of a hard rejection, which fits the
  "keep both or supersede old" workflow.
- The detector is deterministic, offline-friendly, and does not add another
  external model call during upload.
- Existing retrieval safety holds because only `ready` documents are eligible.

**Negative / trade-offs:**
- Lexical fuzzy matching does not understand semantic equivalence. A fully
  rewritten policy with the same meaning may not be flagged.
- Highly templated documents can share many tokens and may produce false
  positives. The `needs_review` status is intentionally conservative: humans
  decide the final action.
- Review actions are not implemented yet. Today the UI surfaces the status and
  matched document ID; keep/supersede controls are a follow-up workflow.

## Alternatives considered
- **Document-level embedding cosine**: attractive and simple, but it would add
  more dependence on Hugging Face availability during upload and could behave
  differently in offline fallback mode.
- **MinHash/Jaccard only**: strong for shingle overlap, but brittle when small
  policy files have one important sentence changed. Pairing SimHash with
  token-count cosine is more stable for this milestone.
- **Hard-reject near-duplicates**: safer against stale duplicate content, but
  wrong when the new upload is the intended replacement. Human review preserves
  that decision.
