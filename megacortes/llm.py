"""Cliente LLM mínimo (HTTP puro, sem SDKs).

Cobre Gemini, OpenAI, OpenRouter e Ollama (todos via API compatível com OpenAI)
e a API de mensagens da Anthropic. Sem chave configurada, o pipeline roda só com
a seleção heurística.
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

PRESETS = {
    "gemini": dict(
        base="https://generativelanguage.googleapis.com/v1beta/openai",
        key_env="GEMINI_API_KEY",
        model="gemini-2.5-flash",
    ),
    "openai": dict(base="https://api.openai.com/v1", key_env="OPENAI_API_KEY", model="gpt-4o-mini"),
    "openrouter": dict(
        base="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        model="google/gemini-2.5-flash",
    ),
    "ollama": dict(base=None, key_env=None, model="qwen2.5:14b"),
    "anthropic": dict(
        base="https://api.anthropic.com/v1", key_env="ANTHROPIC_API_KEY", model="claude-sonnet-5-5"
    ),
}
AUTO_ORDER = ("gemini", "anthropic", "openai", "openrouter")


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, provider: str, model: str, api_key: str | None, base_url: str, timeout: int = 180):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def __repr__(self) -> str:  # nunca imprimir a chave
        return f"LLM({self.provider}:{self.model})"

    @classmethod
    def from_env(cls, provider: str | None = None, model: str | None = None) -> "LLM | None":
        provider = (provider or os.getenv("LLM_PROVIDER") or "").lower().strip()
        if provider in ("none", "off", "no"):
            return None
        if not provider:
            for cand in AUTO_ORDER:
                if os.getenv(PRESETS[cand]["key_env"]):
                    provider = cand
                    break
            else:
                return None
        if provider not in PRESETS:
            raise LLMError(f"LLM_PROVIDER inválido: {provider}. Use um de {sorted(PRESETS)} ou none")
        preset = PRESETS[provider]
        key = os.getenv(preset["key_env"]) if preset["key_env"] else None
        if preset["key_env"] and not key:
            raise LLMError(f"{preset['key_env']} não definida (provedor {provider})")
        base = preset["base"] or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        return cls(provider, model or os.getenv("LLM_MODEL") or preset["model"], key, base)

    # ------------------------------------------------------------------
    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        last: Exception | None = None
        for attempt in range(3):
            try:
                if self.provider == "anthropic":
                    return self._anthropic(system, user, max_tokens)
                return self._openai_compat(system, user)
            except (requests.Timeout, requests.ConnectionError, _Retryable) as e:
                last = e
                time.sleep(2 ** attempt * 2)
        raise LLMError(f"Falha após 3 tentativas: {last}")

    def _openai_compat(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "temperature": 0.3,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        r = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=body, timeout=self.timeout)
        _check(r)
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"Resposta inesperada do {self.provider}: {r.text[:300]}") from e

    def _anthropic(self, system: str, user: str, max_tokens: int) -> str:
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        r = requests.post(f"{self.base_url}/messages", headers=headers, json=body, timeout=self.timeout)
        _check(r)
        try:
            return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type") == "text")
        except (KeyError, ValueError) as e:
            raise LLMError(f"Resposta inesperada da Anthropic: {r.text[:300]}") from e


class _Retryable(Exception):
    pass


def _check(r: requests.Response) -> None:
    if r.status_code in (429, 500, 502, 503, 504, 529):
        raise _Retryable(f"HTTP {r.status_code}")
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")


def extract_json(text: str):
    """Extrai JSON de uma resposta de LLM (aceita cercas ``` e texto em volta)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("[", "]"), ("{", "}")):
        i, j = text.find(open_c), text.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("Não foi possível extrair JSON da resposta do LLM")
