"""
Thin wrapper around the Hugging Face Inference API.

Two capabilities are used:
1. Embeddings (feature-extraction) for dense retrieval.
2. Cross-encoder re-ranking for second-stage relevance scoring.
3. Chat completion for answer generation, grounded in retrieved chunks.

If HF_API_TOKEN is not set, this module falls back to a deterministic
local embedding (hashing trick), deterministic lexical re-ranking, and a
template-based answer, so the app still runs end-to-end in a demo/offline
setting instead of crashing. In that fallback mode `used_fallback=True` is
reported for answer generation so the UI can be honest about it instead of
silently degrading quality.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import List

import requests
from huggingface_hub import InferenceClient

from app.core.config import settings

_client: InferenceClient | None = None
HF_ROUTER_CHAT_URL = "https://router.huggingface.co/v1/chat/completions"
HF_TIMEOUT_SECONDS = 30
MAX_CONTEXT_CHARS = 6000

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
}


def _get_client() -> InferenceClient | None:
    global _client
    if not settings.HF_API_TOKEN:
        return None
    if _client is None:
        _client = InferenceClient(token=settings.HF_API_TOKEN)
    return _client


FALLBACK_DIM = 256


def _fallback_embed(text: str) -> List[float]:
    """Deterministic bag-of-hashed-tokens embedding. Not semantic, but stable
    and dependency-free, so the app is demoable without any API key."""
    vec = [0.0] * FALLBACK_DIM
    for tok in _tokenize(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % FALLBACK_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed_texts(texts: List[str]) -> list[list[float]]:
    client = _get_client()
    if client is None:
        return [_fallback_embed(t) for t in texts]
    try:
        vectors = client.feature_extraction(texts, model=settings.HF_EMBEDDING_MODEL)
        # Some models return per-token vectors; mean-pool if so.
        out = []
        for v in vectors:
            if isinstance(v[0], list):
                dim = len(v[0])
                pooled = [sum(row[i] for row in v) / len(v) for i in range(dim)]
                out.append(pooled)
            else:
                out.append(list(v))
        return out
    except Exception:
        # Network/model/rate-limit failure -> degrade, don't crash the request.
        return [_fallback_embed(t) for t in texts]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _expanded_token_set(text: str) -> set[str]:
    tokens = set(_tokenize(text))
    expanded = set(tokens)
    for token in tokens:
        if token.endswith("s") and len(token) > 3:
            expanded.add(token[:-1])
        if token in {"own", "owns", "owned", "owner", "owners"}:
            expanded.update({"own", "owner"})
        if token in {"approve", "approves", "approved", "approval", "approvals"}:
            expanded.update({"approve", "approval"})
    return expanded


def _fallback_rerank(query: str, texts: list[str]) -> list[float]:
    """Deterministic lexical scorer used when the cross-encoder is unavailable."""
    query_tokens = _expanded_token_set(query)
    if not query_tokens:
        return [0.0 for _ in texts]

    scores = []
    query_lower = query.lower().strip()
    for text in texts:
        text_tokens = _tokenize(text)
        text_token_set = _expanded_token_set(text)
        if not text_tokens:
            scores.append(0.0)
            continue
        overlap = sum(1 for token in text_tokens if token in query_tokens)
        coverage = len(query_tokens.intersection(text_token_set)) / len(query_tokens)
        phrase_boost = 1.0 if query_lower and query_lower in text.lower() else 0.0
        scores.append((overlap / math.sqrt(len(text_tokens))) + coverage + phrase_boost)
    return scores


def _decode_response(raw):
    if isinstance(raw, bytes):
        return json.loads(raw.decode("utf-8"))
    return raw


def _router_chat_completion(messages: list[dict[str, str]], max_tokens: int = 500) -> str:
    """Use Hugging Face's current OpenAI-compatible router endpoint.

    huggingface_hub==0.25.1 still targets api-inference.huggingface.co for
    chat completion, which can fail independently of the token/model. The
    router endpoint is the preferred path for hosted chat models.
    """
    response = requests.post(
        HF_ROUTER_CHAT_URL,
        headers={
            "Authorization": f"Bearer {settings.HF_API_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.HF_CHAT_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        },
        timeout=HF_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Hugging Face router returned HTTP {response.status_code}")

    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("Hugging Face router returned no choices")

    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        raise RuntimeError("Hugging Face router returned an empty answer")
    return content


def _score_from_output(item) -> float | None:
    if isinstance(item, (int, float)):
        return float(item)

    if isinstance(item, dict):
        for key in ("score", "relevance_score", "rerank_score"):
            if key in item:
                return float(item[key])
        if "scores" in item:
            return _score_from_output(item["scores"])
        if "label" in item and "score" in item:
            return float(item["score"])

    if isinstance(item, list):
        if not item:
            return None
        positive = [
            entry
            for entry in item
            if isinstance(entry, dict)
            and str(entry.get("label", "")).upper() in {"LABEL_1", "POSITIVE", "RELEVANT"}
            and "score" in entry
        ]
        if positive:
            return float(positive[0]["score"])
        scored = [entry for entry in item if isinstance(entry, dict) and "score" in entry]
        if scored:
            return float(max(scored, key=lambda entry: entry["score"])["score"])
        if len(item) == 1:
            return _score_from_output(item[0])

    return None


def _extract_rerank_scores(payload, expected_len: int) -> list[float]:
    if isinstance(payload, dict):
        for key in ("scores", "data", "results"):
            if key in payload:
                return _extract_rerank_scores(payload[key], expected_len)

    if isinstance(payload, list):
        scores = [_score_from_output(item) for item in payload]
        scores = [score for score in scores if score is not None]
        if len(scores) == expected_len:
            return scores
        if len(payload) == 1:
            return _extract_rerank_scores(payload[0], expected_len)

    return []


def rerank_texts(query: str, texts: list[str]) -> list[float]:
    """Return one relevance score per text, preserving input order.

    The preferred path uses a Hugging Face cross-encoder. The fallback is
    lexical and deterministic so retrieval remains available without a token,
    during rate limits, or when a free-tier model is cold-starting.
    """
    if not texts:
        return []

    client = _get_client()
    if client is None:
        return _fallback_rerank(query, texts)

    payloads = [
        (
            "text-classification",
            {"inputs": [{"text": query, "text_pair": text} for text in texts]},
        ),
        (
            "sentence-similarity",
            {"inputs": {"source_sentence": query, "sentences": texts}},
        ),
    ]

    for task, payload in payloads:
        try:
            raw = client.post(
                json=payload,
                model=settings.HF_RERANKER_MODEL,
                task=task,
            )
            scores = _extract_rerank_scores(_decode_response(raw), len(texts))
            if len(scores) == len(texts):
                return scores
        except Exception:
            continue

    return _fallback_rerank(query, texts)


def _parse_context_block(block: str) -> tuple[str, str, str, str]:
    match = re.match(r"^\[(?P<source>[^|]+)\|(?P<doc>[^|]+)\|(?P<loc>[^\]]+)\]\s*(?P<text>.*)$", block, re.S)
    if not match:
        return "workspace source", "document", "passage", block.strip()
    return (
        match.group("source").strip(),
        match.group("doc").strip(),
        match.group("loc").strip(),
        match.group("text").strip(),
    )


def _humanize_text(text: str) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    clean = re.sub(r"\b([a-z]+(?:_[a-z]+)+):", lambda m: m.group(1).replace("_", " ") + ":", clean)
    return clean


def _question_terms(question: str) -> set[str]:
    return {token for token in _tokenize(question) if len(token) > 2 and token not in STOPWORDS}


def _split_sentences(text: str) -> list[str]:
    text = _humanize_text(text)
    if not text:
        return []

    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) <= 1 and "," in text and ":" in text:
        return [text]
    return sentences or [text]


def _sentence_score(question_terms: set[str], sentence: str, rank: int) -> float:
    sentence_tokens = _tokenize(sentence)
    if not sentence_tokens:
        return 0.0
    overlap = sum(1 for token in sentence_tokens if token in question_terms)
    coverage = len(question_terms.intersection(sentence_tokens)) / (len(question_terms) or 1)
    return overlap + coverage - (rank * 0.05)


def _extractive_answer(question: str, context_blocks: list[str]) -> str:
    if not context_blocks:
        return "I could not find matching passages in this workspace's ingested sources."

    q_terms = _question_terms(question)
    candidates: list[tuple[float, int, str, str, str, str]] = []
    for block_rank, block in enumerate(context_blocks[:6]):
        source, document, locator, text = _parse_context_block(block)
        for sentence in _split_sentences(text):
            score = _sentence_score(q_terms, sentence, block_rank)
            candidates.append((score, block_rank, source, document, locator, sentence))

    if not candidates:
        source, document, locator, text = _parse_context_block(context_blocks[0])
        candidates.append((0.0, 0, source, document, locator, _humanize_text(text)))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = []
    seen = set()
    best_score = candidates[0][0]
    primary_location = None
    for score, _, source, document, locator, sentence in candidates:
        key = (source, document, locator, sentence)
        if key in seen:
            continue
        if selected:
            if score <= 0 or score < best_score * 0.6:
                continue
            if primary_location == (source, document, locator):
                continue
        seen.add(key)
        selected.append((source, document, locator, sentence))
        primary_location = primary_location or (source, document, locator)
        if len(selected) == 2:
            break

    if len(selected) == 1:
        source, document, locator, sentence = selected[0]
        return f"According to {source} ({document}, {locator}), {sentence}"

    lines = [
        f"- {sentence} Source: {source} ({document}, {locator})."
        for source, document, locator, sentence in selected
    ]
    return "I found these matching facts:\n" + "\n".join(lines)


def _trim_context(context_blocks: list[str]) -> str:
    context = "\n\n".join(context_blocks)
    if len(context) <= MAX_CONTEXT_CHARS:
        return context
    return context[:MAX_CONTEXT_CHARS] + "\n\n[Context truncated to fit the generation request.]"


def generate_answer(question: str, context_blocks: List[str]) -> tuple[str, bool]:
    """Returns (answer_text, used_fallback)."""
    if not context_blocks:
        return (
            "No matching passages were found in this workspace's ingested sources.",
            True,
        )

    context = _trim_context(context_blocks)
    system_prompt = (
        "You are AskTheCompany, an internal enterprise assistant. Answer ONLY using "
        "the provided context. If the context does not contain the answer, say you "
        "don't have enough information. Be concise, factual, and human readable. "
        "Do not invent sources or facts."
    )
    user_prompt = (
        f"Context:\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer in 1-3 sentences. Mention the source name naturally if useful."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if settings.HF_API_TOKEN:
        try:
            return _router_chat_completion(messages), False
        except Exception:
            pass

    client = _get_client()
    if client is not None:
        try:
            completion = client.chat_completion(
                messages=messages,
                model=settings.HF_CHAT_MODEL,
                max_tokens=500,
                temperature=0.2,
            )
            return completion.choices[0].message.content, False
        except Exception:
            pass

    return (_extractive_answer(question, context_blocks), True)
