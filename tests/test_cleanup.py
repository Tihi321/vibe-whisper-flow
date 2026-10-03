import pytest
import requests

from vibeflow import cleanup
from vibeflow.cleanup import (
    CleanupError,
    OpenAICompatCleaner,
    build_messages,
    list_models,
    make_cleaner,
    sanitize_response,
)
from vibeflow.config import CleanupConfig


def test_default_prompt_is_short():
    assert len(cleanup.DEFAULT_PROMPT.split()) < 140


def test_build_messages():
    msgs = build_messages("PROMPT", "hello there")
    assert msgs[0] == {"role": "system", "content": "PROMPT"}
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "<transcript>\nhello there\n</transcript>"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hello world.", "Hello world."),
        ("```\nHello world.\n```", "Hello world."),
        ("```text\nHello world.\n```", "Hello world."),
        ('"Hello world."', "Hello world."),
        ("'Hello world.'", "Hello world."),
        ("<transcript>\nHello world.\n</transcript>", "Hello world."),
        ("Here is the cleaned text:\nHello world.", "Hello world."),
        ("Here is the cleaned up text: Hello world.", "Hello world."),
        ("Cleaned text: Hello world.", "Hello world."),
        ("Sure, here you go:\nHello world.", "Hello world."),
        ("  Hello world.  \n", "Hello world."),
    ],
)
def test_sanitize_response(raw, expected):
    assert sanitize_response(raw, "orig") == expected


def test_sanitize_keeps_inner_quotes_pair():
    assert sanitize_response('"a" and "b"', "orig") == '"a" and "b"'


def test_sanitize_preamble_alone_not_stripped():
    assert sanitize_response("Cleaned text:", "orig") == "Cleaned text:"


