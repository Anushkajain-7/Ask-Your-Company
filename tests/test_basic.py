"""
Run with: pytest -v
Covers the critical paths: auth, tenant isolation, ingestion, retrieval, ask.
Uses a temporary SQLite DB so it never touches your real data.
"""
import os
import sys
import tempfile
import json
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mktemp(suffix='.db')}"
os.environ["HF_API_TOKEN"] = ""  # force offline fallback path in CI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import init_db
from app.core.db import SessionLocal
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Chunk, Document, Source, User, Workspace
from app.routers import auth as auth_router
from app.services import retrieval
from app.services.hf_client import generate_answer

init_db()
client = TestClient(app)


def _signup(email="user1@acme.com", workspace="Acme"):
    return client.post(
        "/api/auth/signup",
        json={"email": email, "password": "supersecret1", "full_name": "Test User", "workspace_name": workspace},
    )


def test_signup_and_login():
    r = _signup()
    assert r.status_code == 200
    token = r.json()["access_token"]

    r2 = client.post("/api/auth/login", json={"email": "user1@acme.com", "password": "supersecret1"})
    assert r2.status_code == 200
    assert r2.json()["access_token"]


def test_duplicate_signup_rejected():
    _signup(email="dup@acme.com")
    r = _signup(email="dup@acme.com")
    assert r.status_code == 400


def test_wrong_password_rejected():
    _signup(email="wrongpass@acme.com")
    r = client.post("/api/auth/login", json={"email": "wrongpass@acme.com", "password": "nope12345"})
    assert r.status_code == 401


