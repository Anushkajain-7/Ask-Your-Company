"""
Hybrid retrieval: BM25 (lexical) + dense cosine similarity (semantic),
combined with a weighted sum, optionally followed by cross-encoder
re-ranking, then handed to hf_client for grounded generation. Everything
here is filtered by workspace_id and source visibility first — that's the
permission boundary (see ADR-003 and ADR-005).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import List

from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Chunk, Document, Source
from app.services.hf_client import embed_texts, rerank_texts


@dataclass
class RetrievedChunk:
    chunk_id: int
    text: str
    locator: str
    source_name: str
    source_type: str
    document_filename: str
    score: float


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (na * nb)


def _normalize(scores: List[float]) -> List[float]:
    if not scores:
        return scores
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        return [0.5 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_result(row, score: float) -> RetrievedChunk:
    chunk, document, source = row
    return RetrievedChunk(
        chunk_id=chunk.id,
        text=chunk.text,
        locator=chunk.locator,
        source_name=source.name,
        source_type=source.source_type,
        document_filename=document.filename,
        score=round(float(score), 4),
    )


def _normalized_role(role: str | None) -> str:
    return (role or "member").strip().lower() or "member"


def retrieve(
    db: Session,
    workspace_id: int,
    query: str,
    top_k: int | None = None,
    user_role: str = "member",
) -> List[RetrievedChunk]:
    top_k = top_k or settings.TOP_K
    role = _normalized_role(user_role)

    rows_query = (
        db.query(Chunk, Document, Source)
        .join(Document, Chunk.document_id == Document.id)
        .join(Source, Document.source_id == Source.id)
        .filter(Chunk.workspace_id == workspace_id)  # <-- tenant isolation boundary
        .filter(Document.status == "ready")
    )

    if role != "admin":
        rows_query = rows_query.filter(Source.visible_to_roles.in_(["all", role]))

    rows = rows_query.all()
    if not rows:
        return []

    corpus_texts = [r[0].text for r in rows]
    tokenized = [_tokenize(t) for t in corpus_texts]
    bm25 = BM25Okapi(tokenized)
    bm25_scores = bm25.get_scores(_tokenize(query))
    bm25_scores = _normalize(list(bm25_scores))

    query_vec = embed_texts([query])[0]
    dense_scores = []
    for r in rows:
        chunk = r[0]
        try:
            emb = json.loads(chunk.embedding_json) if chunk.embedding_json else None
        except Exception:
            emb = None
        dense_scores.append(_cosine(query_vec, emb) if emb else 0.0)
    dense_scores = _normalize(dense_scores)

    combined = [
        settings.BM25_WEIGHT * b + settings.DENSE_WEIGHT * d
        for b, d in zip(bm25_scores, dense_scores)
    ]

    candidate_k = top_k
    if settings.ENABLE_RERANKING:
        candidate_k = max(top_k, settings.RERANK_CANDIDATE_K)

    ranked_candidates = sorted(zip(rows, combined), key=lambda x: x[1], reverse=True)[:candidate_k]

    if not settings.ENABLE_RERANKING or len(ranked_candidates) <= 1:
        return [_build_result(row, score) for row, score in ranked_candidates[:top_k]]

    candidate_texts = [row[0].text for row, _ in ranked_candidates]
    rerank_scores = rerank_texts(query, candidate_texts)
    if len(rerank_scores) != len(ranked_candidates):
        return [_build_result(row, score) for row, score in ranked_candidates[:top_k]]

    normalized_rerank_scores = _normalize([float(score) for score in rerank_scores])
    reranked = sorted(
        zip(ranked_candidates, normalized_rerank_scores),
        key=lambda item: (item[1], item[0][1]),
        reverse=True,
    )[:top_k]

    return [_build_result(row, score) for ((row, _hybrid_score), score) in reranked]


def confidence_from_results(results: List[RetrievedChunk]) -> float:
    """Heuristic confidence: top score, gently boosted by agreement among
    top-3 (multiple corroborating chunks -> higher confidence)."""
    if not results:
        return 0.0
    top = results[0].score
    agreement = sum(r.score for r in results[:3]) / (3 * max(top, 1e-6))
    conf = 0.7 * top + 0.3 * min(agreement, 1.0)
    return round(min(max(conf, 0.0), 1.0) * 100, 1)
