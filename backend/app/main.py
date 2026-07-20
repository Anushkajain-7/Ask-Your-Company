from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.routers import ask, auth, sources
from app.services.demo_seed import ensure_demo_workspace

app = FastAPI(title="AskTheCompany", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to your deployed frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(sources.router)
app.include_router(ask.router)


@app.on_event("startup")
def on_startup():
    init_db()
    if settings.ENABLE_DEMO_SEED:
        db = SessionLocal()
        try:
            ensure_demo_workspace(db)
        except Exception as exc:
            print(f"Demo seed skipped: {exc}")
        finally:
            db.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}


FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
