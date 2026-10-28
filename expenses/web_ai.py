"""Ask your CA on the web version: a free hosted model through Groq (spec section 6.7).

The hosted site can't run Ollama (Streamlit Community Cloud has about 1 GB of memory), so in web
mode the chat uses Groq's free tier instead. It contacts ONLY api.groq.com, and only after the
visitor has agreed. It sends the same "CA figures" block as the desktop chat (computed totals,
never statement rows, descriptions or merchants). The API key comes from the GROQ_API_KEY
environment variable (a Streamlit Cloud secret); it is never logged or shown. Chat content is
never logged.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass

from expenses import ai

API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-20b"  # free tier, highest daily request limit (checked 2026-10-28)
PROVIDER = "Groq"
TIMEOUT = 60

# Per-visitor limits: keep the shared free quota available for everyone.
MAX_QUESTIONS = 20  # per browser session
MAX_QUESTION_CHARS = 500
MAX_HISTORY = 6  # earlier messages sent along with a question
MAX_ANSWER_TOKENS = 1024  # includes the model's hidden reasoning


@dataclass(frozen=True)
class WebAIConfig:
    key: str | None  # None = not set up by the site owner
    model: str


def config() -> WebAIConfig:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    model = os.environ.get("EXPENSES_WEB_AI_MODEL", "").strip() or DEFAULT_MODEL
    return WebAIConfig(key=key or None, model=model)


def _error(status: int, raw: bytes) -> ai.AIError:
    if status == 429:
        return ai.AIError("the free AI limit was reached for now; please try again in a minute")
    if status in (401, 403):
        return ai.AIError("the site's AI key was refused; the site owner needs to update it")
    try:
        detail = json.loads(raw)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        detail = f"HTTP {status}"
    return ai.AIError(detail)


def payload(model: str, history: list[dict], question: str, context: str) -> dict:
    """The request body: system prompt with the CA figures, the recent chat, and the question."""
    messages = [{"role": "system", "content": ai.SYSTEM_PROMPT.format(context=context)}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history[-MAX_HISTORY:]]
    messages.append({"role": "user", "content": question[:MAX_QUESTION_CHARS]})
    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
        "max_completion_tokens": MAX_ANSWER_TOKENS,
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # quicker answers; the reasoning itself is never shown
    return body


def ask(key: str, model: str, history: list[dict], question: str, context: str) -> Iterator[str]:
    """Stream the answer. Only the answer text is yielded, never the model's reasoning."""
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload(model, history, question, context)).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "expense-tracker",
        },
    )

    def chunks() -> Iterator[str]:
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                for raw in response:
                    line = raw.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        return
                    message = json.loads(data)
                    if "error" in message:
                        raise ai.AIError(message["error"].get("message", "unknown error"))
                    for choice in message.get("choices", []):
                        yield choice.get("delta", {}).get("content") or ""  # delta.reasoning is ignored
        except urllib.error.HTTPError as exc:
            raise _error(exc.code, exc.read()) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise ai.AIError("the AI service is not reachable right now") from exc

    yield from ai.strip_thinking(chunks())
