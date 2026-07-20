# ADR-002: Per-source-type parsers with structure-aware chunking

## Context
A single sliding-window chunker (split every N characters) is what makes
generic RAG demos fail on real company data: it cuts tables mid-row, breaks
Slack threads mid-conversation, and throws away the one thing that makes a
citation trustworthy — knowing *where* in the source document a fact came
from.

## Decision
Each source type gets its own parser and chunker in
`backend/app/services/ingestion.py`:
- **Markdown/wiki**: split on heading boundaries; the heading text becomes
  the chunk's citation locator (e.g. `§ 4.2 Parental Leave (India)`).
- **PDF**: split by page; the page number is the locator (`p.7`). Pages with
  no extractable text layer are flagged as needing OCR rather than silently
  dropped or garbled.
- **Slack-style JSON threads**: one chunk per thread, messages joined in
  order; the channel name is the locator (`#people-ops`).
- **CSV/XLSX**: one chunk per row, so a single fact ("Asha is in
  Engineering") is never diluted by 50 unrelated rows; the row number is
  the locator (`row 14`).

Any chunk still over `CHUNK_SIZE_CHARS` after structural splitting falls
back to a sliding window with overlap, so nothing is ever silently dropped.

## Consequences
**Positive:**
- Citations in the UI are meaningful (`[HR Wiki §4.2]`, `[India Leave
  Policy.pdf p.7]`, `[Slack #people-ops]`, `row 14`) instead of "chunk #38".
- Table/row-level facts survive retrieval intact instead of being merged
  into a paragraph-shaped blob.
- Scanned PDF pages are explicitly marked as low-quality rather than
  contributing empty or garbled chunks.

**Negative / trade-offs:**
- More parser code to maintain (5 parsers instead of 1).
- OCR itself (Tesseract/PaddleOCR) is *not* implemented yet — scanned pages
  are flagged, not read. This is an explicit, documented scope cut for the
  alpha build (see README "Known limitations").
- Exact duplicate files are rejected, and fuzzy near-duplicates (the same
  policy doc with one paragraph edited) are flagged for human review before
  they become searchable. See ADR-006.

## Alternatives considered
- **Unstructured.io / LlamaParse** for parsing: strong off-the-shelf
  parsers, but they're paid/hosted services or heavy dependencies. Rejected
  for the alpha build to keep the project runnable entirely on free-tier
  infra with only a Hugging Face token as an external dependency; worth
  revisiting once OCR and richer table extraction are in scope.
- **Fixed-size chunking for everything**: simplest to build, but produces
  meaningless locators and cuts across table rows and thread boundaries —
  rejected as the core failure mode this project exists to fix.