@pytest.mark.parametrize("raw", ["", "   ", "``` ```", '""'])
def test_sanitize_empty_returns_original(raw):
    assert sanitize_response(raw, "the original") == "the original"


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_list_models_parses_and_sorts(monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        if "/api/v0/" in url:
            return FakeResp(404)
        seen["url"] = url
        seen["headers"] = headers
        return FakeResp(payload={"data": [{"id": "b"}, {"id": "a"}]})

    monkeypatch.setattr(cleanup.requests, "get", fake_get)
    assert list_models("http://x/v1/", "key") == ["a", "b"]
    assert seen["url"] == "http://x/v1/models"  # fallback after /api/v0 failed
    assert seen["headers"] == {"Authorization": "Bearer key"}


def test_list_models_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(cleanup.requests, "get", boom)
    assert list_models("http://x/v1") == []


def test_list_models_bad_payload_returns_empty(monkeypatch):
    monkeypatch.setattr(cleanup.requests, "get", lambda *a, **k: FakeResp(payload={"nope": 1}))
    assert list_models("http://x/v1") == []


def _cleaner(api_key=None):
    return OpenAICompatCleaner("http://x/v1/", api_key, "m", 5, "PROMPT", "lmstudio/m")


def test_openai_clean_success(monkeypatch):
    c = _cleaner()
    calls = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResp(payload={"choices": [{"message": {"content": '"Hello, world."'}}]})

    monkeypatch.setattr(c._session, "post", fake_post)
    assert c.clean("um hello world") == "Hello, world."
    assert calls["url"] == "http://x/v1/chat/completions"
    assert "Authorization" not in calls["headers"]
    assert calls["json"]["temperature"] == 0
    assert calls["json"]["stream"] is False
    assert calls["json"]["model"] == "m"
    assert calls["json"]["messages"][1]["content"].startswith("<transcript>")


def test_openai_clean_sends_auth_when_key(monkeypatch):
    c = _cleaner("sk-1")
    calls = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["headers"] = headers
        return FakeResp(payload={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(c._session, "post", fake_post)
    c.clean("hi")
    assert calls["headers"]["Authorization"] == "Bearer sk-1"


def test_openai_clean_error_status(monkeypatch):
    c = _cleaner()
    monkeypatch.setattr(c._session, "post", lambda *a, **k: FakeResp(500, text="server exploded"))
    with pytest.raises(CleanupError) as ei:
        c.clean("hi")
    assert "500" in str(ei.value) and "server exploded" in str(ei.value)


def test_openai_clean_network_error(monkeypatch):
    c = _cleaner()

    def boom(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(c._session, "post", boom)
    with pytest.raises(CleanupError):
        c.clean("hi")


def test_openai_clean_cancelled(monkeypatch):
    c = _cleaner()

    def fake_post(*a, **k):
        c.cancel()
        return FakeResp(payload={"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setattr(c._session, "post", fake_post)
    with pytest.raises(CleanupError, match="cancelled"):
        c.clean("hi")


@pytest.fixture
def no_keys(monkeypatch):
    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LMSTUDIO_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_make_cleaner_none_cases(no_keys):
    assert make_cleaner(CleanupConfig(provider="none")) is None
    assert make_cleaner(CleanupConfig(enabled=False)) is None
    for p in ("groq", "openai", "anthropic"):
        assert make_cleaner(CleanupConfig(provider=p)) is None


def test_make_cleaner_empty_key_is_missing(no_keys, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    assert make_cleaner(CleanupConfig(provider="groq")) is None


def test_make_cleaner_names(no_keys, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g")
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert make_cleaner(CleanupConfig(provider="groq")).name == "groq/llama-3.3-70b-versatile"
    assert make_cleaner(CleanupConfig(provider="openai")).name == "openai/gpt-4o-mini"
    assert make_cleaner(CleanupConfig(provider="anthropic", model="claude-x")).name == "anthropic/claude-x"


def test_make_cleaner_lmstudio_model_discovery(no_keys, monkeypatch):
    def md(*ids_states):
        return lambda *a, **k: [{"id": i, "type": "llm", "state": s} for i, s in ids_states]

    monkeypatch.setattr(cleanup, "list_models_detailed",
                        md(("big", "not-loaded"), ("qwen", "loaded"), ("zed", "loaded")))
    assert make_cleaner(CleanupConfig(provider="lmstudio")).name == "lmstudio/qwen"
    assert make_cleaner(CleanupConfig(provider="lmstudio", model="mine")).name == "lmstudio/mine"
    monkeypatch.setattr(cleanup, "list_models_detailed", md(("big", "not-loaded")))
    assert make_cleaner(CleanupConfig(provider="lmstudio")) is None
    assert make_cleaner(CleanupConfig(provider="lmstudio", model="big")).name == "lmstudio/big"
    monkeypatch.setattr(cleanup, "list_models_detailed", lambda *a, **k: [])
    assert make_cleaner(CleanupConfig(provider="lmstudio")) is None


def test_connection_error_is_unavailable(monkeypatch):
    c = _cleaner()

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(c._session, "post", boom)
    with pytest.raises(cleanup.CleanupUnavailable) as ei:
        c.clean("hi")
    assert "http://x/v1" in str(ei.value)
    assert isinstance(ei.value, CleanupError)


def test_connect_timeout_is_unavailable_but_read_timeout_is_not(monkeypatch):
    c = _cleaner()
    monkeypatch.setattr(c._session, "post", lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectTimeout("t")))
    with pytest.raises(cleanup.CleanupUnavailable):
        c.clean("hi")
    monkeypatch.setattr(c._session, "post", lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ReadTimeout("t")))
    with pytest.raises(CleanupError) as ei:
        c.clean("hi")
    assert not isinstance(ei.value, cleanup.CleanupUnavailable)


def test_anthropic_connection_error_is_unavailable(monkeypatch):
    c = cleanup.AnthropicCleaner("k", "", 5, "")
    monkeypatch.setattr(c._session, "post", lambda *a, **k: (_ for _ in ()).throw(
        requests.exceptions.ConnectionError("x")))
    with pytest.raises(cleanup.CleanupUnavailable):
        c.clean("hi")


def test_http_error_is_plain_cleanup_error(monkeypatch):
    c = _cleaner()
    monkeypatch.setattr(c._session, "post", lambda *a, **k: FakeResp(500, text="x"))
    with pytest.raises(CleanupError) as ei:
        c.clean("hi")
    assert not isinstance(ei.value, cleanup.CleanupUnavailable)


def test_post_uses_connect_read_timeout(monkeypatch):
    c = _cleaner()
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["t"] = timeout
        return FakeResp(payload={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(c._session, "post", fake_post)
    c.clean("hi")
    assert seen["t"] == (5, 5)


LMS_PAYLOAD = {"data": [
    {"id": "emb", "type": "embeddings", "state": "not-loaded", "extra": 1},
    {"id": "a-big", "type": "llm", "state": "not-loaded"},
    {"id": "b-vlm", "type": "vlm", "state": "loaded"},
    {"id": "c-llm", "type": "llm", "state": "loaded"},
]}


def test_list_models_detailed_lmstudio(monkeypatch):
    urls = []

    def fake_get(url, headers=None, timeout=None):
        urls.append(url)
        return FakeResp(payload=LMS_PAYLOAD)

    monkeypatch.setattr(cleanup.requests, "get", fake_get)
    res = cleanup.list_models_detailed("http://127.0.0.1:1234/v1")
    assert urls == ["http://127.0.0.1:1234/api/v0/models"]
    assert [m["id"] for m in res] == ["b-vlm", "c-llm", "a-big"]
    assert res[0] == {"id": "b-vlm", "type": "vlm", "state": "loaded"}
    assert list_models("http://127.0.0.1:1234/v1/") == ["b-vlm", "c-llm", "a-big"]


def test_list_models_detailed_fallback(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        if "/api/v0/" in url:
            raise requests.ConnectionError("nope")
        return FakeResp(payload={"data": [{"id": "z"}, {"id": "y"}]})

    monkeypatch.setattr(cleanup.requests, "get", fake_get)
    assert cleanup.list_models_detailed("http://h/v1") == [
        {"id": "y", "type": "llm", "state": "unknown"},
        {"id": "z", "type": "llm", "state": "unknown"},
    ]


def test_list_models_detailed_total_failure(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(cleanup.requests, "get", boom)
    assert cleanup.list_models_detailed("http://h/v1") == []


def _ok(content="ok", **extra):
    payload = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    payload.update(extra)
    return FakeResp(payload=payload)


def test_extra_body_merged(monkeypatch):
    c = OpenAICompatCleaner("http://x/v1", None, "m", 5, "P", "n",
                            extra_body={"reasoning_effort": "none"})
    seen = []
    monkeypatch.setattr(c._session, "post",
                        lambda url, headers=None, json=None, timeout=None: seen.append(dict(json)) or _ok())
    c.clean("hi")
    assert seen[0]["reasoning_effort"] == "none"


def test_make_cleaner_extra_body_per_provider(no_keys, monkeypatch):
    monkeypatch.setattr(cleanup, "list_models_detailed", lambda *a, **k: [{"id": "m", "type": "llm", "state": "loaded"}])
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert make_cleaner(CleanupConfig(provider="lmstudio")).extra_body == {"reasoning_effort": "none"}
    assert make_cleaner(CleanupConfig(provider="openai")).extra_body is None
    assert make_cleaner(CleanupConfig(provider="groq")).extra_body is None


def test_400_retries_without_extra_body_once(monkeypatch):
    c = OpenAICompatCleaner("http://x/v1", None, "m", 5, "P", "n",
                            extra_body={"reasoning_effort": "none"})
    seen = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append("reasoning_effort" in json)
        return FakeResp(400, text="bad") if "reasoning_effort" in json else _ok("Fine.")

    monkeypatch.setattr(c._session, "post", fake_post)
    assert c.clean("hi") == "Fine."
    assert seen == [True, False]
    assert c._extra_ok is False
    assert c.clean("hi") == "Fine."
    assert seen == [True, False, False]


def test_400_without_extra_body_is_error(monkeypatch):
    c = _cleaner()
    monkeypatch.setattr(c._session, "post", lambda *a, **k: FakeResp(400, text="bad"))
    with pytest.raises(CleanupError):
        c.clean("hi")


def test_empty_content_warns_and_falls_back(monkeypatch, caplog):
    c = _cleaner()
    resp = FakeResp(payload={
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {"completion_tokens_details": {"reasoning_tokens": 120}},
    })
    monkeypatch.setattr(c._session, "post", lambda *a, **k: resp)
    with caplog.at_level("WARNING", logger="vibeflow.cleanup"):
        assert c.clean("original text") == "original text"
    assert "finish_reason=length" in caplog.text and "reasoning_tokens=120" in caplog.text


def test_sanitize_strips_think_block():
    assert sanitize_response("<think>\nhmm\n</think>\nHello.", "o") == "Hello."


def test_max_tokens_floor_and_cap():
    assert cleanup._max_tokens("hi") == 256
    assert cleanup._max_tokens("w " * 5000) == 4096
