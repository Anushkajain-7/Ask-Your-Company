"""
Seed the local Demo Company workspace through the public API.

Run the FastAPI server first:
    cd backend && uvicorn app.main:app --reload --port 8000

Then run:
    python scripts/seed_demo.py
"""
from __future__ import annotations

from pathlib import Path
import os

import requests


BASE_URL = os.getenv("ASKTHECOMPANY_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
EMAIL = "admin@demo.com"
PASSWORD = "supersecret1"
WORKSPACE = "Demo Company"

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "docs" / "demo" / "corpus"


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

QUESTIONS = [
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


def ensure_server(session: requests.Session) -> None:
    response = session.get(f"{BASE_URL}/api/health", timeout=10)
    response.raise_for_status()


def ensure_account(session: requests.Session) -> str:
    signup = session.post(
        f"{BASE_URL}/api/auth/signup",
        json={
            "email": EMAIL,
            "password": PASSWORD,
            "full_name": "Demo Admin",
            "workspace_name": WORKSPACE,
        },
        timeout=30,
    )
    if signup.status_code not in {200, 400}:
        signup.raise_for_status()

    login = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    login.raise_for_status()
    return login.json()["access_token"]


def ensure_sources(session: requests.Session) -> dict[str, dict]:
    sources = {source["name"]: source for source in session.get(f"{BASE_URL}/api/sources", timeout=30).json()}
    for name, source_type, visibility in SOURCE_SPECS:
        if name in sources:
            continue
        created = session.post(
            f"{BASE_URL}/api/sources",
            params={"name": name, "source_type": source_type, "visible_to_roles": visibility},
            timeout=30,
        )
        created.raise_for_status()
        sources[name] = created.json()
    return sources


def ensure_uploads(session: requests.Session, sources: dict[str, dict]) -> list[tuple[str, str, str]]:
    uploaded = []
    for source_name, filename, content_type in UPLOADS:
        source_id = sources[source_name]["id"]
        existing = session.get(f"{BASE_URL}/api/sources/{source_id}/documents", timeout=30).json()
        if any(doc["filename"] == filename for doc in existing):
            continue

        path = CORPUS / filename
        with path.open("rb") as file_handle:
            response = session.post(
                f"{BASE_URL}/api/sources/{source_id}/documents",
                files={"file": (filename, file_handle, content_type)},
                timeout=90,
            )
        response.raise_for_status()
        document = response.json()
        uploaded.append((source_name, filename, document["status"]))
    return uploaded


def ensure_questions(session: requests.Session) -> int:
    logs = session.get(f"{BASE_URL}/api/audit-log?limit=200", timeout=30).json()
    already_asked = {log["question"] for log in logs}
    asked = 0
    for question in QUESTIONS:
        if question in already_asked:
            continue
        response = session.post(
            f"{BASE_URL}/api/ask",
            json={"question": question, "top_k": 4},
            timeout=120,
        )
        response.raise_for_status()
        asked += 1
    return asked


def main() -> None:
    session = requests.Session()
    ensure_server(session)
    token = ensure_account(session)
    session.headers.update({"Authorization": f"Bearer {token}"})

    sources = ensure_sources(session)
    uploaded = ensure_uploads(session, sources)
    asked = ensure_questions(session)
    final_sources = session.get(f"{BASE_URL}/api/sources", timeout=30).json()
    logs = session.get(f"{BASE_URL}/api/audit-log?limit=200", timeout=30).json()

    print(f"Demo credentials: {EMAIL} / {PASSWORD}")
    print(f"Workspace: {WORKSPACE}")
    print(f"Sources: {len(final_sources)}")
    print(f"New uploads this run: {len(uploaded)}")
    print(f"New questions asked this run: {asked}")
    print(f"Evaluation log entries available: {len(logs)}")
    for source in final_sources:
        print(
            f"- {source['name']}: {source['document_count']} docs, "
            f"{source['coverage_pct']}%, {source['status']}"
        )


if __name__ == "__main__":
    main()
