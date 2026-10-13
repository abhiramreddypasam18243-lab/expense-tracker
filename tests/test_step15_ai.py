"""Step 15 — Ask your CA: local AI chat through Ollama (AC-78 to AC-82)."""

import json
import re
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from expenses import ai, ca, tax
from expenses.money import format_inr

ROOT = Path(__file__).resolve().parent.parent


# --- fake Ollama ----------------------------------------------------------------------------------


class FakeOllama:
    def __init__(self, models=("qwen3:4b-instruct",), thinking_models=("thinker",)):
        self.models = list(models)
        self.thinking_models = set(thinking_models)
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _lines(self, status, lines):
                self.send_response(status)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for line in lines:
                    self.wfile.write((json.dumps(line) + "\n").encode())
                    self.wfile.flush()

            def do_GET(self):
                if self.path == "/api/tags":
                    body = json.dumps({"models": [{"name": m} for m in fake.models]}).encode()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, **payload})
                if self.path == "/api/show":
                    caps = ["completion"] + (["thinking"] if payload["model"] in fake.thinking_models else [])
                    body = json.dumps({"capabilities": caps}).encode()
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/api/pull":
                    fake.models.append(payload["model"])
                    self._lines(
                        200,
                        [
                            {"status": "pulling manifest"},
                            {"status": "downloading", "total": 100, "completed": 40},
                            {"status": "downloading", "total": 100, "completed": 100},
                            {"status": "success"},
                        ],
                    )
                elif self.path == "/api/chat":
                    if payload["model"] == "broken":
                        self._lines(400, [{"error": "model 'broken' not found"}])
                        return
                    chunks = ["<think>let me", " think</think>", "Choose the ", "new regime."]
                    lines = [{"message": {"role": "assistant", "content": c}, "done": False} for c in chunks]
                    if payload.get("think"):  # Ollama returns thinking in its own field when asked to
                        lines.insert(
                            0, {"message": {"role": "assistant", "content": "", "thinking": "SECRET"}}
                        )
                    self._lines(
                        200, lines + [{"message": {"role": "assistant", "content": ""}, "done": True}]
                    )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    server = FakeOllama()
    yield server
    server.close()


# --- AC-78 local only -----------------------------------------------------------------------------


def test_ac_78_local_urls_only(monkeypatch):
    for url in (
        "http://ollama:11434",
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
    ):
        assert ai.is_local_url(url), url
    for url in (
        "https://api.openai.com",
        "http://example.com:11434",
        "http://192.168.1.20:11434",
        "http://ollama.evil.com:11434",
        "ftp://localhost",
        "",
    ):
        assert not ai.is_local_url(url), url
    monkeypatch.delenv("EXPENSES_AI_URL", raising=False)
    assert ai.config().url is None
    monkeypatch.setenv("EXPENSES_AI_URL", "https://api.example.com")
    assert ai.config().url is None
    monkeypatch.setenv("EXPENSES_AI_URL", "http://ollama:11434")
    monkeypatch.setenv("EXPENSES_AI_MODEL", "llama3.2:3b")
    assert (ai.config().url, ai.config().model) == ("http://ollama:11434", "llama3.2:3b")
    monkeypatch.delenv("EXPENSES_AI_MODEL")
    assert ai.config().model == ai.DEFAULT_MODEL == "qwen3:4b-instruct"


# --- AC-79 context --------------------------------------------------------------------------------


def test_ac_79_context_contains_the_ca_figures():
    inputs = tax.TaxInputs(tax_year="2026-27", salary_minor=18_00_000_00, sec80c_minor=84_000_00)
    comparison = tax.compare(inputs)
    items = [ca.TaxItem("life_insurance", "Life insurance premiums", "s.123 (old 80C)", 24_000_00, [])]
    calendar = ca.filing_calendar("2026-27", date(2026, 10, 13))
    text = ai.build_context(inputs, comparison, items, calendar, ca.checklist(items, "2026-27"))
    for needle in (
        "2026-27",
        "New regime",
        "Old regime",
        format_inr(comparison.new.total_tax_minor),
        format_inr(comparison.old.total_tax_minor),
        format_inr(comparison.new.taxable_minor),
        "recommended",
        format_inr(comparison.saving_minor),
        "s.123 (old 80C)",
        "₹84,000.00",
        "Life insurance premiums",
        "₹24,000.00",
        "15 Dec 2026",
    ):
        assert needle in text, needle


# --- AC-80 client against a fake server -----------------------------------------------------------


