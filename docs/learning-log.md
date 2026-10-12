# Learning log

## Step 1 — Walking skeleton (AC-38, AC-39, AC-40) — 2026-10-12

**What was built**
- Project layout: `expenses/` (core, empty for now), `tools/make_synthetic.py`, `tests/`, `app.py` (a "hello" Streamlit page).
- `Dockerfile` (python:3.12-slim, non-root `appuser`, HEALTHCHECK on `/_stcore/health`, usage stats off) and `docker-compose.yml` (published on `127.0.0.1:8501` only, named volume for `/app/data`).
- `requirements.txt` (runtime) + `requirements-dev.txt` (test/lint), `pyproject.toml` (pytest + ruff config), `.gitignore` / `.dockerignore` that keep data, DBs, CSVs and `.env` out of git and the image.
- Synthetic statement generator with two layouts (`amount`, `debit_credit`), seeded and deterministic, integer-paise only.

**Why it is designed this way**
- *Walking skeleton first*: the full path (code → image → container → browser) works on day 1, so every later step is just "add a feature and rebuild" instead of a scary integration at the end.
- *0.0.0.0 inside, 127.0.0.1 outside*: inside a container, binding to 127.0.0.1 would make the app unreachable even from the host. So the container listens on all of *its own* interfaces, and Compose publishes the port only on the host's loopback. Example: `"8501:8501"` would expose your finances to everyone on the café Wi-Fi; `"127.0.0.1:8501:8501"` exposes them only to your own machine.
- *Non-root user*: if Streamlit had a bug that let someone run code, they'd be `appuser`, not `root`, inside the container.
- *Seeded generator*: `random.Random(42)` always gives the same sequence, so tests can rely on exact output, and no real data is ever needed. It also plants things later steps must handle: preamble lines before the header, two identical ₹20 teas on the same day (dedupe edge case), and two huge purchases (anomaly targets).

**One thing that could go wrong**
- The Docker build/health check (manual part of AC-40) has **not been run** — Docker is not installed on this machine. The static checks pass and the same app was verified locally (`/_stcore/health` → `ok`), but the image build itself is still unverified.

**Quiz questions**
1. Why does the container listen on `0.0.0.0` while the compose file uses `127.0.0.1:8501:8501`? What would happen if each were swapped?
2. Why is `*.csv` in both `.gitignore` and `.dockerignore`, and what kind of leak does each one prevent?
3. The generator uses `random.Random(seed)` instead of `random.seed(seed)`. Why does a private RNG instance make the tests more reliable?
