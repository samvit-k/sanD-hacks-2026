"""Thin Gemini wrapper. Tests inject a fake via set_llm()."""
import logging
import re
import time
from typing import List, Optional, Protocol, Type, TypeVar

from pydantic import BaseModel

from .config import settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


MAX_QUOTA_WAIT_S = 60
_RETRY_DELAY = re.compile(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s")


class QuotaExhaustedError(RuntimeError):
    """Gemini quota exhausted with a retry delay too long to wait for."""


def quota_retry_delay(exc: Exception) -> Optional[float]:
    """Seconds the API asks us to wait on a 429 quota error, or None if not a quota error / no delay given."""
    text = str(exc)
    if getattr(exc, "code", None) != 429 and "RESOURCE_EXHAUSTED" not in text:
        return None
    m = _RETRY_DELAY.search(text)
    return float(m.group(1)) if m else None


class LLM(Protocol):
    model_name: str

    def generate_json(self, prompt: str, model_cls: Type[T], temperature: Optional[float] = None) -> T: ...

    def embed(self, texts: List[str]) -> List[List[float]]: ...


class GeminiLLM:
    def __init__(self) -> None:
        from google import genai  # imported lazily so tests don't need credentials

        if not settings.gemini_api_key:
            raise RuntimeError("Gemini is not configured on the server: GEMINI_API_KEY is empty for the running "
                               "backend process (set it in .env, or restart the backend without overriding it).")
        self._genai = genai
        from google.genai import types

        # Without an explicit timeout the SDK waits forever on a stalled request (observed live).
        self.client = genai.Client(api_key=settings.gemini_api_key,
                                   http_options=types.HttpOptions(timeout=settings.gemini_timeout_s * 1000))
        self.model_name = settings.gemini_model

    def _retry(self, fn, what: str, attempts: int = 3):
        for i in range(attempts):
            try:
                return fn()
            except Exception as exc:  # rate limits, transient 5xx, malformed JSON
                delay = quota_retry_delay(exc)
                if delay is not None and delay > MAX_QUOTA_WAIT_S:
                    # e.g. a daily quota: retrying in a few seconds cannot succeed.
                    raise QuotaExhaustedError(
                        f"Gemini quota exhausted; the API says to retry in about {delay / 3600:.1f} h.") from exc
                if i == attempts - 1:
                    raise
                wait = max(2 ** i * 2, delay or 0)  # honour short (per-minute) retry delays
                log.warning("%s failed (%s); retrying in %ss", what, exc, wait)
                time.sleep(wait)

    def generate_json(self, prompt: str, model_cls: Type[T], temperature: Optional[float] = None) -> T:
        """Structured output. Low temperature by default (faithful extraction/judgment); question
        proposals pass a higher one so "Regenerate" can yield a different formulation."""
        from google.genai import types

        def call():
            resp = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=model_cls,
                    temperature=0.1 if temperature is None else temperature,
                ),
            )
            if isinstance(resp.parsed, model_cls):
                return resp.parsed
            return model_cls.model_validate_json(resp.text)

        return self._retry(call, f"Gemini {model_cls.__name__}")

    def embed(self, texts: List[str]) -> List[List[float]]:
        from google.genai import types

        out: List[List[float]] = []
        for start in range(0, len(texts), 100):
            batch = texts[start:start + 100]

            def call():
                return self.client.models.embed_content(
                    model=settings.embed_model,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        output_dimensionality=settings.embed_dim,
                        task_type="SEMANTIC_SIMILARITY",
                    ),
                )

            resp = self._retry(call, "Gemini embed")
            out.extend(list(e.values) for e in resp.embeddings)
        return out


_llm: Optional[LLM] = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = GeminiLLM()
    return _llm


def set_llm(llm: Optional[LLM]) -> None:
    global _llm
    _llm = llm