def test_ac_80_status_pull_and_ask(fake):
    status = ai.status(fake.url, "qwen3:4b-instruct")
    assert status.reachable and status.has_model
    assert not ai.status(fake.url, "other:1b").has_model

    progress = list(ai.pull(fake.url, "other:1b"))
    assert progress[-1].done and progress[1].fraction == 0.4
    assert ai.status(fake.url, "other:1b").has_model

    answer = "".join(
        ai.ask(fake.url, "qwen3:4b-instruct", [{"role": "user", "content": "Hi"}], "Which regime?", "CTX-123")
    )
    assert answer == "Choose the new regime."
    sent = fake.requests[-1]
    assert sent["model"] == "qwen3:4b-instruct" and sent["stream"] is True and "think" not in sent
    assert sent["messages"][0]["role"] == "system" and "CTX-123" in sent["messages"][0]["content"]
    assert [m["content"] for m in sent["messages"][1:]] == ["Hi", "Which regime?"]

    # A thinking model: ask for separate thinking, and never show it.
    thinking_answer = "".join(ai.ask(fake.url, "thinker", [], "Which regime?", "ctx"))
    assert fake.requests[-1]["think"] is True
    assert thinking_answer == "Choose the new regime." and "SECRET" not in thinking_answer

    with pytest.raises(ai.AIError, match="not found"):
        "".join(ai.ask(fake.url, "broken", [], "x", "ctx"))


def test_ac_80_unreachable_is_quick():
    start = time.monotonic()
    status = ai.status("http://127.0.0.1:9", "qwen3:4b")  # nothing listens on port 9
    assert not status.reachable and time.monotonic() - start < 5


def test_ac_80_think_tags_split_across_chunks():
    chunks = ["Ans", "wer <thi", "nk>hidden</th", "ink> done"]
    assert "".join(ai.strip_thinking(iter(chunks))) == "Answer  done"
    assert "".join(ai.strip_thinking(iter(["Fine.</thi", "nk> Next"]))) == "Fine. Next"  # stray close tag


# --- AC-81 the chat tab ---------------------------------------------------------------------------


def ca_page(monkeypatch, tmp_path, ai_url=None, web=False):
    monkeypatch.setenv("EXPENSES_DB", str(tmp_path / "ai.db"))
    monkeypatch.setenv("EXPENSES_DEMO", "1" if web else "0")
    for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_TLS_PROXY", "EXPENSES_AI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    if ai_url:
        monkeypatch.setenv("EXPENSES_AI_URL", ai_url)
    else:
        monkeypatch.delenv("EXPENSES_AI_URL", raising=False)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.switch_page("ui/ca.py").run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def page_text(at):
    return "\n".join(
        [m.value for m in at.markdown]
        + [c.value for c in at.caption]
        + [i.value for i in at.info]
        + [w.value for w in at.warning]
    )


def test_ac_81_no_url_shows_setup(monkeypatch, tmp_path):
    at = ca_page(monkeypatch, tmp_path)
    assert "Ask your CA" in page_text(at) and "EXPENSES_AI_URL" in page_text(at)
    assert len(at.chat_input) == 0


def test_ac_81_web_mode_is_desktop_only(monkeypatch, tmp_path, fake):
    at = ca_page(monkeypatch, tmp_path, ai_url=fake.url, web=True)
    assert re.search(r"desktop", page_text(at), re.I) and len(at.chat_input) == 0


def test_ac_81_download_then_chat(monkeypatch, tmp_path):
    server = FakeOllama(models=())
    try:
        at = ca_page(monkeypatch, tmp_path, ai_url=server.url)
        assert len(at.chat_input) == 0
        at.button(key="ai_pull").click().run()
        assert not at.exception
        assert len(at.chat_input) == 1
        at.chat_input[0].set_value("Which regime should I choose?").run()
        assert not at.exception
        replies = [m for m in at.chat_message if m.name == "assistant"]
        assert replies and "Choose the new regime." in "".join(md.value for md in replies[-1].markdown)
        assert "CA figures" in server.requests[-1]["messages"][0]["content"]
    finally:
        server.close()


# --- AC-82 compose and network isolation ----------------------------------------------------------


def test_ac_82_compose_runs_ollama_privately():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    service = text.split("  ollama:", 1)[1].split("\nvolumes:", 1)[0]
    assert "image: ollama/ollama:0.40.3" in service
    assert "ports:" not in service  # no published ports: only the app can reach it
    assert "ollama-models:/root/.ollama" in service
    assert "EXPENSES_AI_URL: http://ollama:11434" in text
    assert "\n  ollama-models:" in text.split("\nvolumes:", 1)[1]
    ai_source = (ROOT / "expenses" / "ai.py").read_text(encoding="utf-8")
    hosts = set(re.findall(r"https?://([^/\"'\s]+)", ai_source))
    assert hosts <= {"ollama:11434", "localhost:11434"}
