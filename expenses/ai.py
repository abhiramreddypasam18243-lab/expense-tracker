"""Ask your CA: chat with a free model running locally in Ollama (spec section 6.6).

Talks ONLY to a local Ollama service (the compose service "ollama", localhost or a loopback
address). The model gets the CA calculator's figures as context and is told to use only those
numbers; it explains, it never does the tax maths. Chat content is never logged.
"""

from __future__ import annotations

import ipaddress
import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

from expenses import tax
from expenses.money import format_inr

DEFAULT_MODEL = "qwen3:4b-instruct"  # same size as qwen3:4b, but no slow "thinking" step
LOCAL_HOSTS = {"localhost", "ollama"}  # compose service name: e.g. http://ollama:11434
STATUS_TIMEOUT = 2
CHAT_TIMEOUT = 300  # seconds between streamed chunks; a 4B model on CPU can be slow to start

SYSTEM_PROMPT = """\
You are "Your CA", a careful assistant for an individual taxpayer in India, \
inside a private expense-tracker app.

Rules:
- Answer in simple English and keep it short (under about 150 words unless \
the user asks for detail).
- For every number about the user's income, tax or deductions, use ONLY the \
figures in the "CA figures" block below. Never recalculate, estimate or invent \
numbers. If a figure you need is missing, say which field the user should fill \
in on the Your CA page.
- Use the section names given for the tax year (for 2026-27 the Income-tax Act, \
2025 numbering, e.g. s.123 for the old 80C).
- You may explain tax concepts in general: the two regimes, deductions, \
deadlines and documents.
- Don't present any investment product as a certain recommendation.
- End answers that involve a decision with: "This is guidance, not professional advice."

{context}"""


class AIError(Exception):
    """The local model could not answer (not running, model missing, or an error reply)."""


@dataclass(frozen=True)
class AIConfig:
    url: str | None  # None = AI chat switched off
    model: str


@dataclass(frozen=True)
class Status:
    reachable: bool
    has_model: bool
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class PullProgress:
    status: str
    completed: int = 0
    total: int = 0
    done: bool = False

    @property
    def fraction(self) -> float | None:
        """Download progress for a progress bar (display only)."""
        return self.completed / self.total if self.total else None


# --- configuration ------------------------------------------------------------------------------


def is_local_url(url: str) -> bool:
    """Only the local Ollama service is allowed: 'ollama', 'localhost' or a loopback IP."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if parsed.hostname in LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def config() -> AIConfig:
    url = os.environ.get("EXPENSES_AI_URL", "").strip().rstrip("/")
    model = os.environ.get("EXPENSES_AI_MODEL", "").strip() or DEFAULT_MODEL
    return AIConfig(url=url if url and is_local_url(url) else None, model=model)


# --- HTTP helpers -----------------------------------------------------------------------------------


def _check(url: str) -> None:
    if not is_local_url(url):
        raise AIError("refusing to contact a non-local AI service")


def _error_message(raw: bytes) -> str:
    try:
        return json.loads(raw).get("error") or raw.decode(errors="replace")
    except (ValueError, AttributeError):
        return raw.decode(errors="replace") or "unknown error"


def _stream(url: str, path: str, payload: dict, timeout: float) -> Iterator[dict]:
    """POST and yield each JSON line of Ollama's streamed reply."""
    _check(url)
    request = urllib.request.Request(
        url + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for line in response:
                if not line.strip():
                    continue
                message = json.loads(line)
                if "error" in message:
                    raise AIError(message["error"])
                yield message
    except urllib.error.HTTPError as exc:
        raise AIError(_error_message(exc.read())) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise AIError("the local AI service is not reachable") from exc


# --- public API ------------------------------------------------------------------------------------------


def status(url: str, model: str) -> Status:
    """Is Ollama running, and is the model downloaded? Never raises."""
    if not is_local_url(url):
        return Status(False, False)
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=STATUS_TIMEOUT) as response:
            names = tuple(m["name"] for m in json.load(response).get("models", []))
    except (OSError, ValueError, KeyError):
        return Status(False, False)
    wanted = {model, f"{model}:latest"}
    return Status(True, any(n in wanted for n in names), names)


def pull(url: str, model: str) -> Iterator[PullProgress]:
    """Download a model into Ollama (one time), reporting progress."""
    for message in _stream(url, "/api/pull", {"model": model, "stream": True}, timeout=CHAT_TIMEOUT):
        state = message.get("status", "")
        yield PullProgress(state, message.get("completed", 0), message.get("total", 0), state == "success")


