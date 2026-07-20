from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models import Base

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    _ensure_schema()


def _ensure_schema():
    """Tiny migration shim for the no-Alembic local app setup."""
    inspector = inspect(engine)
    source_columns = {col["name"] for col in inspector.get_columns("sources")}
    if "visible_to_roles" not in source_columns:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE sources "
                    "ADD COLUMN visible_to_roles VARCHAR(120) "
                    "DEFAULT 'all' NOT NULL"
                )
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
