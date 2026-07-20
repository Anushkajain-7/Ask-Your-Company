import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
ENV_FILE = BASE_DIR / ".env"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str) -> str:
    return os.getenv(name) or default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_load_dotenv(ENV_FILE)


class Settings:
    # --- Secrets: always from environment, never hardcoded. ---
    HF_API_TOKEN: str = os.getenv("HF_API_TOKEN", "")
    JWT_SECRET: str = _env("JWT_SECRET", "dev-secret-change-me-in-.env")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

    # --- Model choice (swappable via .env, no code change needed) ---
    HF_EMBEDDING_MODEL: str = _env("HF_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    HF_CHAT_MODEL: str = _env("HF_CHAT_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
    HF_RERANKER_MODEL: str = _env("HF_RERANKER_MODEL", "BAAI/bge-reranker-base")

    # --- Storage ---
    DATABASE_URL: str = _env("DATABASE_URL", f"sqlite:///{DATA_DIR}/asktheco.db")
    UPLOAD_DIR: Path = DATA_DIR / "uploads"

    # --- Retrieval tuning ---
    TOP_K: int = int(os.getenv("TOP_K", "6"))
    ENABLE_RERANKING: bool = _bool_env("ENABLE_RERANKING", True)
    RERANK_CANDIDATE_K: int = int(_env("RERANK_CANDIDATE_K", "20"))
    FUZZY_DEDUP_THRESHOLD: float = float(_env("FUZZY_DEDUP_THRESHOLD", "0.92"))
    BM25_WEIGHT: float = float(os.getenv("BM25_WEIGHT", "0.4"))
    DENSE_WEIGHT: float = float(os.getenv("DENSE_WEIGHT", "0.6"))
    CHUNK_SIZE_CHARS: int = int(os.getenv("CHUNK_SIZE_CHARS", "1200"))
    CHUNK_OVERLAP_CHARS: int = int(os.getenv("CHUNK_OVERLAP_CHARS", "150"))


settings = Settings()
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
