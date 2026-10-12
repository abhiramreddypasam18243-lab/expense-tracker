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

## Step 4 — Categorisation (AC-23 to AC-26) — 2026-10-12

**What was built**
- `expenses/categorize.py`: 14 default categories (with chart colours), 38 default keyword rules, `seed_defaults`, `match_category`, `recategorize`, manual `set_category`, and add/edit/delete for categories and rules.
- Category and rule SQL added to `db.py`; the importer now categorises each row as it is inserted.

**Why it is designed this way**
- *Substring rules, first match wins, ordered by (priority, id)*: easy to explain and fully predictable. Example: `REFUND AMAZON RETAIL` matches both `REFUND` (priority 10) and `AMAZON` (priority 100) → priority 10 runs first → Refunds, not Shopping.
- *`category_source` column* (`rule` / `manual` / `default`): "Re-run rules" only touches non-manual rows, so a user's correction is never silently undone.
- *Seed only when there are no categories*: running it twice does nothing, and a category the user deleted is not resurrected on the next start.
- *Uncategorised is protected*: it is the fallback for no-match rows and for deleted categories, so it can't be deleted or renamed.
- *Seeding runs before the import's `with conn:`*: in sqlite3, a nested `with conn:` commits when it exits, which would break the "whole file or nothing" rule.

**One thing that could go wrong**
- Substring matching has false positives: `RENT` also matches `PARENT` or `CURRENT A/C`, and `METRO` matches `METRO CASH AND CARRY` (a wholesale store). That's why `RENT` has priority 200 (runs last), and users can add more specific rules with lower priority numbers.

**Quiz questions**
1. Two rules have the same priority and both match a description. Which one wins, and why is it important that the order is deterministic?
2. A user manually moves a Swiggy order to "Entertainment", then adds a new rule and clicks "Re-run rules". What happens to that order, and which column makes this possible?
3. What happens to a category's transactions, rules and budget when the category is deleted?

## Step 5 — Insights, budgets, anomalies (AC-27 to AC-32) — 2026-10-12

