"""
Ingestion: turn a raw uploaded file into a list of (text, locator) chunks.

Design notes (see docs/adr/ADR-002-ingestion-and-chunking.md):
- Each source type gets its OWN parser + chunker, because "one chunker for
  everything" is exactly what makes off-the-shelf RAG fail on messy
  enterprise data (see problem statement: tables, threads, scans).
- `locator` is what powers the citation badges in the UI
  (e.g. "p.7", "\u00a74.2", "row 14", "#people-ops thread").
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from app.core.config import settings


@dataclass
class RawChunk:
    text: str
    locator: str


def content_hash(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def _sliding_window(text: str, base_locator: str) -> List[RawChunk]:
    """Fallback chunker for plain text with no exploitable structure."""
    size = settings.CHUNK_SIZE_CHARS
    overlap = settings.CHUNK_OVERLAP_CHARS
    chunks = []
    start = 0
    n = len(text)
    if n == 0:
        return chunks
    while start < n:
        end = min(start + size, n)
        piece = text[start:end].strip()
        if piece:
            chunks.append(RawChunk(text=piece, locator=base_locator))
        start += size - overlap
    return chunks


def parse_markdown(raw_bytes: bytes, filename: str) -> List[RawChunk]:
    """Heading-aware chunking: split on markdown headings, keep heading as locator."""
    text = raw_bytes.decode("utf-8", errors="ignore")
    lines = text.split("\n")
    chunks: List[RawChunk] = []
    current_heading = filename
    buffer: List[str] = []

    def flush():
        body = "\n".join(buffer).strip()
        if body:
            chunks.extend(_sliding_window(body, current_heading))

    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush()
            buffer = []
            current_heading = f"\u00a7 {m.group(2).strip()}"
        else:
            buffer.append(line)
    flush()
    return chunks or _sliding_window(text, filename)


def parse_pdf(raw_bytes: bytes, filename: str) -> List[RawChunk]:
    """Page-aware chunking. Falls back to noting a page needs OCR if empty."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw_bytes))
    chunks: List[RawChunk] = []
    for i, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        locator = f"p.{i}"
        if not page_text:
            # Scanned page with no extractable text layer.
            chunks.append(
                RawChunk(
                    text=f"[Page {i} of {filename} appears to be a scanned image with "
                    f"no OCR text layer. Enable an OCR step (e.g. Tesseract/PaddleOCR) "
                    f"to make this page searchable.]",
                    locator=locator,
                )
            )
            continue
        chunks.extend(_sliding_window(page_text, locator))
    return chunks


def parse_slack_json(raw_bytes: bytes, filename: str) -> List[RawChunk]:
    """Thread-aware chunking: one chunk per thread, messages joined in order."""
    data = json.loads(raw_bytes.decode("utf-8", errors="ignore"))
    chunks: List[RawChunk] = []
    threads = data if isinstance(data, list) else data.get("threads", [])
    for thread in threads:
        channel = thread.get("channel", "unknown-channel")
        messages = thread.get("messages", [])
        body = "\n".join(f"{m.get('user', '?')}: {m.get('text', '')}" for m in messages)
        if body.strip():
            chunks.extend(_sliding_window(body, f"#{channel}"))
    return chunks


def parse_tabular(raw_bytes: bytes, filename: str) -> List[RawChunk]:
    """Row-aware chunking for CSV/XLSX: each row becomes its own citable chunk."""
    chunks: List[RawChunk] = []
    if filename.lower().endswith((".xlsx", ".xls")):
        import pandas as pd

        df = pd.read_excel(io.BytesIO(raw_bytes))
    else:
        text = raw_bytes.decode("utf-8", errors="ignore")
        df = None
        reader = list(csv.reader(io.StringIO(text)))
        if reader:
            header, rows = reader[0], reader[1:]
            for idx, row in enumerate(rows, start=2):  # row 1 is header
                pairs = ", ".join(f"{h}: {v}" for h, v in zip(header, row))
                if pairs.strip():
                    chunks.append(RawChunk(text=pairs, locator=f"row {idx}"))
            return chunks

    if df is not None:
        for idx, row in df.iterrows():
            pairs = ", ".join(f"{c}: {row[c]}" for c in df.columns)
            chunks.append(RawChunk(text=pairs, locator=f"row {idx + 2}"))
    return chunks


PARSERS = {
    "markdown": parse_markdown,
    "pdf": parse_pdf,
    "slack_json": parse_slack_json,
    "csv": parse_tabular,
    "xlsx": parse_tabular,
}


def infer_source_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".md": "markdown",
        ".markdown": "markdown",
        ".pdf": "pdf",
        ".json": "slack_json",
        ".csv": "csv",
        ".xlsx": "xlsx",
        ".xls": "xlsx",
        ".txt": "markdown",
    }.get(ext, "markdown")


def chunk_file(raw_bytes: bytes, filename: str, source_type: str) -> List[RawChunk]:
    parser = PARSERS.get(source_type, parse_markdown)
    return parser(raw_bytes, filename)
