import hashlib
import json
import math
import re
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.models import Chunk, Document, Source, User
from app.schemas import DocumentOut, SourceOut
from app.services.hf_client import embed_texts
from app.services.ingestion import chunk_file, content_hash, infer_source_type

router = APIRouter(prefix="/api/sources", tags=["sources"])

DEDUP_SHINGLE_SIZE = 3
DEDUP_MIN_TOKENS = 40
SIMHASH_BITS = 64


def _normalize_visibility(value: str) -> str:
    role = (value or "all").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,30}", role):
        raise HTTPException(
            status_code=400,
            detail="visible_to_roles must be a role label like all, admin, member, or hr",
        )
    return role


def _source_status(docs: list[Document]) -> tuple[float, str]:
    if not docs:
        return 0.0, "empty"
    ready = [d for d in docs if d.status == "ready"]
    coverage = round(100 * len(ready) / len(docs), 1)
    if any(d.status == "needs_review" for d in docs):
        return coverage, "needs review"
    if coverage == 100:
        return coverage, "up to date"
    return coverage, "partial sync"


@router.get("", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sources = db.query(Source).filter(Source.workspace_id == user.workspace_id).all()
    out = []
    for s in sources:
        docs = s.documents
        coverage, status = _source_status(docs)
        out.append(
            SourceOut(
                id=s.id,
                name=s.name,
                source_type=s.source_type,
                visible_to_roles=s.visible_to_roles or "all",
                document_count=len(docs),
                coverage_pct=coverage,
                status=status,
            )
        )
    return out


@router.post("", response_model=SourceOut)
def create_source(
    name: str,
    source_type: str,
    visible_to_roles: str = "all",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    valid_types = {"markdown", "pdf", "slack_json", "csv", "xlsx"}
    if source_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"source_type must be one of {valid_types}")
    source = Source(
        workspace_id=user.workspace_id,
        name=name,
        source_type=source_type,
        visible_to_roles=_normalize_visibility(visible_to_roles),
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return SourceOut(
        id=source.id,
        name=source.name,
        source_type=source.source_type,
        visible_to_roles=source.visible_to_roles,
        document_count=0,
        coverage_pct=0.0,
        status="empty",
    )


def _has_exact_duplicate(db: Session, workspace_id: int, new_hash: str) -> bool:
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


def _text_from_chunks(raw_chunks) -> str:
    return "\n".join(chunk.text for chunk in raw_chunks)


def _find_fuzzy_duplicate(db: Session, workspace_id: int, raw_chunks) -> tuple[Document | None, float]:
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


@router.post("/{source_id}/documents", response_model=DocumentOut)
async def upload_document(
    source_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    source = (
        db.query(Source)
        .filter(Source.id == source_id, Source.workspace_id == user.workspace_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    raw = await file.read()
    file_hash = content_hash(raw)

    if _has_exact_duplicate(db, user.workspace_id, file_hash):
        raise HTTPException(status_code=409, detail="This exact file has already been ingested (duplicate detected)")

    document = Document(
        workspace_id=user.workspace_id,
        source_id=source.id,
        filename=file.filename,
        content_hash=file_hash,
        status="processing",
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    try:
        raw_chunks = chunk_file(raw, file.filename, source.source_type)
        if not raw_chunks:
            document.status = "failed"
            document.error = "No extractable text found in this file."
            db.commit()
            return _doc_out(document, source.name)

        matched_document, similarity = _find_fuzzy_duplicate(db, user.workspace_id, raw_chunks)
        texts = [c.text for c in raw_chunks]
        embeddings = embed_texts(texts)

        for i, (rc, emb) in enumerate(zip(raw_chunks, embeddings)):
            chunk = Chunk(
                workspace_id=user.workspace_id,
                document_id=document.id,
                text=rc.text,
                locator=rc.locator,
                chunk_index=i,
                embedding_json=json.dumps(emb),
            )
            db.add(chunk)

        if matched_document:
            document.status = "needs_review"
            document.error = (
                f"Near duplicate of document_id={matched_document.id} "
                f"(similarity={similarity:.3f}). Review before making this document searchable."
            )
        else:
            document.status = "ready"
        db.commit()
    except Exception as e:
        document.status = "failed"
        document.error = str(e)
        db.commit()

    return _doc_out(document, source.name)


def _doc_out(document: Document, source_name: str) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        filename=document.filename,
        status=document.status,
        source_name=source_name,
        error=document.error or "",
    )


@router.get("/{source_id}/documents", response_model=list[DocumentOut])
def list_documents(source_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    source = (
        db.query(Source)
        .filter(Source.id == source_id, Source.workspace_id == user.workspace_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return [_doc_out(d, source.name) for d in source.documents]
