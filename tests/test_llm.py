import pytest

from megacortes import llm as llm_mod
from megacortes.llm import LLM, LLMError


class Resp:
    def __init__(self, status, data):
        self.status_code, self._d, self.text = status, data, str(data)

    def json(self):
        return self._d


def test_from_env_none_without_keys(monkeypatch):
    for k in ("LLM_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert LLM.from_env() is None


def test_from_env_autodetects_and_never_leaks_key(monkeypatch):
    for k in ("LLM_PROVIDER", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    llm = LLM.from_env()
    assert llm.provider == "openai" and "sk-secret" not in repr(llm)


def test_explicit_provider_without_key_is_an_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        LLM.from_env("gemini")


def test_provider_none_disables(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert LLM.from_env("none") is None


def test_openai_compat_request_shape(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return Resp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    llm = LLM("gemini", "gemini-2.5-flash", "KEY", "https://example.test/v1")
    assert llm.complete("sys", "usr") == "ok"
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer KEY"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_anthropic_request_shape(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return Resp(200, {"content": [{"type": "text", "text": "olá"}]})

    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    llm = LLM("anthropic", "claude-sonnet-5-5", "KEY", "https://api.anthropic.com/v1")
    assert llm.complete("sys", "usr") == "olá"
    assert seen["url"].endswith("/messages") and seen["headers"]["x-api-key"] == "KEY"
    assert seen["body"]["system"] == "sys" and seen["body"]["max_tokens"] > 0


def test_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return Resp(429, {}) if calls["n"] < 3 else Resp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    assert LLM("openai", "m", "k", "https://x").complete("s", "u") == "ok" and calls["n"] == 3


def test_client_error_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return Resp(401, {"error": "bad key"})

    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    with pytest.raises(LLMError, match="401"):
        LLM("openai", "m", "k", "https://x").complete("s", "u")
    assert calls["n"] == 1
