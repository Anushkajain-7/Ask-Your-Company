"""
Database models for AskTheCompany.

Multi-tenancy model:
- A `User` belongs to one `Workspace` (their company/team space).
- All `Source`, `Document`, `Chunk`, and `QueryLog` rows are scoped to a
  `workspace_id`. Source rows also carry a `visible_to_roles` visibility
  label for document-level ACLs within a workspace. Retrieval applies both
  filters in one place — see docs/adr/ADR-003-permissions-model.md and
  docs/adr/ADR-005-document-level-acls.md.
"""
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Text, Float, Boolean
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="workspace", cascade="all, delete-orphan")
    sources = relationship("Source", back_populates="workspace", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(120), default="")
    role = Column(String(80), default="member")  # "admin" | "member" | custom role like "hr"
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    workspace = relationship("Workspace", back_populates="users")


class Source(Base):
    """A logical connector/bucket, e.g. 'Wiki', 'PDF', 'Slack', 'Sheets'."""
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    name = Column(String(80), nullable=False)          # display name, e.g. "Wiki"
    source_type = Column(String(30), nullable=False)   # markdown|pdf|slack_json|csv|xlsx
    visible_to_roles = Column(String(120), nullable=False, default="all")
    created_at = Column(DateTime, default=datetime.utcnow)

    workspace = relationship("Workspace", back_populates="sources")
    documents = relationship("Document", back_populates="source", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    filename = Column(String(500), nullable=False)
    content_hash = Column(String(64), index=True)  # for near-duplicate detection
    status = Column(String(20), default="processing")  # processing|ready|failed|needs_review
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    source = relationship("Source", back_populates="documents")
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    locator = Column(String(200), default="")  # e.g. "p.7", "§4.2", "row 14", "#people-ops"
    chunk_index = Column(Integer, default=0)
    embedding_json = Column(Text, default="")  # JSON-encoded float vector

    document = relationship("Document", back_populates="chunks")


class QueryLog(Base):
    """Audit log: every question asked, by whom, with what result."""
    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, default="")
    confidence = Column(Float, default=0.0)
    cited_chunk_ids = Column(Text, default="")  # JSON list of chunk ids
    created_at = Column(DateTime, default=datetime.utcnow)
