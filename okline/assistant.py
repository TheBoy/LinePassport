"""Ollama-backed question answering and lightweight RAG helpers."""

from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests


MAX_OLLAMA_RESPONSE_BYTES = 2 * 1024 * 1024


class OllamaError(RuntimeError):
    """Raised when an Ollama endpoint is unavailable or returns invalid data."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def normalize_ollama_base_url(value: str) -> str:
    raw = (value or "http://127.0.0.1:11434").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Ollama URL must use http:// or https://.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Ollama URL must not contain credentials, query, or fragment.")
    path = parsed.path.rstrip("/")
    if path.endswith("/api"):
        path = path[:-4]
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout: float = 120.0,
    ) -> None:
        self.base_url = normalize_ollama_base_url(base_url)
        self.api_key = api_key.strip()
        self.timeout = max(5.0, min(float(timeout), 600.0))

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = requests.request(
                method,
                f"{self.base_url}/api/{path.lstrip('/')}",
                headers=headers,
                json=payload,
                timeout=(5.0, self.timeout),
            )
        except requests.RequestException as exc:
            raise OllamaError(f"Cannot connect to Ollama: {exc}") from exc
        try:
            raw = response.content
            if len(raw) > MAX_OLLAMA_RESPONSE_BYTES:
                raise OllamaError("Ollama response is too large.", response.status_code)
            try:
                data = response.json()
            except ValueError as exc:
                raise OllamaError(
                    f"Ollama returned invalid JSON (HTTP {response.status_code}).",
                    response.status_code,
                ) from exc
            if not response.ok:
                detail = data.get("error") if isinstance(data, dict) else ""
                raise OllamaError(
                    f"Ollama error {response.status_code}: {detail or response.reason}",
                    response.status_code,
                )
            if not isinstance(data, dict):
                raise OllamaError("Ollama returned an unexpected response.")
            return data
        finally:
            response.close()

    def list_models(self) -> list[dict[str, Any]]:
        data = self._request("GET", "tags")
        models = data.get("models")
        if not isinstance(models, list):
            return []
        result: list[dict[str, Any]] = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("model") or item.get("name") or "").strip()
            if not name:
                continue
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            result.append(
                {
                    "name": name,
                    "size": int(item.get("size") or 0),
                    "family": str(details.get("family") or ""),
                    "parameterSize": str(details.get("parameter_size") or ""),
                    "quantization": str(details.get("quantization_level") or ""),
                }
            )
        return result

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        if not model.strip():
            raise OllamaError("Embedding model is not configured.")
        if not texts:
            return []
        data = self._request(
            "POST",
            "embed",
            {"model": model.strip(), "input": texts, "truncate": True},
        )
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise OllamaError("Ollama returned invalid embeddings.")
        result: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or not vector:
                raise OllamaError("Ollama returned an empty embedding.")
            try:
                result.append([float(value) for value in vector])
            except (TypeError, ValueError) as exc:
                raise OllamaError("Ollama returned an invalid embedding vector.") from exc
        return result

    def chat(
        self,
        *,
        model: str,
        question: str,
        context: str,
        system_prompt: str,
        strict_knowledge: bool,
        temperature: float,
    ) -> dict[str, Any]:
        if not model.strip():
            raise OllamaError("Chat model is not configured.")
        policy = (
            "Use only the supplied knowledge. If it is insufficient, set canAnswer to false."
            if strict_knowledge
            else "Use the supplied knowledge when relevant. You may use general knowledge."
        )
        prompt = (
            f"{policy}\n\n"
            "Return JSON with answer, canAnswer, and confidence. Do not include markdown fences.\n\n"
            f"KNOWLEDGE:\n{context or '(no matching knowledge)'}\n\n"
            f"QUESTION:\n{question}"
        )
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "canAnswer": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["answer", "canAnswer", "confidence"],
        }
        data = self._request(
            "POST",
            "chat",
            {
                "model": model.strip(),
                "messages": [
                    {"role": "system", "content": system_prompt.strip()},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": schema,
                "options": {"temperature": max(0.0, min(float(temperature), 2.0))},
            },
        )
        message = data.get("message")
        content = str(message.get("content") or "") if isinstance(message, dict) else ""
        parsed = _parse_json_object(content)
        answer = str(parsed.get("answer") or "").strip()
        can_answer = bool(parsed.get("canAnswer")) and bool(answer)
        try:
            confidence = max(0.0, min(float(parsed.get("confidence") or 0.0), 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "answer": answer,
            "canAnswer": can_answer,
            "confidence": confidence,
            "model": str(data.get("model") or model),
            "promptEvalCount": int(data.get("prompt_eval_count") or 0),
            "evalCount": int(data.get("eval_count") or 0),
        }


def _parse_json_object(value: str) -> dict[str, Any]:
    raw = value.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise OllamaError("Ollama did not return a structured answer.") from exc
    if not isinstance(data, dict):
        raise OllamaError("Ollama did not return a structured answer.")
    return data


def chunk_markdown(value: str, *, max_chars: int = 1200) -> list[str]:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    blocks = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    heading = ""
    for block in blocks:
        if block.startswith("#"):
            heading = block.splitlines()[0].strip()
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        prefix = f"{heading}\n\n" if heading and not block.startswith("#") else ""
        remaining = block
        while len(prefix) + len(remaining) > max_chars:
            limit = max(200, max_chars - len(prefix))
            cut = remaining.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            chunks.append((prefix + remaining[:cut]).strip())
            remaining = remaining[cut:].strip()
        current = (prefix + remaining).strip()
    if current:
        chunks.append(current)
    return chunks


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def lexical_score(query: str, text: str) -> float:
    query_terms = _lexical_terms(query)
    text_terms = _lexical_terms(text)
    if not query_terms or not text_terms:
        return 0.0
    return len(query_terms & text_terms) / math.sqrt(len(query_terms) * len(text_terms))


def _lexical_terms(value: str) -> set[str]:
    normalized = value.casefold()
    words = set(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))
    compact = re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)
    grams = {compact[i : i + 3] for i in range(max(0, len(compact) - 2))}
    return words | grams


def rank_chunks(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    query_embedding: list[float] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        embedding = chunk.get("embedding")
        score = (
            cosine_similarity(query_embedding, embedding)
            if query_embedding is not None and isinstance(embedding, list)
            else lexical_score(question, text)
        )
        if score <= 0:
            continue
        ranked.append({**chunk, "score": round(score, 6)})
    ranked.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return ranked[: max(1, min(int(top_k), 12))]
