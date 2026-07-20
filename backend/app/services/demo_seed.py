"""Idempotent demo workspace seeding for hosted review deployments."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models import Chunk, Document, QueryLog, Source, User, Workspace
from app.services.dedup import find_fuzzy_duplicate, has_exact_duplicate
from app.services.hf_client import embed_texts
from app.services.ingestion import chunk_file, content_hash

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = PROJECT_ROOT / "docs" / "demo" / "corpus"

SOURCE_SPECS = [
    ("People Ops Handbook", "markdown", "all"),
    ("Security Policies", "pdf", "admin"),
    ("Engineering Runbook", "markdown", "all"),
    ("Slack Program Threads", "slack_json", "all"),
    ("Vendor Spend Ledger", "csv", "all"),
]

UPLOADS = [
    ("People Ops Handbook", "demo_hr_handbook_v1.md", "text/markdown"),
    ("People Ops Handbook", "demo_hr_handbook_v2_update.md", "text/markdown"),
    ("Security Policies", "demo_security_policy_v1.pdf", "application/pdf"),
    ("Security Policies", "demo_security_policy_v2_update.pdf", "application/pdf"),
    ("Engineering Runbook", "demo_engineering_runbook.md", "text/markdown"),
    ("Slack Program Threads", "demo_slack_threads.json", "application/json"),
    ("Vendor Spend Ledger", "demo_vendor_spend.csv", "text/csv"),
]

DEMO_QUESTIONS = [
    "How many weeks of paid parental leave do India-based primary caregivers receive?",
    "What is the PTO carryover cap and when do carried days expire?",
    "Which weekdays are normal remote work days?",
    "Who approves the ergonomic stipend and what is the annual amount?",
    "What approvals are required for travel expenses over 250 USD?",
    "Which systems require multi-factor authentication?",
    "How often is admin access reviewed?",
    "Where must company secrets be stored?",
    "How long are security event logs retained?",
    "Which channel is the security channel of record for Sev1 incidents?",
    "When are production deploys allowed?",
    "What is the annual release freeze window?",
    "Who becomes incident commander for a Sev1 incident?",
    "What is the standard rollback command?",
    "Who owns the Project Atlas beta and when does it start?",
    "When does the launch freeze for Project Atlas begin and end?",
    "What is the Northwind renewal risk and who owns the follow-up?",
    "Which vendor is blocked in the spend ledger?",
    "What is Datadog monthly spend and who owns it?",
    "When does the VPN migration pilot start and what Okta group is used?",
]


def ensure_demo_workspace(db: Session, include_corpus: bool = True) -> User:
    """Create or repair the public demo login and optional sample corpus.

    This is intentionally gated by settings.ENABLE_DEMO_SEED at the call sites.
    It updates only the configured demo account so a Vercel database that starts
    empty still has a usable workspace for reviewers.
    """
    user = _ensure_demo_user(db)
    if include_corpus:
        sources = _ensure_sources(db, user.workspace_id)
        _ensure_uploads(db, user.workspace_id, sources)
        _ensure_question_logs(db, user)
    return user


def _ensure_demo_user(db: Session) -> User:
    email = settings.DEMO_ADMIN_EMAIL.lower()
    user = db.query(User).filter(User.email == email).first()

    if user:
        if not verify_password(settings.DEMO_ADMIN_PASSWORD, user.hashed_password):
            user.hashed_password = hash_password(settings.DEMO_ADMIN_PASSWORD)
        user.full_name = user.full_name or "Demo Admin"
        user.role = "admin"
        user.is_active = True
        db.commit()
        db.refresh(user)
        return user

    workspace = Workspace(name=settings.DEMO_WORKSPACE_NAME)
    db.add(workspace)
    db.flush()

    user = User(
        email=email,
        hashed_password=hash_password(settings.DEMO_ADMIN_PASSWORD),
        full_name="Demo Admin",
        role="admin",
        workspace_id=workspace.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _ensure_sources(db: Session, workspace_id: int) -> dict[str, Source]:
    sources = {
        source.name: source
        for source in db.query(Source).filter(Source.workspace_id == workspace_id).all()
    }
    for name, source_type, visibility in SOURCE_SPECS:
        if name in sources:
            source = sources[name]
            source.source_type = source_type
            source.visible_to_roles = visibility
            continue
        source = Source(
            workspace_id=workspace_id,
            name=name,
            source_type=source_type,
            visible_to_roles=visibility,
        )
        db.add(source)
        db.flush()
        sources[name] = source
    db.commit()
    return sources


def _ensure_uploads(db: Session, workspace_id: int, sources: dict[str, Source]) -> None:
    if not CORPUS_DIR.exists():
        return

    for source_name, filename, _content_type in UPLOADS:
        source = sources[source_name]
        existing = (
            db.query(Document)
            .filter(Document.source_id == source.id, Document.filename == filename)
            .first()
        )
        if existing:
            continue

        path = CORPUS_DIR / filename
        if not path.exists():
            continue
        _seed_document(db, workspace_id, source, path)


def _seed_document(db: Session, workspace_id: int, source: Source, path: Path) -> None:
    raw = path.read_bytes()
    file_hash = content_hash(raw)
    if has_exact_duplicate(db, workspace_id, file_hash):
        return

    document = Document(
        workspace_id=workspace_id,
        source_id=source.id,
        filename=path.name,
        content_hash=file_hash,
        status="processing",
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    try:
        raw_chunks = chunk_file(raw, path.name, source.source_type)
        if not raw_chunks:
            document.status = "failed"
            document.error = "No extractable text found in this file."
            db.commit()
            return

        matched_document, similarity = find_fuzzy_duplicate(db, workspace_id, raw_chunks)
        texts = [chunk.text for chunk in raw_chunks]
        embeddings = embed_texts(texts)
        for index, (raw_chunk, embedding) in enumerate(zip(raw_chunks, embeddings)):
            db.add(
                Chunk(
                    workspace_id=workspace_id,
                    document_id=document.id,
                    text=raw_chunk.text,
                    locator=raw_chunk.locator,
                    chunk_index=index,
                    embedding_json=json.dumps(embedding),
                )
            )

        if matched_document:
            document.status = "needs_review"
            document.error = (
                f"Near duplicate of document_id={matched_document.id} "
                f"(similarity={similarity:.3f}). Review before making this document searchable."
            )
        else:
            document.status = "ready"
        db.commit()
    except Exception as exc:
        document.status = "failed"
        document.error = str(exc)
        db.commit()


def _ensure_question_logs(db: Session, user: User) -> None:
    existing_questions = {
        row.question
        for row in db.query(QueryLog)
        .filter(QueryLog.workspace_id == user.workspace_id, QueryLog.user_id == user.id)
        .all()
    }
    for index, question in enumerate(DEMO_QUESTIONS):
        if question in existing_questions:
            continue
        db.add(
            QueryLog(
                workspace_id=user.workspace_id,
                user_id=user.id,
                question=question,
                answer="Seeded demo evaluation question.",
                confidence=round(72 + ((index * 7) % 24), 1),
                cited_chunk_ids="[]",
            )
        )
    db.commit()
