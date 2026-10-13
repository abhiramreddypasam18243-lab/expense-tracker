# Expense Tracker — Specification

Status: **DRAFT v0.1** (drafted by Claude on 2026-10-12, awaiting owner review)
Owner: Pasam Abhiram Reddy

This file has two parts:

- **Part A — Specification**: what the product is (sections 1–16).
- **Part B — Working agreement**: how we build it (your original rules, unchanged).

---

# Part A — Specification

## 1. Purpose

This is a **private, offline, single-user expense tracker** for Indian bank and card statements. The user imports CSV statement exports. The app cleans them, removes duplicates, categorises each transaction, and shows where the money goes. It also flags unusual spending.

Everything runs locally in a container. No data ever leaves the machine.

## 2. Goals and non-goals

**Goals**

- G1. Import CSV statements from any bank whose CSV has a date, a description and an amount (or debit/credit) column.
- G2. Handle money exactly: no rounding errors and no float.
- G3. Re-importing the same or an overlapping statement never creates duplicates.
- G4. Automatic, editable categorisation using keyword rules.
- G5. Clear insights: monthly summary, category breakdown, budgets, and anomaly flags.
- G6. A clean, modern, easy-to-read UI (section 7.2).
- G7. Privacy by default: no telemetry and no sensitive values in logs. The only network call is the exchange-rate lookup in I8, which sends a currency code and a date, nothing else.

**Non-goals** (see also section 14): multi-user accounts, bank APIs or scraping, PDF statements, ML or LLM categorisation, multi-currency *accounting* (everything is stored and reported in INR; foreign rows are converted on import, see I8), mobile app.

## 3. Domain model

All money is a **signed integer in minor units (paise)**. **Debits are negative and credits are positive.** For example, ₹1,234.50 spent is stored as `-123450`.