def test_demo_login_auto_seeds_when_enabled(monkeypatch):
    demo_email = "autoseed-demo@example.com"
    demo_workspace = "Auto Seed Demo Co"

    def fake_ensure_demo_workspace(db):
        workspace = Workspace(name=demo_workspace)
        db.add(workspace)
        db.flush()
        user = User(
            email=demo_email,
            hashed_password=hash_password("supersecret1"),
            full_name="Demo Admin",
            role="admin",
            workspace_id=workspace.id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    monkeypatch.setattr(settings, "ENABLE_DEMO_SEED", True)
    monkeypatch.setattr(settings, "DEMO_ADMIN_EMAIL", demo_email)
    monkeypatch.setattr(settings, "DEMO_ADMIN_PASSWORD", "supersecret1")
    monkeypatch.setattr(settings, "DEMO_WORKSPACE_NAME", demo_workspace)
    monkeypatch.setattr(auth_router, "ensure_demo_workspace", fake_ensure_demo_workspace)

    r = client.post(
        "/api/auth/login",
        json={"email": demo_email, "password": "supersecret1"},
    )

    assert r.status_code == 200
    assert r.json()["user_email"] == demo_email
    assert r.json()["workspace_name"] == demo_workspace


def test_unauthenticated_request_rejected():
    r = client.get("/api/sources")
    assert r.status_code == 401


def _auth_headers(email, workspace):
    r = _signup(email=email, workspace=workspace)
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_ingest_and_ask_end_to_end():
    headers = _auth_headers("e2e@acme.com", "E2E Co")

    src = client.post(
        "/api/sources?name=Wiki&source_type=markdown", headers=headers
    )
    assert src.status_code == 200
    source_id = src.json()["id"]

    md_content = (
        b"# HR Wiki\n\n## 4.2 Parental Leave (India)\n"
        b"In 2026, employees in India are entitled to 26 weeks of paid parental leave per child."
    )
    doc = client.post(
        f"/api/sources/{source_id}/documents",
        headers=headers,
        files={"file": ("leave.md", md_content, "text/markdown")},
    )
    assert doc.status_code == 200
    assert doc.json()["status"] == "ready"

    ask = client.post(
        "/api/ask", headers=headers, json={"question": "What is the parental leave policy in India?"}
    )
    assert ask.status_code == 200
    body = ask.json()
    assert len(body["citations"]) > 0
    assert "26 weeks" in body["citations"][0]["text_preview"]
    assert 0 <= body["confidence"] <= 100


def test_duplicate_document_rejected():
    headers = _auth_headers("dupdoc@acme.com", "DupDoc Co")
    src = client.post("/api/sources?name=Wiki&source_type=markdown", headers=headers).json()
    content = b"# Some doc\nHello world, this is unique content for dedup testing."
    r1 = client.post(
        f"/api/sources/{src['id']}/documents", headers=headers,
        files={"file": ("a.md", content, "text/markdown")},
    )
    assert r1.status_code == 200
    r2 = client.post(
        f"/api/sources/{src['id']}/documents", headers=headers,
        files={"file": ("a_copy.md", content, "text/markdown")},
    )
    assert r2.status_code == 409


def _remote_work_policy(days="three") -> bytes:
    return f"""# Remote Work Policy

Remote work policy version A. Employees may work remotely up to {days} days per week with manager approval.
Requests must be submitted in the HR portal before Monday noon. Security training must be completed before accessing internal systems.
Equipment stipends are available once per calendar year. International remote work requires legal review and payroll approval.
Performance reviews remain on the same quarterly cadence. Team leads should document exceptions in the workspace wiki.
This policy applies to full-time employees and interns. Questions should be routed to People Operations.
""".encode()


def test_near_duplicate_document_flagged_for_review():
    headers = _auth_headers("near-dup@acme.com", "Near Dup Co")
    src = client.post("/api/sources?name=Policies&source_type=markdown", headers=headers).json()

    first = client.post(
        f"/api/sources/{src['id']}/documents",
        headers=headers,
        files={"file": ("remote-work-v1.md", _remote_work_policy("three"), "text/markdown")},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "ready"

    second = client.post(
        f"/api/sources/{src['id']}/documents",
        headers=headers,
        files={"file": ("remote-work-v2.md", _remote_work_policy("two"), "text/markdown")},
    )

    assert second.status_code == 200
    body = second.json()
    assert body["status"] == "needs_review"
    assert f"document_id={first.json()['id']}" in body["error"]
    assert "similarity=" in body["error"]

    sources = client.get("/api/sources", headers=headers).json()
    assert sources[0]["status"] == "needs review"


def test_different_document_not_flagged_as_near_duplicate():
    headers = _auth_headers("not-dup@acme.com", "Not Dup Co")
    src = client.post("/api/sources?name=Policies&source_type=markdown", headers=headers).json()

    first = client.post(
        f"/api/sources/{src['id']}/documents",
        headers=headers,
        files={"file": ("remote-work.md", _remote_work_policy("three"), "text/markdown")},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "ready"

    travel_policy = b"""# Travel Policy

Travel expenses are reimbursed after receipts are submitted through the finance portal.
Flights should be booked in economy class unless an exception is approved. Hotel stays should follow the city rate card.
Client meals require attendee names and a business purpose. Team offsites need budget approval before booking.
Reimbursements are paid in the next payroll cycle. Missing receipts require a manager attestation.
The policy applies to business travel only and does not cover relocation, remote work, or home-office equipment.
"""
    second = client.post(
        f"/api/sources/{src['id']}/documents",
        headers=headers,
        files={"file": ("travel.md", travel_policy, "text/markdown")},
    )

    assert second.status_code == 200
    assert second.json()["status"] == "ready"
    assert second.json()["error"] == ""


def test_tenant_isolation():
    """Workspace A's documents must never be retrievable by workspace B."""
    headers_a = _auth_headers("tenantA@corp.com", "Tenant A")
    headers_b = _auth_headers("tenantB@corp.com", "Tenant B")

    src_a = client.post("/api/sources?name=Secrets&source_type=markdown", headers=headers_a).json()
    client.post(
        f"/api/sources/{src_a['id']}/documents", headers=headers_a,
        files={"file": ("secret.md", b"# Confidential\nThe merger codename is Project Falcon.", "text/markdown")},
    )

    ask_b = client.post(
        "/api/ask", headers=headers_b, json={"question": "What is the merger codename?"}
    )
    assert ask_b.status_code == 200
    assert len(ask_b.json()["citations"]) == 0, "Tenant B must not see Tenant A's ingested content"


def test_csv_row_level_citation():
    headers = _auth_headers("csvuser@acme.com", "CSV Co")
    src = client.post("/api/sources?name=Sheets&source_type=csv", headers=headers).json()
    csv_content = b"employee,department,tenure_years\nAsha,Engineering,4\nRavi,Sales,2\n"
    doc = client.post(
        f"/api/sources/{src['id']}/documents", headers=headers,
        files={"file": ("roster.csv", csv_content, "text/csv")},
    )
    assert doc.status_code == 200
    ask = client.post("/api/ask", headers=headers, json={"question": "Which department is Asha in?"})
    assert ask.status_code == 200
    assert any("row" in c["locator"] for c in ask.json()["citations"])


def test_generation_fallback_is_human_readable():
    answer, used_fallback = generate_answer(
        "How often is admin access reviewed?",
        [
            "[Security Policies | security.pdf | p.1] Admin access is reviewed every 30 days by Security Operations. MFA is required for production systems."
        ],
    )

    assert used_fallback is True
    assert "Generation service unavailable" not in answer
    assert "According to Security Policies" in answer
    assert "every 30 days" in answer


def _role_acl_fixture(prefix: str):
    db = SessionLocal()
    try:
        workspace = Workspace(name=f"{prefix} ACL Co")
        db.add(workspace)
        db.flush()

        admin = User(
            email=f"{prefix}-admin@corp.com",
            hashed_password=hash_password("supersecret1"),
            full_name="Admin User",
            role="admin",
            workspace_id=workspace.id,
        )
        member = User(
            email=f"{prefix}-member@corp.com",
            hashed_password=hash_password("supersecret1"),
            full_name="Member User",
            role="member",
            workspace_id=workspace.id,
        )
        db.add_all([admin, member])
        db.flush()

        source = Source(
            workspace_id=workspace.id,
            name="HR Restricted",
            source_type="markdown",
            visible_to_roles="hr",
        )
        db.add(source)
        db.flush()

        document = Document(
            workspace_id=workspace.id,
            source_id=source.id,
            filename="hr-compensation.md",
            content_hash=f"{prefix}-hr-compensation",
            status="ready",
        )
        db.add(document)
        db.flush()

        chunk = Chunk(
            workspace_id=workspace.id,
            document_id=document.id,
            text="The HR-only compensation memo says salary band Phoenix is confidential.",
            locator="§ Compensation",
            chunk_index=0,
            embedding_json=json.dumps([1.0, 0.0]),
        )
        db.add(chunk)
        db.commit()

        admin_token = create_access_token({"sub": str(admin.id)})
        member_token = create_access_token({"sub": str(member.id)})
        return {
            "admin": {"Authorization": f"Bearer {admin_token}"},
            "member": {"Authorization": f"Bearer {member_token}"},
        }
    finally:
        db.close()


def test_member_cannot_retrieve_hr_only_source():
    headers = _role_acl_fixture("member-blocked")

    ask = client.post(
        "/api/ask",
        headers=headers["member"],
        json={"question": "What does the HR-only compensation memo say?"},
    )

    assert ask.status_code == 200
    assert ask.json()["citations"] == []


def test_admin_can_retrieve_hr_only_source():
    headers = _role_acl_fixture("admin-allowed")

    ask = client.post(
        "/api/ask",
        headers=headers["admin"],
        json={"question": "What does the HR-only compensation memo say?"},
    )

    assert ask.status_code == 200
    citations = ask.json()["citations"]
    assert len(citations) == 1
    assert "salary band Phoenix" in citations[0]["text_preview"]


def test_reranking_changes_hybrid_order(monkeypatch):
    """Cross-encoder re-ranking should be able to reorder hybrid candidates."""
    db = SessionLocal()
    try:
        workspace = Workspace(name="Rerank Fixture Co")
        db.add(workspace)
        db.flush()

        source = Source(
            workspace_id=workspace.id,
            name="Fixture Wiki",
            source_type="markdown",
        )
        db.add(source)
        db.flush()

        document = Document(
            workspace_id=workspace.id,
            source_id=source.id,
            filename="fixture.md",
            content_hash="rerank-fixture",
            status="ready",
        )
        db.add(document)
        db.flush()

        hybrid_favorite = Chunk(
            workspace_id=workspace.id,
            document_id=document.id,
            text="alpha alpha alpha annual policy",
            locator="alpha",
            chunk_index=0,
            embedding_json=json.dumps([1.0, 0.0]),
        )
        reranker_favorite = Chunk(
            workspace_id=workspace.id,
            document_id=document.id,
            text="semantic answer for employee benefits",
            locator="semantic",
            chunk_index=1,
            embedding_json=json.dumps([0.0, 1.0]),
        )
        db.add_all([hybrid_favorite, reranker_favorite])
        db.commit()

        monkeypatch.setattr(settings, "BM25_WEIGHT", 0.5)
        monkeypatch.setattr(settings, "DENSE_WEIGHT", 0.5)
        monkeypatch.setattr(settings, "RERANK_CANDIDATE_K", 2)
        monkeypatch.setattr(retrieval, "embed_texts", lambda texts: [[1.0, 0.0] for _ in texts])

        monkeypatch.setattr(settings, "ENABLE_RERANKING", False)
        hybrid_only = retrieval.retrieve(db, workspace.id, "alpha", top_k=2)
        assert [r.chunk_id for r in hybrid_only] == [hybrid_favorite.id, reranker_favorite.id]

        def fake_rerank(query, texts):
            assert query == "alpha"
            assert texts == [hybrid_favorite.text, reranker_favorite.text]
            return [0.1, 0.9]

        monkeypatch.setattr(settings, "ENABLE_RERANKING", True)
        monkeypatch.setattr(retrieval, "rerank_texts", fake_rerank)
        reranked = retrieval.retrieve(db, workspace.id, "alpha", top_k=2)

        assert [r.chunk_id for r in reranked] == [reranker_favorite.id, hybrid_favorite.id]
        assert reranked[0].chunk_id != hybrid_only[0].chunk_id
    finally:
        db.close()
