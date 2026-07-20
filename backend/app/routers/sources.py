import json
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models import Chunk, Document, Source, User
from app.schemas import DocumentOut, SourceOut
from app.services.dedup import find_fuzzy_duplicate, has_exact_duplicate
from app.services.hf_client import embed_texts
from app.services.ingestion import chunk_file, content_hash

router = APIRouter(prefix="/api/sources", tags=["sources"])


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

    if has_exact_duplicate(db, user.workspace_id, file_hash):
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

        matched_document, similarity = find_fuzzy_duplicate(db, user.workspace_id, raw_chunks)
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