| Entity | Fields |
|---|---|
| **Transaction** | `id` (int PK), `account` (str), `txn_date` (ISO date), `description` (raw str), `description_norm` (str), `merchant` (str or null), `amount_minor` (int, signed, **always INR paise**), `orig_currency` (ISO code, `INR` for most rows), `orig_amount_minor` (int, signed, in the original currency's minor units), `fx_rate` (Decimal as TEXT, null for INR), `category_id` (FK), `category_source` (`rule` / `manual` / `default`), `fingerprint` (str, unique), `batch_id` (FK), `created_at` (ISO datetime) |
| **ImportBatch** | `id`, `source_name` (file name only), `account`, `imported_at`, `rows_total`, `rows_imported`, `rows_duplicate`, `rows_rejected` |
| **Category** | `id`, `name` (unique), `kind` (`expense` / `income` / `transfer`), `color` (hex, used by the UI) |
| **Rule** | `id`, `pattern` (case-insensitive substring), `category_id`, `priority` (int, lower runs first) |
| **Budget** | `category_id` (unique), `monthly_limit_minor` (int > 0) |
| **FxRate** | `currency`, `rate_date` (the transaction date asked for), `rate` (Decimal as TEXT: INR per 1 unit), `source_date` (the date the provider answered with), `fetched_at`; primary key `(currency, rate_date)` |

Storage: **SQLite** through the standard-library `sqlite3` module. The file is `data/expenses.db` by default and can be overridden with `EXPENSES_DB`. The schema is created on first run and versioned through `PRAGMA user_version`.

## 4. Import rules

The import pipeline is: read CSV → detect columns → for each row, parse the date and the money (I1, I2) → normalise (I4) → fingerprint (I5) → categorise (section 5) → insert into SQLite in one transaction per file.

### I1. Money parsing [LEARN]

`parse_amount(text) -> int` (minor units). It uses `decimal.Decimal` only.

**Accepted** (after trimming whitespace):

- Plain numbers and decimals: `500`, `500.5`, `500.50`
- Indian or Western thousands separators: `1,23,456.78` and `123,456.78`
- A leading currency marker: `₹`, `Rs`, `Rs.`, `INR` (any case, optional space)
- A leading `-` or `+`, and accounting parentheses `(500.00)` meaning negative
- A trailing `Dr` / `DR` (debit, so negative) or `Cr` / `CR` (credit, so positive)

**Rejected** with `MoneyParseError` (the message never includes the raw value):

- Empty or whitespace-only text
- More than 2 decimal places, for example `10.555`
- Non-numeric text, more than one sign marker (`-500 Dr`), or misplaced commas like `1,,000`
- A `float` input. The function accepts `str` only.

**Two-column statements** (separate Debit and Credit columns): `parse_debit_credit(debit, credit)` takes exactly one non-empty side. A debit becomes a negative number and a credit stays positive. If both sides or neither side is filled, it raises an error.

Conversion: `int(Decimal(clean) * 100)` only after checking the exponent is ≥ −2. No rounding ever happens.

### I2. Date parsing

`parse_date(text) -> datetime.date`. Accepted formats: `DD/MM/YYYY`, `DD-MM-YYYY`, `DD.MM.YYYY`, `YYYY-MM-DD`, `DD-Mon-YYYY` / `DD Mon YYYY` (`12-Oct-2026`), and `DD/MM/YY` (with `YY` read as 20YY).

Ambiguous dates are always read **day first** (the Indian convention): `03/04/2026` is 3 April. Impossible dates (`31/02/2026`) are rejected.

### I3. Column detection

The header row is matched case-insensitively, ignoring spaces, dots and underscores, against these aliases:

| Field | Aliases |
|---|---|
| date | date, txn date, transaction date, value date, posting date |
| description | description, narration, particulars, details, remarks |
| amount | amount, txn amount, transaction amount |
| debit | debit, withdrawal, withdrawal amt, dr |
| credit | credit, deposit, deposit amt, cr |
| currency (optional) | currency, ccy, curr, currency code |

A file is valid if it has a **date**, a **description**, and either an **amount** column or a **debit + credit** pair. If both forms exist, `amount` wins.

If no valid header is found in the first 20 lines, the import fails with a clear message. Bank exports often have preamble lines, so the header does not have to be on line 1.

Encoding: UTF-8, with or without a BOM. Blank lines and lines with only commas are skipped silently.

### I4. Normalisation and merchant

- `description_norm` = the description in upper case, with whitespace runs collapsed to one space and trimmed.
- **Merchant extraction** (best effort, otherwise null):
  - UPI (`UPI/<ref>/<NAME>/...` or `UPI-<NAME>-...`) gives `<NAME>`.
  - POS / card (`POS <digits> <NAME>`) gives `<NAME>`.
  - Otherwise the merchant is null.
- Merchant names are formatted for display: each word is capitalised (`SWIGGY` → `Swiggy`), except words with no vowels or with digits, which stay upper case because they are usually acronyms or codes (`PVR CINEMAS` → `PVR Cinemas`, `KFC`, `HDFC LIFE` → `HDFC Life`).

### I5. Fingerprint and de-duplication [LEARN]

`fingerprint = sha256("{account}|{txn_date ISO}|{orig_currency}|{orig_amount_minor}|{description_norm}|{occurrence}")` as a hex string.

- The **original** currency and amount are used (not the converted INR amount), so a re-import is still recognised as a duplicate even if a rate were different. For INR rows, `orig_amount_minor == amount_minor`.
- `account` is part of the fingerprint, so de-duplication is **per account** (decision Q4).

- `occurrence` is 0 for the first time this exact `(account, date, orig_currency, orig_amount, description_norm)` tuple appears **inside the same file**, 1 for the second, and so on. This keeps two genuine identical transactions on one day, such as two ₹20 tea purchases.
- `fingerprint` has a UNIQUE constraint. On import, a row whose fingerprint already exists is counted as a **duplicate** and skipped.
- Result: re-importing a file adds 0 rows. Importing an overlapping statement adds only the new rows.

### I6. Bad rows

A row that fails I1 or I2 is **rejected**, not fatal. The import report lists `(line number, reason)` for each rejected row, with no raw values. Valid rows from the same file are still imported.

### I7. Import report

Each import returns an `ImportReport` with `batch_id`, `rows_total`, `rows_imported`, `rows_duplicate`, `rows_rejected` and `errors: list[(line, reason)]`. Every import is saved as an ImportBatch.

**Undo**: deleting a batch deletes exactly the transactions it inserted.

### I8. Currency conversion (decision Q5)

- If the file has a **currency** column, a blank cell or `INR` means INR. Any other ISO 4217 code (3 letters) is converted to INR on import. Without a currency column, every row is INR.
- The original amount is parsed with I1 into minor units of that currency (I1's two-decimal limit applies; three-decimal currencies such as KWD are rejected).
- **Rate source**: live **ECB reference rates** from Frankfurter, `GET https://api.frankfurter.dev/v1/{txn_date}?from={CUR}&to=INR` (no API key). It uses the rate for the **transaction date**; for a weekend or holiday, the provider answers with the previous business day, which is stored as `source_date`.
- **Privacy**: the request carries only the currency code and the date: no amounts, descriptions, merchants or account names. The call lives only in `expenses/fx.py`, uses standard-library `urllib.request` with a 5-second timeout, and is made only when a non-INR row is imported.
- **Cache**: every fetched rate is stored in the FxRate table. A `(currency, date)` pair is fetched at most once, ever. Re-imports and tests work offline once the rate is cached.
- **Exactness**: the JSON is parsed with `json.loads(..., parse_float=Decimal)` so the rate is never a float. `amount_minor = (orig_amount_minor × rate)` rounded to a whole paisa with **ROUND_HALF_EVEN**. This is the only place in the system where money is rounded.
- **Failure**: if the network is down, the provider has no rate, or the code is unknown, the row is **rejected** with the reason "exchange rate unavailable" (I6). It is never guessed.
- **Testability**: the importer takes the rate lookup as an injectable function, so tests never touch the network.

### I9. Combined import (several statements, several accounts)

Import several statements at once — typically one per bank account — as one unit.

1. **Preview** (`combine.preview`), which writes nothing to the database. For every file it shows: account, rows in file, new rows, duplicates (already in the database, or repeated in an earlier file of the **same** account in this set), rejected rows with reasons, and a **blocking problem** if the file can't be read (no header, not UTF-8, missing account name).
2. **Transfer pairs between your own accounts** are found among the new, valid rows:
   - one debit and one credit with the **same INR amount** (`a = −b`), in **different accounts**, at most **3 days** apart;
   - each row is used in at most one pair; debits are matched in date order, each to the unused credit with the **closest date** (ties: earliest credit, then file order);
   - every pair is shown in the preview, **pre-ticked**; the user can untick any pair (decision: "detect, you confirm").
3. **Commit** (`combine.commit`), **all or nothing**: if any file has a blocking problem, nothing is imported. Otherwise every file is imported in **one database transaction** as **one import batch** (source = the file names, account = the account names), so one Undo removes the whole set. Rows in ticked pairs get the first category of kind `transfer` (re-created as "Transfers" if none exists) with `category_source = manual`, so "Re-run rules" never undoes them. Unticked pairs keep their rule category.
4. The result is a report per file (imported, duplicate, rejected, errors) plus the number of transfer pairs marked.

The single-file import (I7) still works the same way.

## 5. Categorisation

- **Default categories** are seeded on first run: Food & Dining, Groceries, Transport, Shopping, Bills & Utilities, Rent, Entertainment, Health, Education, Travel, Salary (income), Refunds (income), Transfers (transfer), Cash (expense), Uncategorised. Each has a fixed colour from the UI palette.
- **Default rules** are about 40 keyword rules, including `ATM WDL`, `ATM CASH` and `CASH WDL` → Cash (not plain `ATM`, which also matches "TRE**ATM**ENT"), for example `SWIGGY` and `ZOMATO` → Food & Dining, `UBER` and `OLA` → Transport, `AMAZON` → Shopping, `SALARY` → Salary.
- **Matching**: rules are tried in ascending `priority`, then by `id`. The first rule whose pattern is a substring of `description_norm` wins. With no match, a debit goes to **Uncategorised** (`category_source = default`).
- **Manual override**: the user can set a transaction's category, which stores `category_source = manual`. **"Re-run rules"** recategorises only rows whose source is not `manual`.
- Users can add, edit and delete rules and categories. Deleting a category moves its transactions to Uncategorised.

## 6. Insights

### 6.1 Anomaly detection — modified z-score [LEARN]

The goal is to flag debits that are unusually large **for their category**.

- **Population**: debits only. Work with `x = abs(amount_minor)`, grouped by category, over the **whole stored history** (decision Q2). Transfers are excluded.
- **Score**: `M_i = 0.6745 × (x_i − median) / MAD`, where `MAD = median(|x_i − median|)` (Iglewicz & Hoaglin).
- **Flag** when `M_i > 3.5` **and** `x_i ≥ 2 × median` (decision after Step 9, AC-57). This is one-sided: unusually *small* spends are not interesting. The 2× rule stops a tiny but non-zero MAD — e.g. a ₹649 subscription that is just under half of a category's payments, giving MAD = ₹0.69 — from flagging ordinary spends.
- **Minimum sample**: a category with fewer than **8** debits produces no flags.
- **MAD = 0** (more than half the values are identical), decision **Q1**: fall back to the **mean absolute deviation**, `MeanAD = mean(|x_i − median|)`, and use `M_i = (x_i − median) / (1.253314 × MeanAD)`. The same `> 3.5` threshold applies. If MeanAD is also 0 (every value identical), nothing is flagged. The constant 1.253314 ≈ √(π/2) makes MeanAD comparable to a standard deviation for normal data, just as 0.6745 does for MAD.
- **Computation**: use `Decimal` or exact integer maths for the median and MAD. Floats are allowed only for the final score value, which is displayed and never stored as money.
- **Output**: a list of `(transaction_id, category, amount_minor, median_minor, score)`, sorted by score in descending order. `median_minor` may end in .5 paise when the count is even, so it is stored as a `Decimal`, used for display only.

### 6.2 Monthly summary

For a given month (`YYYY-MM`):

- Total income (credits excluding Transfers; refunds count as income under Refunds, decision Q3)
- Total spend (debits excluding Transfers)
- Net
- Spend per category, sorted in descending order
- Top 5 merchants by spend

There is also a **trend** of total spend for each of the last 12 months.

### 6.3 Budgets

There is an optional monthly limit per expense category. For a month the status is:

- `ok` when spent < 80% of the limit
- `warning` when spent is between 80% and 100%
- `over` when spent is above 100%

### 6.4 Recurring payments

Spot subscriptions and fixed monthly bills.

- **Population**: debits outside transfer categories, whole history (like 6.1).
- **Grouping key**: the merchant if known, otherwise `description_norm` with every word containing a digit removed (so `IMPS RENT SYN123456 SYN LANDLORD` and `IMPS RENT SYN654321 SYN LANDLORD` group together).
- **A group is recurring** when all of these hold:
  - at least **3** payments, in at least 3 different months;
  - **every** gap between consecutive payments is **20–40 days** (monthly);
  - **every** amount is within **±10 %** of the group's median amount (integer check: `|x − median| × 100 ≤ median × 10`).
- **Output** per group: name, category, typical (median) amount, number of payments, last date, typical interval (median gap, days), **next expected date** (last date + median gap), and **active** = the last payment is no more than 40 days before the newest transaction in the database.
- Sorted by typical amount, largest first.

## 7. Interfaces

### 7.1 CLI (`python -m expenses …`, argparse, standard library only)

| Command | Purpose |
|---|---|
| `import <csv> --account <name>` | Import and print the import report |
| `list [--month YYYY-MM] [--category NAME] [--limit N]` | Table of transactions |
| `summary --month YYYY-MM` | Monthly summary (6.2) |
| `anomalies` | Anomaly list (6.1) |
| `recategorize` | Re-run rules on non-manual rows |
| `export <out.csv> [--month YYYY-MM]` | Export transactions to CSV, with amounts as plain INR decimals plus `Original Currency` and `Original Amount` columns |
| `recurring` | Recurring payments (6.4) |
| `undo-import <batch_id>` | Delete a batch |
| `import-many FILE=ACCOUNT ... [--yes] [--no-transfers]` | Combined import (I9). Without `--yes` it only prints the preview (dry run, nothing written). `--no-transfers` imports without marking transfer pairs. |

Exit code is 0 on success, 1 on user error (bad file or bad arguments), and 2 on an internal error.

### 7.2 Streamlit UI — design requirements

The UI is a thin layer over the core: no SQL and no business rules live in UI files.

**Look and feel**

- A custom theme in `.streamlit/config.toml`: a calm neutral background, one accent colour (indigo), the system font stack, and rounded cards.
- A small `ui/style.py` injects minimal CSS for KPI cards and spacing. It contains no external assets, fonts or CDNs (offline rule).
- **All money is shown in Indian format**: `₹1,23,456.78`. Spend is shown in a neutral text colour, income in green, and over-budget in red.
- Every page has a clear title, a one-line subtitle, and a friendly **empty state** with a call to action when there is no data, for example "No transactions yet — import a statement".
- Charts use Altair, which ships with Streamlit, with category colours taken from the Category table. They have no chart junk: no gridline clutter and labelled axes.

**Pages** (sidebar navigation, using `st.navigation`)

1. **Dashboard**:
   - A month picker.
   - 4 KPI cards: Spend, Income, Net, and Transactions count.
   - A spend-by-category donut.
   - A 12-month spend trend bar chart.
   - Budget progress bars.
   - The top 3 anomalies.
2. **Transactions**:
   - Filters for month, category, account and a text search.
   - An editable table where only the category column can be changed (the edit becomes a manual override).
   - A CSV export button.
3. **Import**:
   - A **multi-file** uploader. For each file, pick an existing account or type a new one.
   - A **combined preview** (I9): a per-file table and the transfer pairs, each with a tick box. The Import button stays disabled while any file has a blocking problem.
   - The result is shown as 4 metric chips (total, imported, duplicate, rejected) plus a rejected-rows table.
   - An import history list with Undo.
4. **Insights**: the anomaly table with an explanation of each score ("₹8,400 vs typical ₹450 in Food & Dining"), the top merchants, and a **Recurring payments** card (6.4) with the total per month, each payment's typical amount, next expected date and an Active / Stopped badge.
5. **Settings**: tabs for Categories, Rules (add, edit, delete, reorder by priority, and Re-run rules) and Budgets.

**Demo mode** (`EXPENSES_DEMO=1`): a banner reads "Demo — synthetic data". The database is in-memory or temporary and seeded from `tools/make_synthetic.py`, and Import is limited to synthetic files.

## 8. Deployment

### 8.1 Container

- `Dockerfile`:
  - Base image `python:3.12-slim`, with requirements installed from a pinned `requirements.txt`.
  - Runs as a **non-root** user.
  - Starts with `streamlit run app.py --server.address=0.0.0.0 --server.port=8501`, and listens on 0.0.0.0 *inside* the container only.
  - Disables Streamlit usage stats (`--browser.gatherUsageStats=false`).
  - Has a `HEALTHCHECK` on `/_stcore/health`.
- `docker-compose.yml`:
  - Publishes **`127.0.0.1:8501:8501` only**.
  - Mounts a named volume (or `./data`) at `/app/data` for the database.
  - Has no secrets in the file.
- **Passphrase gate**: if `EXPENSES_PUBLIC=1` (meaning the operator intends to publish beyond localhost), the app refuses to start unless `EXPENSES_PASSPHRASE_HASH` is set. Every session must then enter the passphrase, which is checked against a salted PBKDF2 hash using `hmac.compare_digest`. The passphrase is passed in at runtime and never stored in the image.
- **HTTPS when public**: with `EXPENSES_PUBLIC=1`, the app also refuses to start unless the traffic is encrypted — either Streamlit's own TLS is on (`STREAMLIT_SERVER_SSL_CERT_FILE` and `STREAMLIT_SERVER_SSL_KEY_FILE`, no new dependency) or the operator declares an HTTPS reverse proxy in front with `EXPENSES_TLS_PROXY=1`. Without it, the passphrase would cross the network in plain text.
- **`docker-compose.public.yml`** (an override file): publishes on all interfaces, sets `EXPENSES_PUBLIC=1`, requires `EXPENSES_PASSPHRASE_HASH` (compose fails with an error if it is missing), and mounts `./certs` (git- and docker-ignored) for the TLS certificate. Usage: `docker compose -f docker-compose.yml -f docker-compose.public.yml up -d --build`. The base `docker-compose.yml` stays localhost-only.
- The container HEALTHCHECK uses `https` when a certificate is configured.
- **Public hosting** is allowed only with `EXPENSES_DEMO=1`.

### 8.2 Desktop app (Windows launcher)

- `launcher/start.ps1`: if `http://127.0.0.1:8501/_stcore/health` is not already `ok`, run `docker compose up -d --build` in the WSL distribution (default `Ubuntu`) for this project folder, show a "Starting…" notification, and wait up to 3 minutes for health. Then open the app in its **own window** with Microsoft Edge app mode (`msedge --app=http://127.0.0.1:8501`). On failure it shows a message box that says what to check.
- `launcher/stop.ps1`: `docker compose stop` (data is kept).
- `launcher/install-shortcuts.ps1`: creates **Expense Tracker** and **Stop Expense Tracker** shortcuts on the Desktop and in the Start menu, using `launcher/icon.ico`.
- The launcher only ever uses the localhost compose file; it never starts public mode.

### 8.3 Internet demo

- `demo_app.py` is the entry point for public hosting. It **forces** `EXPENSES_DEMO=1` and removes `EXPENSES_PUBLIC`, `EXPENSES_PASSPHRASE_HASH` and `EXPENSES_DB` before running `app.py`, so a hosted copy can never show anything but synthetic data, whatever the host's settings are.
- Host: **Streamlit Community Cloud** (free; deploys `demo_app.py` from a GitHub repository, which may stay private). Publishing needs the owner's GitHub and Streamlit accounts.

## 9. Privacy and security

- No network calls at runtime **except the exchange-rate lookup in I8** (decision Q5). A test checks that no module other than `expenses/fx.py` imports `requests`, `urllib.request`, `http.client` or `socket`, and that `fx.py` only contacts `api.frankfurter.dev`.
- The container therefore needs outbound HTTPS. Without it, foreign-currency rows are rejected while INR rows import normally.
- Logging: at INFO level and above, log only counts, IDs and timings, never descriptions, merchants or amounts. DEBUG is off by default.
- Uploaded files are processed in memory and never written to disk. Only the parsed rows go to SQLite.
- `data/`, `*.db` and `.env` are git-ignored and excluded by `.dockerignore`.

## 10. Project structure

```
expense tracker/
  app.py                    # Streamlit entry (navigation only)
  ui/                       # Streamlit pages (dashboard, transactions, import_data, insights_page,
                            #   settings) + common.py, charts.py, style.py, assets/ (logo SVGs)
  expenses/                 # core — NO streamlit imports
    money.py                # I1, INR formatting
    dates.py                # I2
    importer.py             # I3, I4, I6, I7
    fingerprint.py          # I5
    fx.py                   # I8 — the only module allowed to use the network
    db.py                   # schema, repository functions
    categorize.py           # section 5
    insights.py             # 6.2, 6.3
    anomaly.py              # 6.1
    auth.py                 # runtime config + passphrase gate (8.1)
    exporter.py             # CSV export used by the CLI and the UI (7.1, 7.2)
    __main__.py / cli.py    # 7.1
  tools/make_synthetic.py   # synthetic statement generator
  tools/hash_passphrase.py  # creates EXPENSES_PASSPHRASE_HASH (passphrase read without echo)
  tests/                    # test_ac_XX_*.py
  docs/learning-log.md
  Dockerfile, docker-compose.yml, .dockerignore, .gitignore
  requirements.txt, pyproject.toml (ruff + pytest config)
  .streamlit/config.toml
```

## 11. Dependencies (pinned in requirements.txt)

- Runtime: `streamlit` (which brings `pandas` and `altair`).
- Dev: `pytest`, `pytest-cov`, `ruff`.
- Nothing else without asking.

## 12. Acceptance criteria

The test for each criterion is named `test_ac_XX_<slug>`.

**Money and dates (Step 2)**

- **AC-01** `format_inr(12345678)` gives `₹1,23,456.78`; `format_inr(5)` gives `₹0.05`; `format_inr(100000000)` gives `₹10,00,000.00`.
- **AC-02** `format_inr(-123450)` gives `-₹1,234.50`.
- **AC-03** `parse_amount` handles plain and comma-grouped values: `"500"`→50000, `"1,23,456.78"`→12345678, `"123,456.78"`→12345678.
- **AC-04** Currency markers and whitespace: `" ₹ 1,234.50 "`, `"Rs.1234.5"` and `"INR 1234.50"` all give 123450.
- **AC-05** Signs: `"-500"`→-50000, `"(500.00)"`→-50000, `"500.00 Dr"`→-50000, `"500 CR"`→50000.
- **AC-06** `parse_debit_credit("1,000.00", "")` gives -100000 and `("", "250")` gives 25000. Both filled or both empty raises an error.
- **AC-07** More than 2 decimals (`"10.555"`) raises `MoneyParseError`.
- **AC-08** `""`, `"abc"`, `"1,,000"`, `"--5"` and `"-500 Dr"` raise `MoneyParseError`, and the error message does not contain the input.
- **AC-09** A float input (`10.5`) raises `TypeError`. The results of AC-03 to AC-06 are all of type `int`.
- **AC-10** `parse_date` accepts every format in I2 (one test case each).
- **AC-11** `"03/04/2026"` gives 2026-04-03 (day first).
- **AC-12** `"31/02/2026"`, `"2026-13-01"` and `"hello"` raise `DateParseError`.

**Import, storage, de-duplication (Step 3)**

- **AC-13** Header detection works with aliases, mixed case and preamble lines. It works for both the single-amount and the debit/credit layout.
- **AC-14** Importing a synthetic CSV stores every valid row with the correct sign, date, `description_norm` and merchant.
- **AC-15** A file with 2 bad rows imports the rest. The report lists those 2 line numbers with their reasons and no raw values.
- **AC-16** The fingerprint is deterministic and ignores case or whitespace differences in the description.
- **AC-17** Importing the same file twice gives 0 imported and N duplicate on the second import.
- **AC-18** Two identical rows (same date, amount and description) in one file are **both** stored.
- **AC-19** For overlapping statements A (Jan–Feb) and B (Feb–Mar), the stored count equals the number of unique rows.
- **AC-20** Merchant extraction gives the expected merchant for UPI dash and slash forms and for POS descriptions, and null otherwise.
- **AC-21** `undo_import(batch_id)` removes exactly that batch's rows.
- **AC-22** Captured logs at INFO level during an import contain no description, merchant or amount from the file.
- **AC-44** A file with a currency column converts a USD row using an injected rate: `$12.34` at `90.3` gives 111430 paise (`1234 × 90.3 = 111430.2`). `orig_currency` and `orig_amount_minor` are stored, and INR or blank-currency rows are unchanged.
- **AC-45** Rounding is ROUND_HALF_EVEN: rate `0.5` turns 1 minor unit into 0 paise and 3 minor units into 2 paise.
- **AC-46** A rate lookup that fails (raises or returns nothing) rejects only that row, with the reason "exchange rate unavailable". The other rows import.
- **AC-47** Rates are cached: importing two USD rows on the same date, and then re-importing the file, calls the lookup exactly once.
- **AC-48** `fx.parse_rate_response` reads the Frankfurter JSON into a `Decimal` (never a float) and returns the provider's date. A malformed body raises `FxError`.

**Categorisation (Step 4)**

- **AC-23** Default categories and rules are seeded exactly once, so running the seed twice is a no-op.
- **AC-24** The first matching rule by priority wins, and with no match the row goes to Uncategorised.
- **AC-25** A manual override survives "Re-run rules", while non-manual rows are updated.
- **AC-26** Deleting a category moves its transactions to Uncategorised.

**Insights (Step 5)**

- **AC-27** The monthly summary matches a hand-computed fixture: income, spend, net, per category, top merchants, with Transfers excluded.
- **AC-28** The 12-month trend returns 12 entries, with zero for months that have no data.
- **AC-29** Budget status is ok, warning or over at the 79%, 80%, 100% and 101% boundaries.
- **AC-30** The modified z-score matches a hand-computed example to 3 decimals. The values for this example are `[100,110,120,130,140,150,160,5000]` in rupees, and the 5000 is flagged.
- **AC-31** A category with fewer than 8 debits produces no flags. Credits and Transfers are never flagged.
- **AC-32** MAD = 0 uses the mean-AD fallback (Q1). For `[649 × 9, 6490]` rupees, the 6490 is flagged and the 649s are not. If every value is identical, nothing is flagged.

**CLI (Step 6)**

- **AC-33** `import`, `list`, `summary`, `anomalies` and `undo-import` work end-to-end on a temporary database, with exit code 0. A missing file gives exit code 1.
- **AC-34** `export` writes a CSV that, when re-imported into a fresh database, gives identical amounts (a round trip).

**UI (Step 7)**

- **AC-35** Each page renders without exception using Streamlit `AppTest`, both with an empty database and with a seeded synthetic database.
- **AC-36** No module in `expenses/` imports `streamlit`, and no module in `ui/` contains SQL (it contains no `SELECT`, `INSERT`, `UPDATE` or `DELETE` strings).
- **AC-37** An empty database shows the empty-state message on the Dashboard and Transactions pages.

**Deployment and privacy (Steps 1 and 8)**

- **AC-38** `docker-compose.yml` publishes only on `127.0.0.1`. A test parses the YAML text and asserts this.
- **AC-39** `make_synthetic.py --seed 42` is deterministic. Its output has no real-looking account numbers and covers both CSV layouts.
- **AC-40** The Dockerfile runs as non-root and has a HEALTHCHECK. `docker compose up -d --build` becomes healthy (manual check, recorded in the learning log).
- **AC-41** With `EXPENSES_PUBLIC=1` and no `EXPENSES_PASSPHRASE_HASH`, startup refuses. With the hash set, a wrong passphrase is rejected and the right one is accepted.
- **AC-42** In demo mode the banner is shown and the database is not `data/expenses.db`.
- **AC-43** No module except `expenses/fx.py` imports network libraries, and `fx.py`'s only URL is `api.frankfurter.dev` (section 9).

**Extras (Step 9)**

- **AC-49** Merchant display: `POS 1102 PVR CINEMAS` → `PVR Cinemas`, `UPI/R1/KFC/OKAXIS` → `KFC`, `UPI-HDFC LIFE-R2-PREMIUM` → `HDFC Life`, `POS 9 MCDONALD'S` → `Mcdonald's`. The AC-20 cases are unchanged.
- **AC-50** A fresh database has a `Cash` expense category and the `ATM WDL`, `ATM CASH` and `CASH WDL` rules. `ATM WDL 123` → Cash, `HOSPITAL TREATMENT` → Health (not Cash). Synthetic data has no Uncategorised rows.
- **AC-51** `EXPENSES_PUBLIC=1` with a valid hash but no TLS and no `EXPENSES_TLS_PROXY=1` is refused. With Streamlit TLS on, or with `EXPENSES_TLS_PROXY=1`, it is accepted. The hash is still required (AC-41).
- **AC-52** `docker-compose.public.yml` publishes `0.0.0.0:8501`, sets `EXPENSES_PUBLIC: "1"`, requires `${EXPENSES_PASSPHRASE_HASH:?...}`, and sets both Streamlit TLS paths. The base compose file still binds 127.0.0.1 only. `certs/` and `*.pem` are git- and docker-ignored. Streamlit serving `/_stcore/health` over HTTPS is a manual check, recorded in the learning log.
- **AC-53** Recurring detection on a fixture finds a monthly ₹649 subscription (4 payments) and a monthly ₹22,000 rent (3 payments, different references), with the correct typical amount, count, last date, next expected date and active flag.
- **AC-54** These are not recurring: variable amounts (> ±10 %), irregular gaps (e.g. 10 or 60 days), only 2 payments, credits, and transfers. A group whose last payment is more than 40 days before the newest transaction is reported with `active = False`.
- **AC-55** On synthetic data, Rent and the monthly Spotify subscription are detected. The Insights page shows "Recurring payments", and `python -m expenses recurring` exits 0 and lists them.
- **AC-57** Anomalies must also be at least 2× the category median: with 12 × ₹649, 6 × ₹119 and tickets of ₹677–₹2,000 (MAD = ₹28), only ₹2,000 is flagged, even though ₹800 alone scores above 3.5.
- **AC-58** The launcher scripts exist; `start.ps1` checks health before starting, waits for health with a timeout, opens `msedge --app=http://127.0.0.1:8501`, and never references the public compose file; `install-shortcuts.ps1` creates Desktop and Start-menu shortcuts with `icon.ico`; `icon.ico` is a valid ICO file. Manual check: double-clicking the shortcut opens the app window (recorded in the learning log).
- **AC-59** `demo_app.py` always runs in demo mode: even with `EXPENSES_PUBLIC=1`, no hash and `EXPENSES_DB` set, it shows the demo banner and seeded data, and never creates the `EXPENSES_DB` file.
- **AC-60** `combine.preview` on 3 files (two accounts, one file repeating rows of another file of the same account, one row already in the database) reports correct per-file totals, new rows, duplicates and rejected rows, and writes nothing (no transactions, no batches).
- **AC-61** Transfer detection: a ₹20,000 debit in HDFC and a ₹20,000 credit in ICICI 1 day later form a pair; the same amounts 4 days apart, or within one account, do not; with two possible credits the closer date wins and each row is in at most one pair.
- **AC-62** All or nothing: a file with no header, or with an empty account name, is a blocking problem in the preview, `commit` raises `CombinedImportError`, and the database is unchanged.
- **AC-63** Commit imports every file as one batch; ticked pairs land in Transfers with source `manual` and stay there after "Re-run rules"; an unticked pair keeps its rule category; `undo_import(batch)` removes every row of every file.
- **AC-64** CLI `import-many a.csv="HDFC Savings" b.csv="ICICI Card"`: without `--yes` it prints the preview and pairs and writes nothing (exit 0); with `--yes` it imports and marks pairs; with `--no-transfers` it marks none; a malformed argument (no `=`) or a missing file exits 1.
- **AC-65** The Import page renders with a multi-file uploader (`accept_multiple_files`); demo mode is unchanged. Manual check: uploading two statements in the browser shows the combined preview and the transfer pairs, and the import works (recorded in the learning log).
- **AC-56** Export adds `Original Currency` and `Original Amount` columns. A USD row survives a round trip: after re-import the INR amounts are identical, and the exported original columns read `USD` and `-12.34`.

## 13. Build order

| Step | Content | ACs |
|---|---|---|
| 1 | Walking skeleton: structure, `requirements.txt`, `pyproject.toml`, `.gitignore`, `.dockerignore`, `git init`, Dockerfile + compose, a "Hello" Streamlit page, the synthetic generator | AC-38, AC-39, AC-40 |
| 2 | Money and dates — **[LEARN] I1** | AC-01 – AC-12 |
| 3 | Importer, SQLite, fingerprint, de-dup, undo, currency conversion — **[LEARN] I5** | AC-13 – AC-22, AC-44 – AC-48 |
| 4 | Categorisation | AC-23 – AC-26 |
| 5 | Insights, budgets, anomalies — **[LEARN] 6.1** | AC-27 – AC-32 |
| 6 | CLI | AC-33 – AC-34 |
| 7 | Streamlit UI (all pages, theme, empty states) | AC-35 – AC-37 |
| 8 | Hardening: passphrase gate, demo mode, network check | AC-41 – AC-43 |
| 9 | Extras: merchant acronyms, Cash/ATM rules, HTTPS for public mode, recurring payments, original currency in export, anomaly 2× rule | AC-49 – AC-57 |
| 10 | Desktop app launcher and internet-demo entry point | AC-58 – AC-59 |
| 11 | Combined multi-statement import with transfer matching | AC-60 – AC-65 |

Rebuild the container after every step.

## 14. Out of scope

Multi-user support and login (other than the passphrase gate), bank APIs, PDF or XLS import, ML or LLM categorisation, multi-currency reporting or offline rate tables, non-monthly recurring payments (weekly, yearly), split transactions, attachments and receipts, mobile app, and cloud sync.

## 15. Open questions

These must be answered by the owner before the step that needs them. Each one has a proposed default.

| ID | Question | Needed in | Proposed default |
|---|---|---|---|
| ~~Q1~~ | ~~When MAD = 0, what do we flag?~~ | Step 5 | **Decided 2026-10-12: mean-AD fallback** |
| ~~Q2~~ | ~~Anomaly window?~~ | Step 5 | **Decided 2026-10-12: whole history** |
| ~~Q3~~ | ~~Should refunds reduce category spend?~~ | Step 5 | **Decided 2026-10-12: no, they count as income** |
| ~~Q4~~ | ~~Should de-duplication be per account, or global?~~ | Step 3 | **Decided 2026-10-12: per account** |
| ~~Q5~~ | ~~Should non-INR rows be rejected or ignored?~~ | Step 3 | **Decided 2026-10-12: convert to INR with live rates (I8)** |

## 16. Decisions log

| Date | Decision | Reason |
|---|---|---|
| 2026-10-12 | The spec was drafted by Claude, because the original spec.md held only the working agreement | The owner chose "Claude drafts the spec" |
| 2026-10-12 | Spec approved by owner; file renamed `spec.md` → `SPEC.md` | Part B refers to SPEC.md; case matters on Linux/git |
| 2026-10-12 | Dependencies split: `requirements.txt` (runtime, goes in the image) + `requirements-dev.txt` (pytest, pytest-cov, ruff) | Keeps test tools out of the container; same pinned versions as section 11 |
| 2026-10-12 | **Q4**: de-duplication is per account (the account is in the fingerprint) | Two accounts can genuinely have identical rows (e.g. rent paid from both) |
| 2026-10-12 | **Q5**: non-INR rows are converted to INR on import using **live online ECB rates** (Frankfurter), rule I8 | Owner's choice. **This amends the Part B privacy rule "no network calls at runtime"** with one narrow exception: only a currency code and date are sent, the call is made only for non-INR rows, results are cached, and offline means the row is rejected, never guessed |
| 2026-10-12 | The fingerprint uses the original currency and amount, not the converted INR | A re-import stays a duplicate regardless of the rate |
| 2026-10-12 | **Q1**: when MAD = 0, use the mean-absolute-deviation fallback `(x − median) / (1.253314 × MeanAD)`; if MeanAD = 0 too, flag nothing | Standard practice (IBM SPSS); avoids both divide-by-zero and the noise of "flag anything above median" |
| 2026-10-12 | **Q2**: anomalies are computed over the whole stored history | Simplest; a personal tracker holds months, not decades |
| 2026-10-12 | **Q3**: refunds are income (Refunds category) and do not reduce category spend | Transparent; avoids fuzzy merchant matching |
| 2026-10-12 | Added `expenses/exporter.py` and a global `--db` CLI option | Export is shared by the CLI and the UI's export button; `--db` lets tests and demos use a separate database |
| 2026-10-12 | Export writes the converted INR amount only | AC-34 round trip is in INR; the original currency stays in the database |
| 2026-10-12 | UI theme defines both light and dark palettes in `.streamlit/config.toml`; the app follows the viewer's system setting | One accent colour per theme keeps the look calm in both |
| 2026-10-12 | Budget bars are drawn with small HTML/CSS (green / orange / red by status) instead of `st.progress` | `st.progress` can't change colour, so over-budget wasn't visible at a glance |
| 2026-10-12 | Ruff ignores line length (E501) in `ui/*.py` only | UI files hold HTML/markdown snippets that read worse when split |
| 2026-10-12 | Passphrase hash format is `pbkdf2_sha256:<iterations>:<salt>:<hash>` (600,000 iterations), made by `tools/hash_passphrase.py` | `:` instead of the usual `$`, because docker-compose would treat `$` in env values as variable substitution |
| 2026-10-12 | The gate is shown whenever a hash is set, not only with EXPENSES_PUBLIC=1; a wrong attempt waits 0.5 s | Setting a hash is a clear intent to lock; the delay slows guessing |
| 2026-10-12 | "Config refuses to start" = the app serves only an error message and no pages | Streamlit has no pre-start hook; no page or data is reachable, which is the safety goal |
| 2026-10-12 | Demo mode gives each visitor their own temporary SQLite file seeded with synthetic data and budgets; Import shows an "Add a synthetic statement" button instead of an uploader | Interprets "Import is limited to synthetic files": real files can't be uploaded at all, and visitors can't see each other's changes |
| 2026-10-12 | **Step 9 added** at the owner's request ("add them"): the five suggestions from the end of Step 8 | Owner approved the features; specified as AC-49 – AC-56 before coding |
| 2026-10-12 | Cash rules use `ATM WDL`, `ATM CASH` and `CASH WDL`, not `ATM` | `ATM` is a substring of TREATMENT, so hospital bills would become Cash |
| 2026-10-12 | Public mode requires HTTPS (Streamlit TLS or a declared proxy); AC-41's gate tests now run with `EXPENSES_TLS_PROXY=1` | Without TLS the passphrase crosses the network in plain text; Streamlit's built-in TLS needs no new dependency |
| 2026-10-12 | Recurring = monthly only: ≥ 3 payments, every gap 20–40 days, every amount within ±10 % of the median | Simple, explainable rules; catches subscriptions and rent, skips variable bills and frequent small spends |
| 2026-10-12 | The synthetic generator adds a fixed monthly ₹119 Spotify payment on the 12th | Gives the recurring detector a realistic subscription; it uses no random numbers, so the rest of the generated data is unchanged |
| 2026-10-12 | Export's extra columns are named `Original Currency` / `Original Amount`, not `Currency` | `Currency` is an import alias (I3), so re-importing the export would convert the INR amount a second time |
| 2026-10-12 | **Anomaly 2× rule**: flag only if `M > 3.5` and the amount is at least 2× the category median (owner's choice) | Adding the monthly Spotify payment exposed a MAD weakness: 14 of 29 Entertainment payments were ₹649, so MAD = ₹0.69 and every cinema ticket looked "unusual". The 2× materiality check is simple to explain and keeps the real outliers |
| 2026-10-13 | **Step 10**: Windows desktop launcher (Docker in WSL + Edge app window) and a public demo on Streamlit Community Cloud (owner's choice: "Desktop app + internet demo") | Real data stays on the PC; only the forced-demo entry point is ever hosted |
| 2026-10-13 | The launcher uses `127.0.0.1`, not `localhost` | Found in the manual check: Windows PowerShell resolves `localhost` to IPv6 `::1` first, which WSL doesn't forward, so the health check hung |
| 2026-10-13 | **Step 11 — combined import (I9)**: transfers are detected and the user confirms each pair; all-or-nothing; available in the UI and CLI (owner's choices) | Combining statements double-counts money moved between own accounts unless the pair is recognised |
| 2026-10-13 | A combined import is stored as **one** import batch (file and account names joined) rather than a new "group" table | One Undo for the whole set without a schema migration |
| 2026-10-13 | Confirmed transfer pairs are stored with `category_source = manual` | The user confirmed them, so rules must not overwrite them |
| 2026-10-12 | New default categories and rules (Cash, ATM) only reach a **fresh** database | Seeding never touches an existing database (AC-23); add them in Settings on an older database |

---

# Part B — Working agreement

Part A above is the source of truth. If the code and the spec disagree, stop and tell me which one should change. Do not quietly change either.

## About me and this project

I am an undergraduate student building this as a graded project. I will be quizzed on it in a viva, so I must understand every module. I want to finish as fast as possible, so keep the pace high and the explanations short.

## How to work

- Follow the build order in section 13 of the spec, one step at a time. Finish a step (all its acceptance criteria passing) before starting the next. Do not stop in the middle of a step unless you are blocked, a [LEARN] module comes up, or the spec has an open question you need me to answer.
- Tests first. For each acceptance criterion, write the failing test, then the code that makes it pass. Name each test after its AC ID (for example test_ac_01_indian_grouping).
- Move fast. Do not pause for review between modules. At the end of each step, give me a short summary: files changed, tests passing, anything I should check.
- Ask before deciding. If the spec leaves a choice open (section 15), ask me. When we decide, add a row to the Decisions Log in SPEC.md.
- Do not add features that are not in the spec. Suggest them at the end instead.

## Learning rules

- At the end of each step, add a short entry to docs/learning-log.md: what was built, why it is designed that way, one thing that could go wrong, and 3 quiz questions for me (no answers; I will answer them myself).
- These modules are marked [LEARN]. For them, first explain the design and the key edge cases in a few sentences, then let me write the first version myself and review it afterwards:
  - Money parsing (spec section 4, rule I1)
  - Fingerprint and de-duplication (rule I5)
  - Modified z-score anomaly detection (section 6.1)
- If I say "you write it" for a [LEARN] module, write it, and still add its learning-log entry.
- If I ask "why", explain with a small concrete example, not just a definition.

## Project rules

- Money: parse with Decimal, store as signed integers in minor units, never use float. Format only for display.
- Privacy: no network calls at runtime, no telemetry, no downloading models. Logs at normal levels must not contain descriptions, merchants or amounts. *(Amended 2026-10-12, decision Q5: one exception, the exchange-rate lookup in rule I8.)*
- Data: never create, commit or print real bank data. Use only the synthetic generator in tools/make_synthetic.py. The database and data/ are git-ignored.
- Structure: core logic in plain Python modules with no UI imports. The CLI and the Streamlit UI are thin layers over the core.
- Dependencies: pin versions in requirements.txt. Ask before adding a new dependency.
- Deployment: the project must always run as a container (spec section 8.1). Build the walking-skeleton Dockerfile and docker-compose.yml in step 1, keep them working, and rebuild after each step. Never bind to anything but 127.0.0.1 without the passphrase gate. Never put real data or a passphrase in the image or the repo. Public hosting is for demo mode (synthetic data) only.

## Commands

- Create venv: python -m venv .venv  (activate on Windows: .venv\Scripts\activate)
- Install: pip install -r requirements-dev.txt  (runtime only: requirements.txt)
- Synthetic data: python tools/make_synthetic.py --seed 42 --layout amount --out data/synthetic.csv
- Run locally without Docker: streamlit run app.py --server.address=127.0.0.1
- CLI: python -m expenses --help  (e.g. python -m expenses import data/synthetic.csv --account "Demo Bank"; add --db PATH to use another database)
- Run tests: pytest -q
- Coverage: pytest --cov=expenses --cov-report=term-missing
- Lint: ruff check .
- Run the deployed service: docker compose up -d --build, then open http://localhost:8501
- Desktop app: run launcher/install-shortcuts.ps1 once, then use the Expense Tracker shortcut
- Demo mode (synthetic data, throwaway DB): set EXPENSES_DEMO=1 (locally: `EXPENSES_DEMO=1 streamlit run app.py`)
- Passphrase gate: python tools/hash_passphrase.py, then run with EXPENSES_PUBLIC=1 and EXPENSES_PASSPHRASE_HASH=<output>

(Update this list as the project's real commands appear.)

## Git

Commit after each completed step, with a message that names the AC IDs, for example Add parsing and money rules (AC-01 to AC-12).
