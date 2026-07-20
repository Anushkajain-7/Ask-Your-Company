import json
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models import QueryLog, User
from app.schemas import AskRequest, AskResponse, AuditEntry, Citation
from app.services.hf_client import generate_answer
from app.services.retrieval import confidence_from_results, retrieve

router = APIRouter(prefix="/api", tags=["ask"])


@router.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    results = retrieve(
        db,
        user.workspace_id,
        payload.question,
        payload.top_k,
        user_role=user.role,
    )
    confidence = confidence_from_results(results)

    context_blocks = [
        f"[{r.source_name} | {r.document_filename} | {r.locator}] {r.text}" for r in results
    ]
    answer, used_fallback = generate_answer(payload.question, context_blocks)

    citations = [
        Citation(
            chunk_id=r.chunk_id,
            source_name=r.source_name,
            source_type=r.source_type,
            document_filename=r.document_filename,
            locator=r.locator,
            score=r.score,
            text_preview=r.text[:220],
        )
        for r in results
    ]

    log = QueryLog(
        workspace_id=user.workspace_id,
        user_id=user.id,
        question=payload.question,
        answer=answer,
        confidence=confidence,
        cited_chunk_ids=json.dumps([c.chunk_id for c in citations]),
    )
    db.add(log)
    db.commit()

    return AskResponse(
        answer=answer,
        confidence=confidence,
        citations=citations,
        used_fallback=used_fallback,
        generated_at=datetime.utcnow(),
    )


@router.get("/audit-log", response_model=list[AuditEntry])
def audit_log(limit: int = 50, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    logs = (
        db.query(QueryLog)
        .filter(QueryLog.workspace_id == user.workspace_id)
        .order_by(QueryLog.created_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for log in logs:
        asker = log.user_id
        out.append(
            AuditEntry(
                id=log.id,
                user_email=_email_for(db, asker),
                question=log.question,
                confidence=log.confidence,
                created_at=log.created_at,
            )
        )
    return out


def _email_for(db: Session, user_id: int) -> str:
    u = db.query(User).filter(User.id == user_id).first()
    return u.email if u else "unknown"