def strip_thinking(chunks: Iterable[str]) -> Iterator[str]:
    """Remove <think>...</think> blocks (and stray tags) from a stream, even when split across chunks."""
    open_tag, close_tag = "<think>", "</think>"
    buffer, inside = "", False
    for chunk in chunks:
        buffer += chunk
        while True:
            if not inside:
                start, stray = buffer.find(open_tag), buffer.find(close_tag)
                if stray >= 0 and (start < 0 or stray < start):  # a closing tag without an opening one
                    if stray:
                        yield buffer[:stray]
                    buffer = buffer[stray + len(close_tag) :]
                    continue
                if start >= 0:
                    if start:
                        yield buffer[:start]
                    buffer, inside = buffer[start + len(open_tag) :], True
                    continue
                # Hold back an ending that could be the start of either tag ("<", "</th", ...).
                keep = next(
                    (
                        k
                        for k in range(len(close_tag) - 1, 0, -1)
                        if buffer.endswith(open_tag[:k]) or buffer.endswith(close_tag[:k])
                    ),
                    0,
                )
                if len(buffer) > keep:
                    yield buffer[: len(buffer) - keep]
                buffer = buffer[len(buffer) - keep :]
                break
            end = buffer.find(close_tag)
            if end >= 0:
                buffer, inside = buffer[end + len(close_tag) :], False
                continue
            buffer = buffer[-(len(close_tag) - 1) :]  # might be the start of </think>
            break
    if buffer and not inside:
        yield buffer


def capabilities(url: str, model: str) -> set[str]:
    """What the model can do, e.g. {'completion', 'thinking'}. Empty if unknown."""
    _check(url)
    request = urllib.request.Request(
        url + "/api/show",
        data=json.dumps({"model": model}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=STATUS_TIMEOUT) as response:
            return set(json.load(response).get("capabilities", []))
    except (OSError, ValueError):
        return set()


def ask(url: str, model: str, history: list[dict], question: str, context: str) -> Iterator[str]:
    """Stream the model's answer. history: earlier [{"role", "content"}] turns of this chat.

    Thinking models are asked for *separate* thinking ("think": true), which Ollama returns in
    message.thinking — never shown. Other models get no "think" setting at all.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(context=context)}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": question})
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    if "thinking" in capabilities(url, model):
        payload["think"] = True

    def chunks() -> Iterator[str]:
        for message in _stream(url, "/api/chat", payload, timeout=CHAT_TIMEOUT):
            yield message.get("message", {}).get("content", "")  # message.thinking is ignored

    yield from strip_thinking(chunks())


# --- context from the CA's results ----------------------------------------------------------------------


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def build_context(
    inputs: tax.TaxInputs, comparison: tax.Comparison, items: list, calendar: list, checklist: list[str]
) -> str:
    """Plain-text 'CA figures' block: everything the model may quote, already computed."""
    names = tax.SECTIONS[inputs.tax_year]
    new, old = comparison.new, comparison.old
    lines = [
        "CA figures (computed by the app's tax calculator; use only these numbers)",
        f"Tax year: {inputs.tax_year}. Age group: {tax.AGE_GROUPS[inputs.age]}.",
        f"Income entered: gross salary {format_inr(inputs.salary_minor)}; "
        f"other income {format_inr(inputs.other_income_minor)}.",
        f"New regime ({names['new_regime']}): standard deduction {format_inr(new.standard_deduction_minor)}, "
        f"taxable income {format_inr(new.taxable_minor)}, total tax {format_inr(new.total_tax_minor)} "
        "(after rebate, incl. 4% cess).",
        f"Old regime: standard deduction {format_inr(old.standard_deduction_minor)}, "
        f"HRA exemption {format_inr(old.hra_exemption_minor)}, "
        f"taxable income {format_inr(old.taxable_minor)}, "
        f"total tax {format_inr(old.total_tax_minor)}.",
    ]
    used = [f"{names[key]} {format_inr(amount)}" for key, amount in old.deductions.items() if amount]
    lines.append("Old-regime deductions used (after limits): " + (", ".join(used) if used else "none") + ".")
    if comparison.saving_minor:
        best = "New regime" if comparison.best == "new" else "Old regime"
        lines.append(f"Result: {best} is recommended; it saves {format_inr(comparison.saving_minor)}.")
    else:
        lines.append("Result: both regimes give the same tax.")
    if comparison.best_result.surcharge_excluded:
        lines.append("Note: taxable income is above ₹50 lakh; surcharge is not included in these figures.")
    for tip in comparison.tips:
        lines.append(f"Tip: {tip.text}")
    if items:
        lines.append("Tax-relevant transactions found in the bank statements for this tax year:")
        lines += [
            f"- {i.label} ({i.section}): {format_inr(i.total_minor)} in {len(i.rows)} transaction(s)"
            for i in items
        ]
    else:
        lines.append("Tax-relevant transactions found: none.")
    upcoming = [d for d in calendar if d.status != "past"][:4]
    if upcoming:
        lines.append("Upcoming deadlines:")
        lines += [f"- {_day(d.day)}: {d.title}" + (" (next)" if d.status == "next" else "") for d in upcoming]
    lines.append("Documents to collect: " + "; ".join(checklist) + ".")
    return "\n".join(lines)
