"""Duplicate detection helpers shared by uploads and demo seeding."""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from typing import Iterable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Chunk, Document

DEDUP_SHINGLE_SIZE = 3
DEDUP_MIN_TOKENS = 40
SIMHASH_BITS = 64


def has_exact_duplicate(db: Session, workspace_id: int, new_hash: str) -> bool:
    """Exact-content duplicate guard. Exact duplicates remain hard rejects."""
    existing = (
        db.query(Document)
        .filter(Document.workspace_id == workspace_id, Document.content_hash == new_hash)
        .first()
    )
    return existing is not None


def _dedup_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _dedup_shingles(tokens: list[str]) -> list[str]:
    if len(tokens) < DEDUP_SHINGLE_SIZE:
        return [" ".join(tokens)] if tokens else []
    return [
        " ".join(tokens[i : i + DEDUP_SHINGLE_SIZE])
        for i in range(len(tokens) - DEDUP_SHINGLE_SIZE + 1)
    ]


def _simhash_from_tokens(tokens: list[str]) -> int:
    shingles = _dedup_shingles(tokens)
    if not shingles:
        return 0

    weights = [0] * SIMHASH_BITS
    for shingle in shingles:
        value = int(hashlib.sha256(shingle.encode("utf-8")).hexdigest()[:16], 16)
        for bit in range(SIMHASH_BITS):
            weights[bit] += 1 if value & (1 << bit) else -1

    fingerprint = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            fingerprint |= 1 << bit
    return fingerprint


def _simhash_similarity(left: int, right: int) -> float:
    return 1.0 - ((left ^ right).bit_count() / SIMHASH_BITS)


def _token_cosine(left_tokens: list[str], right_tokens: list[str]) -> float:
    left = Counter(left_tokens)
    right = Counter(right_tokens)
    common = set(left).intersection(right)
    dot = sum(left[token] * right[token] for token in common)
    left_norm = math.sqrt(sum(value * value for value in left.values())) or 1e-9
    right_norm = math.sqrt(sum(value * value for value in right.values())) or 1e-9
    return dot / (left_norm * right_norm)


def _fuzzy_similarity(
    left_tokens: list[str],
    left_fingerprint: int,
    right_tokens: list[str],
    right_fingerprint: int,
) -> float:
    return max(
        _simhash_similarity(left_fingerprint, right_fingerprint),
        _token_cosine(left_tokens, right_tokens),
    )


def _text_from_chunks(raw_chunks: Iterable) -> str:
    return "\n".join(chunk.text for chunk in raw_chunks)


def find_fuzzy_duplicate(db: Session, workspace_id: int, raw_chunks) -> tuple[Document | None, float]:
    new_tokens = _dedup_tokens(_text_from_chunks(raw_chunks))
    if len(new_tokens) < DEDUP_MIN_TOKENS:
        return None, 0.0

    new_fingerprint = _simhash_from_tokens(new_tokens)
    rows = (
        db.query(Document, Chunk)
        .join(Chunk, Chunk.document_id == Document.id)
        .filter(Document.workspace_id == workspace_id)
        .filter(Document.status == "ready")
        .order_by(Document.id, Chunk.chunk_index)
        .all()
    )

    chunks_by_document = defaultdict(list)
    documents = {}
    for document, chunk in rows:
        documents[document.id] = document
        chunks_by_document[document.id].append(chunk.text)

    best_document = None
    best_similarity = 0.0
    for document_id, chunk_texts in chunks_by_document.items():
        existing_tokens = _dedup_tokens("\n".join(chunk_texts))
        if len(existing_tokens) < DEDUP_MIN_TOKENS:
            continue
        existing_fingerprint = _simhash_from_tokens(existing_tokens)
        similarity = _fuzzy_similarity(
            new_tokens,
            new_fingerprint,
            existing_tokens,
            existing_fingerprint,
        )
        if similarity > best_similarity:
            best_similarity = similarity
            best_document = documents[document_id]

    if best_similarity >= settings.FUZZY_DEDUP_THRESHOLD:
        return best_document, best_similarity
    return None, best_similarity
