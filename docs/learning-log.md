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
