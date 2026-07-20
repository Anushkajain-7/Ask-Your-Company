"""Vercel entry point for AskTheCompany.

Vercel's Python runtime discovers functions under `api/`. The actual FastAPI
application lives in `backend/app/main.py`, so this file only adjusts
`sys.path` and re-exports the existing `app` instance.
"""
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402

