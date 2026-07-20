# Resume bullets

- Built a multi-tenant enterprise RAG web app with FastAPI, SQLAlchemy, JWT auth, SQLite/Postgres configuration, and workspace-scoped retrieval, preserving tenant isolation across 13 passing integration/unit tests.
- Implemented hybrid BM25+dense retrieval with Hugging Face embeddings plus toggleable cross-encoder re-ranking, improving the synthetic 100-question eval from 64.0% to 66.0% answer accuracy and from 31.0% to 34.5% citation precision.
- Designed structure-aware ingestion for markdown, PDF, Slack JSON, and CSV/XLSX sources, achieving 100.0% table-lookup accuracy and 100.0% table citation recall on a 25-question roster eval slice.
- Added source-level role ACLs and fuzzy near-duplicate detection with SimHash/token-cosine scoring, flagging revised policy docs as `needs_review` while keeping restricted and unreviewed documents out of retrieval.
