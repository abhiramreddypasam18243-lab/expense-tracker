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

## Step 2 — Money and dates (AC-01 to AC-12) — 2026-10-12

**What was built**
- `expenses/money.py`: `format_inr` (Indian grouping for display), `parse_amount` and `parse_debit_credit` (**[LEARN] I1** — written by Claude at the owner's request).
- `expenses/dates.py`: `parse_date`, a fixed list of day-first formats tried in turn.

**Why it is designed this way**
- *Integers in paise, Decimal only while parsing*: `0.1 + 0.2` in float is `0.30000000000000004`; summed over thousands of rows, totals drift. `Decimal("1234.5") * 100` is exactly `123450`, and once it is an `int` all sums are exact.
- *Peel, then validate, then convert*: sign markers (`Dr/Cr`, `( )`, `+/-`) are removed and **counted** first — `-500 Dr` has two markers and is ambiguous, so it is rejected rather than guessed. Then the currency marker is stripped. Then a regex checks the number's *shape* before `Decimal` sees it, because `Decimal` happily accepts `1e5`, `NaN` and `Infinity`.
- *Decimal places checked separately*: `Decimal("10.555").as_tuple().exponent == -3` → a clear "more than 2 decimal places" error instead of silent rounding.
- *Type check*: `parse_amount(10.5)` raises `TypeError` — by the time it is a float, precision may already be gone.
- *Error messages never echo the input* (privacy): they could end up in logs.
- *Dates are day-first only*: no month-first format is in the list, so `03/04/2026` can only be 3 April. `%Y` needs 4 digits, so `12/10/26` falls through to the `%d/%m/%y` format.
- *`format_inr`*: Indian grouping is "last 3 digits, then groups of 2": `12345678` paise → `123456` rupees → `1,23,456`.

**One thing that could go wrong**
- The grouping regex accepts any *valid* Indian or Western grouping, so a mixed-up export like `12,345,67` is rejected (good), but `1,000` is accepted as both styles (harmless — same value). A bank that uses `.` for thousands and `,` for decimals (European style, `1.234,50`) would be rejected, not misread — acceptable since the spec is INR-only.

**Quiz questions**
1. Walk through `parse_amount("(Rs. 1,23,456.7)")` step by step. What is the result, and which branch sets the sign?
2. Why do we validate with a regex *before* calling `Decimal(...)`? Give an input that would slip through otherwise.
3. Why is `"-500 Dr"` rejected instead of being treated as `-50000`?

## Step 3 — Import, storage, de-duplication, currency (AC-13 to AC-22, AC-44 to AC-48) — 2026-10-12

**What was built**
- `expenses/db.py`: SQLite schema (categories, rules, budgets, import_batches, transactions, fx_rates) and the only SQL in the project.
- `expenses/importer.py`: header detection (I3), normalisation + merchant (I4), bad-row reports (I6), import report + undo (I7), currency path (I8).
- `expenses/fingerprint.py`: `fingerprint()` and `OccurrenceCounter` (**[LEARN] I5** — written by Claude at the owner's request).
- `expenses/fx.py`: live ECB rate fetch (Frankfurter), Decimal JSON parsing, half-even conversion. The only module that touches the network (decision Q5).

**Why it is designed this way**
- *De-dup lives in the database*: `fingerprint TEXT UNIQUE` + `INSERT OR IGNORE`. If `rowcount == 0` the row was a duplicate. The DB guarantees uniqueness even if two imports race — Python code can't forget to check.
- *Occurrence index*: two ₹20 teas on 5 Jan → keys get occurrence 0 and 1 → different hashes → both kept. Re-importing the file numbers them 0 and 1 again → both detected as duplicates. Overlapping statements work the same way.
- *Fingerprint uses the original currency/amount*: `$12.34` is always `USD|-1234`, so a re-import matches even if the INR conversion were different.
- *Per account* (Q4): `HDFC|…|IMPS RENT` ≠ `SBI|…|IMPS RENT`.
- *One file = one DB transaction* (`with conn:`): a crash halfway leaves nothing half-imported.
- *Undo by batch*: every row stores its `batch_id`; `ON DELETE CASCADE` removes exactly that batch's rows.
- *Rates*: `json.loads(body, parse_float=Decimal)` builds `Decimal("90.3")` straight from the text — never a float. Cached per `(currency, date)` in SQLite, so the network is used at most once per pair; failures are remembered for the rest of that import so a dead network costs one timeout, not one per row.
- *Fetcher is injected*: `import_csv(..., fetch_rate=...)` — tests pass a fake, so the suite never goes online.

**One thing that could go wrong**
- The `|`-joined fingerprint is ambiguous in theory: account `A|B` + description `C` and account `A` + description `B|C` hash the same. Fix if ever needed: hash `json.dumps([...])` instead, which escapes separators. Also: if a bank *changes its description text* between two exports of the same period (e.g. adds a reference), the rows will not match and will be imported twice.

**Quiz questions**
1. A statement has three identical ₹20 tea rows on one day. You import it, then import an overlapping statement containing only two of them. How many tea rows are stored, and why?
2. Why does the fingerprint use `orig_amount_minor` instead of the converted INR `amount_minor`?
3. What would break if `import_csv` inserted each row in its own transaction instead of wrapping the whole file in `with conn:`?
