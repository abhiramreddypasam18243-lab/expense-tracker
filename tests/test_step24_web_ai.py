"""Step 24 — Ask your CA on the web version: free hosted model through Groq (AC-125 to AC-129)."""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import ai, web_ai

ROOT = Path(__file__).resolve().parent.parent
KEY = "gsk_test_SECRET_123"


# --- fake Groq (OpenAI-compatible chat completions, server-sent events) ---------------------------


class FakeGroq:
    def __init__(self, status=200):
        self.status = status
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"auth": self.headers.get("Authorization"), **body})
                if fake.status != 200:
                    self.send_response(fake.status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": "nope"}}).encode())
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                deltas = [
                    {"role": "assistant", "content": ""},
                    {"reasoning": "SECRET REASONING"},
                    {"content": "<think>hmm</think>Choose the "},
                    {"content": "new regime."},
                ]
                for delta in deltas:
                    chunk = {"choices": [{"index": 0, "delta": delta}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/openai/v1/chat/completions"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def groq(monkeypatch):
    server = FakeGroq()
    monkeypatch.setattr(web_ai, "API_URL", server.url)
    yield server
    server.close()


# --- AC-125 configuration and the one allowed host ------------------------------------------------


def test_ac_125_key_and_model_from_environment(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("EXPENSES_WEB_AI_MODEL", raising=False)
    assert web_ai.config() == web_ai.WebAIConfig(key=None, model="openai/gpt-oss-20b")
    monkeypatch.setenv("GROQ_API_KEY", f"  {KEY} ")
    monkeypatch.setenv("EXPENSES_WEB_AI_MODEL", "openai/gpt-oss-120b")
    assert web_ai.config() == web_ai.WebAIConfig(key=KEY, model="openai/gpt-oss-120b")


def test_ac_125_only_contacts_groq():
    source = (ROOT / "expenses" / "web_ai.py").read_text(encoding="utf-8")
    assert set(re.findall(r"https?://([^/\"'\s]+)", source)) == {"api.groq.com"}
    assert "logging" not in source and "print(" not in source  # neither the key nor the chat is logged


# --- AC-126 what is sent -----------------------------------------------------------------------------


def test_ac_126_payload_is_grounded_and_bounded():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(10)]
    body = web_ai.payload("openai/gpt-oss-20b", history, "x" * 2000, "CA figures: total tax ₹0")
    messages = body["messages"]
    assert messages[0]["role"] == "system" and "CA figures: total tax ₹0" in messages[0]["content"]
    assert [m["content"] for m in messages[1:-1]] == [f"m{i}" for i in range(4, 10)]  # last 6 only
    assert messages[-1] == {"role": "user", "content": "x" * web_ai.MAX_QUESTION_CHARS}
    assert body["stream"] is True and body["max_completion_tokens"] == web_ai.MAX_ANSWER_TOKENS
    assert body["reasoning_effort"] == "low"
    assert "reasoning_effort" not in web_ai.payload("some/other-model", [], "q", "c")


# --- AC-127 streaming and errors ------------------------------------------------------------------


def test_ac_127_streams_only_the_answer(groq):
    answer = "".join(web_ai.ask(KEY, "openai/gpt-oss-20b", [], "Which regime?", "CA figures"))
    assert answer == "Choose the new regime."
    assert groq.requests[-1]["auth"] == f"Bearer {KEY}"
    assert groq.requests[-1]["messages"][-1]["content"] == "Which regime?"


@pytest.mark.parametrize(
    ("status", "words"), [(429, "limit"), (401, "site owner"), (403, "site owner"), (400, "nope")]
)
def test_ac_127_errors_are_friendly_and_hide_the_key(monkeypatch, status, words):
    server = FakeGroq(status=status)
    monkeypatch.setattr(web_ai, "API_URL", server.url)
    try:
        with pytest.raises(ai.AIError) as err:
            "".join(web_ai.ask(KEY, "openai/gpt-oss-20b", [], "q", "c"))
        assert words in str(err.value) and KEY not in str(err.value)
    finally:
        server.close()


def test_ac_127_unreachable(monkeypatch):
    monkeypatch.setattr(web_ai, "API_URL", "http://127.0.0.1:9/openai/v1/chat/completions")
    with pytest.raises(ai.AIError, match="not reachable"):
        "".join(web_ai.ask(KEY, "m", [], "q", "c"))


# --- AC-128 / AC-129 the chat tab in web mode ----------------------------------------------------


def web_ca_page(monkeypatch, tmp_path, key=KEY):
    monkeypatch.setenv("EXPENSES_DB", str(tmp_path / "web.db"))
    monkeypatch.setenv("EXPENSES_DEMO", "1")
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_TLS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    for name in ("EXPENSES_WEB_AI_MODEL",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EXPENSES_AI_URL", "http://127.0.0.1:9")  # set, but web mode must not use Ollama
    if key:
        monkeypatch.setenv("GROQ_API_KEY", key)
    else:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.switch_page("ui/ca.py").run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def text_of(at):
    return "\n".join(
        [m.value for m in at.markdown] + [c.value for c in at.caption] + [i.value for i in at.info]
    )


def assistant_text(at):
    replies = [m for m in at.chat_message if m.name == "assistant"]
    return "".join(md.value for md in replies[-1].markdown) if replies else ""


def test_ac_128_no_key_explains_setup(monkeypatch, tmp_path):
    at = web_ca_page(monkeypatch, tmp_path, key=None)
    assert "GROQ_API_KEY" in text_of(at) and len(at.chat_input) == 0
    assert KEY not in text_of(at)


def test_ac_128_consent_then_chat(monkeypatch, tmp_path, groq):
    at = web_ca_page(monkeypatch, tmp_path)
    assert "Groq" in text_of(at) and "Never your statement rows" in text_of(at)
    assert len(at.chat_input) == 0 and not groq.requests  # nothing is sent before the visitor agrees
    assert KEY not in text_of(at)
    at.checkbox(key="ai_web_consent").check().run()
    assert len(at.chat_input) == 1
    at.chat_input[0].set_value("Which regime should I choose?").run()
    assert not at.exception, [e.value for e in at.exception]
    assert "Choose the new regime." in assistant_text(at)
    assert "SECRET" not in assistant_text(at)
    sent = groq.requests[-1]["messages"][0]["content"]
    assert "CA figures" in sent
    assert "Swiggy" not in sent and "AMAZON" not in sent.upper()  # demo statement merchants never sent


def test_ac_129_question_limit_per_visit(monkeypatch, tmp_path, groq):
    monkeypatch.setattr(web_ai, "MAX_QUESTIONS", 2)
    at = web_ca_page(monkeypatch, tmp_path)
    at.checkbox(key="ai_web_consent").check().run()
    for q in ("one", "two"):
        at.chat_input[0].set_value(q).run()
    assert len(groq.requests) == 2
    assert len(at.chat_input) == 0 and "questions available per visit" in text_of(at)