**What was built**
- `expenses/insights.py`: `monthly_summary` (income, spend, net, per category, top merchants), `spending_trend` (12 months, zeros filled), budgets (`set_budget`, `budget_status`, `budget_state`).
- `expenses/anomaly.py`: `median`, `robust_scores` (**[LEARN] 6.1** — written by Claude at the owner's request) and `find_anomalies`.
- Insight and budget queries added to `db.py`.

**Why it is designed this way**
- *Median + MAD instead of mean + standard deviation*: one huge value drags the mean and SD toward itself and hides. Swiggy orders ₹100–160 plus one ₹5,000: mean ≈ ₹739, SD ≈ ₹1,720 → ordinary z of the ₹5,000 ≈ 2.5 (not flagged!). Median ₹135, MAD ₹20 → modified z ≈ 164 (clearly flagged).
- *0.6745*: for normally distributed data MAD ≈ 0.6745 × SD, so multiplying by 0.6745 puts the score on the same scale as a normal z-score, and the 3.5 cut-off (Iglewicz & Hoaglin) means "very unusual".
- *MAD = 0 fallback* (Q1): ten ₹649 Netflix bills → every deviation is 0 except the odd one → MAD = 0 → divide by zero. Mean absolute deviation still sees the outlier: `(6490 − 649) / (1.253314 × 584.1) ≈ 7.98` → flagged. All values equal → nothing is unusual → all scores 0.
- *One-sided* (`M > 3.5` only): a ₹20 order in a ₹500 category is not worth a warning.
- *At least 8 debits per category*: with 3 data points, "unusual" is meaningless.
- *Decimal until the very end*: the median of `[1, 2]` is exactly `1.5`; only the final score becomes a float, and it is never stored as money.
- *Budgets use integer maths*: `spent × 100 < limit × 80` — no percentages as floats, so 79.999…% can't be rounded into "warning".
- *Refunds are income* (Q3) and *transfers are excluded* everywhere, so moving money between your own accounts is not "spending".

**One thing that could go wrong**
- A category that mixes very different things (e.g. Food & Dining = ₹20 teas *and* ₹800 dinners) gets a low median, so ordinary dinners can look less "normal". On the synthetic data the median for Food is ₹20 because teas dominate; the ₹8,400 party order is still flagged (score ≈ 17), but a ₹1,500 dinner might be too in real data. Fix if needed: more specific categories or rules.

**Quiz questions**
1. Compute the median and MAD of `[10, 10, 10, 12, 50]` by hand. Which branch of `robust_scores` runs, and is 50 flagged?
2. Why would an ordinary z-score (mean and standard deviation) fail to flag the ₹5,000 order in the example above?
3. Why are credits and transfers excluded from anomaly detection?

## Step 6 — CLI (AC-33, AC-34) — 2026-10-12

**What was built**
- `expenses/cli.py` + `expenses/__main__.py`: `python -m expenses import | list | summary | anomalies | recategorize | export | undo-import`, with a global `--db` option.
- `expenses/exporter.py`: CSV export (shared with the UI later), plus `money.to_plain` and `db.list_transactions` (filters: month, category, account, search, limit).

**Why it is designed this way**
- *Thin layer*: each command is "parse args → call one core function → print". No business rules live in `cli.py`, so the UI in Step 7 reuses exactly the same core.
- *Exit codes* 0 / 1 / 2: scripts can tell "you gave me a bad file" (1) from "the program is broken" (2). The internal-error message prints only the exception *type*, never its text, because the text could contain data.
- *`main(argv)` returns an int instead of calling `sys.exit`*: tests call `cli.main([...])` directly and check the return code — no subprocess needed (one subprocess test checks `python -m expenses` itself).
- *Export uses plain decimals* (`-1234.50`), not `₹1,234.50`: a machine-readable file must round-trip. AC-34 proves it: export → import into a fresh DB → identical amounts.
- *`sys.stdout.reconfigure(encoding="utf-8")`*: Windows consoles default to cp1252, which cannot print ₹ and would crash with UnicodeEncodeError.

**One thing that could go wrong**
- Export writes the converted INR amount only, so a USD row comes back as INR on re-import — the original currency is lost in the file (it stays in the database). Fine for the INR-only round trip the spec asks for, but worth knowing.

**Quiz questions**
1. Why does `cli.main` map argparse's exit code 2 (bad arguments) to our exit code 1?
2. Why must `export` write `-1234.50` rather than `-₹1,234.50`? What would happen on re-import?
3. A command fails with an unexpected `KeyError('SECRET SHOP')`. What does the user see, and why don't we print the message?

## Step 7 — Streamlit UI (AC-35 to AC-37) — 2026-10-12

**What was built**
- `app.py`: page config, CSS, logo, one SQLite connection per script run, `st.navigation` with 5 pages.
- `ui/dashboard.py` (KPIs with month-over-month deltas, category donut + legend, 12-month trend, colour-coded budget bars, top anomalies), `ui/transactions.py` (filters, search, editable Category column, CSV export), `ui/import_data.py` (upload form, import report, history with confirm-to-undo), `ui/insights_page.py` (explained anomalies, top merchants), `ui/settings.py` (categories, rules editor, budgets).
- `ui/common.py`, `ui/charts.py` (Altair), `ui/style.py` (small offline CSS), `.streamlit/config.toml` (light + dark themes), `ui/assets/*.svg`.

**Why it is designed this way**
- *UI is a thin layer*: pages only call `expenses.*` functions and format results. AC-36 enforces it: no `streamlit` import in `expenses/`, no SQL keywords in `ui/`.
- *One connection per run*: Streamlit re-runs the whole script on every click, possibly on different threads; a SQLite connection is cheap to open and must not be shared across threads, so `app.py` opens one, stores it in `st.session_state`, and closes it in `finally`.
- *Editor key changes after every save* (`txn-…-{version}`): `st.data_editor` remembers edits by *row position*. If filters changed, an old "row 3 → Shopping" edit could land on a different transaction. A fresh key throws old edits away.
- *Money is never typed as a float*: budget limits are entered as text and parsed with `parse_amount` (I1); charts get whole rupees (`paise // 100`) only to size the bars, while every label is `format_inr` text.
- *Escaping*: user data (descriptions, merchants, category names) goes through `html.escape` before being placed in HTML, so a description like `<script>` is shown, not run.

**One thing that could go wrong**
- Streamlit's `st.html("")` with an empty string raises an error — the dashboard crashed when no budgets existed. AC-35 (render every page with seeded data) caught it before commit. UI tests that render pages with *different data states* are what catch this class of bug.

**Quiz questions**
1. Why does the Transactions page change the `st.data_editor` key after saving a category change? What bug would happen without it?
2. Where does the number on a dashboard bar come from, and why is it OK to use `paise // 100` there but not in `insights.py`?
3. What does AC-36 check, and how does that protect the "thin UI" rule?

## Step 8 — Hardening: passphrase gate, demo mode, network check (AC-41 to AC-43) — 2026-10-12

**What was built**
- `expenses/auth.py`: `load_config` (reads `EXPENSES_PUBLIC`, `EXPENSES_PASSPHRASE_HASH`, `EXPENSES_DEMO`), `hash_passphrase` / `verify_passphrase` (salted PBKDF2-HMAC-SHA256, 600,000 iterations, constant-time compare).
- `ui/gate.py` (lock screen), `ui/demo.py` (per-visitor throwaway demo database), `tools/hash_passphrase.py`.
- `app.py` now: refuse on unsafe config → gate → demo banner → pages. `docker-compose.yml` documents the runtime env vars.
- AC-43 test: only `expenses/fx.py` may import network libraries, and its only host is `api.frankfurter.dev`.

**Why it is designed this way**
- *Store a hash, never the passphrase*: if someone reads the env/compose file they get `pbkdf2_sha256:600000:<salt>:<hash>`, not the passphrase. The **salt** means two users with the same passphrase get different hashes (no pre-computed tables); **600,000 iterations** make each guess slow (~0.3 s) for an attacker but cost a real user once.
- *`hmac.compare_digest`*: a normal `==` stops at the first different character, so response time leaks how much of the hash matched. `compare_digest` always takes the same time.
- *`:` separator*: docker-compose would read `$600000` in an env value as a variable and silently corrupt the hash.
- *Fail closed*: `EXPENSES_PUBLIC=1` without a hash → only an error message is served, no pages, no data.
- *Demo DB per visitor* in the system temp folder: the real `data/expenses.db` is never opened, and visitors can't see each other's edits. The test checks that `$EXPENSES_DB` is not even created.

**One thing that could go wrong**
- The gate protects the *UI*; it is not a full auth system (no accounts, no lockout after N tries, no HTTPS by itself). Anyone exposing the app beyond localhost should also put it behind HTTPS (a reverse proxy) — otherwise the passphrase travels in plain text on the network.

**Quiz questions**
1. Why is a salted, slow hash (PBKDF2, 600k iterations) better than storing `sha256(passphrase)`?
2. What timing information could leak if we compared hashes with `==`, and how does `compare_digest` stop it?
3. In demo mode, why does every visitor get their own temporary database instead of sharing one?

## Step 9 — Extras (AC-49 to AC-57) — 2026-10-12

**What was built** (the five suggestions from Step 8, approved by the owner, plus one fix they exposed)
- Merchant display keeps acronyms: `PVR Cinemas`, `KFC`, `HDFC Life` (`importer.display_name`).
- `Cash` category with `ATM WDL` / `ATM CASH` / `CASH WDL` rules.
- Public mode needs HTTPS: Streamlit's own TLS or a declared proxy (`EXPENSES_TLS_PROXY=1`); `docker-compose.public.yml` override; HEALTHCHECK speaks https when a certificate is set.
- `expenses/recurring.py` + `python -m expenses recurring` + an Insights card: monthly subscriptions and fixed bills.
- Export keeps `Original Currency` and `Original Amount`.
- Anomaly **2× rule** (owner's choice): flagged spends must also be at least twice the category's typical amount.

**Why it is designed this way**
- *Not plain `ATM`*: substring rules match anywhere, and `TRE-ATM-ENT` contains it. A hospital bill would have become "Cash". Longer patterns are safer.
- *Vowel-less words stay upper case*: a cheap, explainable acronym test — `PVR`, `KFC`, `HDFC` have no vowels; `SWIGGY` does. It is a heuristic (e.g. `IRCTC` → `Irctc`), and that's acceptable for display text.
- *HTTPS in public mode*: the passphrase gate is useless if the passphrase travels in plain text over Wi-Fi. Streamlit can serve TLS itself, so no new dependency; compose's `${VAR:?message}` makes `docker compose up` fail loudly if the hash is missing.
- *Recurring = monthly + stable amount*: rent (₹22,000 on the 3rd) and Spotify (₹119 on the 12th) pass; electricity (₹900–₹2,500) fails the ±10 % check; chai (many per month) fails the 20–40-day gap check. References like `SYN123456` are stripped from the grouping key so each month's rent row groups together.
- *Export column names*: calling it `Currency` would make our own importer treat the INR amount as a foreign amount and convert it a second time.
- *The 2× rule*: adding Spotify made Entertainment = 14 × ₹649 + 6 × ₹119 + cinema tickets (29 rows). 14 is *just under half*, so MAD wasn't 0 (no fallback) but ₹0.69 — and dividing by ₹0.69 makes a ₹677 ticket score ~27. Requiring "at least 2× typical" keeps real outliers (₹65,000 vs ₹2,826) and drops noise.

**One thing that could go wrong**
- The new Cash category and ATM rules are only seeded into a **fresh** database (seeding never edits an existing one, AC-23). On an older database, add them in Settings.
- HTTPS was verified locally (self-signed certificate: `https` health → ok, plain `http` refused); the Docker build itself is still unverified on this machine.

**Quiz questions**
1. Why does `MAD = ₹0.69` produce false anomalies, and why didn't the Q1 mean-AD fallback catch it?
2. Rent rows look like `IMPS RENT SYN123456 SYN LANDLORD`. How does the recurring detector group six of them together, and which check would fail if the rent changed from ₹22,000 to ₹25,000 halfway?
3. Why must public mode require HTTPS even though there is already a passphrase?

### Verification — 2026-10-12 (manual parts of AC-40 and AC-52, and the "older database" note)

- **Container rehearsal on Linux (no Docker available).** WSL Ubuntu 24.04, Python 3.12.3 (same minor version as `python:3.12-slim`), non-root user, build context = git-tracked files minus `tests/` and `docs/` (41 files, no `.db`/`.csv`/`.pem`), `pip install -r requirements.txt` (streamlit 1.65.0), then the **exact** `CMD` and `HEALTHCHECK` commands parsed from the Dockerfile:
  - default mode: HEALTHCHECK over **http → PASS**; a 290-row synthetic import works in the `data/` layout.
  - public mode (env from `docker-compose.public.yml`, self-signed cert): HEALTHCHECK over **https → PASS**; plain http **refused**.
  - Not covered without Docker: the image build itself (`useradd`, `COPY --chown`, volume permissions) and compose's `!override` / `${VAR:?}` handling. Run `docker compose up -d --build` once Docker is installed.
- **Older database** (created with the Step 8 code, commit 800bd75): opening it with Step 9 code keeps 14 categories and 20 ATM rows in Uncategorised (seeding never edits an existing DB, AC-23). Adding *Cash* + the three ATM rules via the Settings functions and clicking *Re-run rules* moved all 20 rows to Cash, leaving 0 Uncategorised.
