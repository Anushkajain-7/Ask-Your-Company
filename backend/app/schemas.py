from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = ""
    workspace_name: str = Field(min_length=2)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_email: str
    workspace_name: str


class MeResponse(BaseModel):
    email: str
    full_name: str
    role: str
    workspace_name: str


class SourceOut(BaseModel):
    id: int
    name: str
    source_type: str
    visible_to_roles: str
    document_count: int
    coverage_pct: float
    status: str


class DocumentOut(BaseModel):
    id: int
    filename: str
    status: str
    source_name: str
    error: str = ""


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: Optional[int] = None


class Citation(BaseModel):
    chunk_id: int
    source_name: str
    source_type: str
    document_filename: str
    locator: str
    score: float
    text_preview: str


class AskResponse(BaseModel):
    answer: str
    confidence: float
    citations: List[Citation]
    used_fallback: bool
    generated_at: datetime


class AuditEntry(BaseModel):
    id: int
    user_email: str
    question: str
    confidence: float
    created_at: datetime
