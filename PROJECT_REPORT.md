# ASHA Shahayak: Project Report and Handover

Date: 12 October 2026
Based on: `asha-shahayak-main.zip` (file timestamps 2026-10-11)
Tests: 152 collected. 151 pass without `ASHA_SESSION_SALT`, 152 pass with it (section 11).

This report is built from the code and data files in the repository. Where a
capability could not be found in the code, it says **not found**. Items that were
checked by running the code as well as reading it are marked **(verified by running)**.
No value from `.env` appears here. Line numbers refer to this snapshot and will drift
as code changes; search by function name if a number no longer matches.

Sections 1 to 12 describe how the repository works today. Section 13 is the handover:
how to get running, what to fix next, in what order, and what is blocked on real data.
A developer who reads this file top to bottom should be able to start work immediately.

---

## 1. Purpose and complete flow

The project takes payment claims from ASHA workers in a safety-first way. The package
docstring says payment-claim intake (`asha_shahayak/__init__.py:1`) and the FastAPI app
describes itself as durable, confirmation-first intake (`asha_shahayak/api.py:78`).

An ASHA sends a text or a voice note (Hindi or Awadhi) on WhatsApp. The bot works out the
activity, month, count and amount she is claiming, compares it with a rate card, looks up
what the block office has recorded for her, replies with a receipt ID, and, if there is a
shortfall, offers to draft a complaint that is filed only after she says yes. An officer
dashboard shows the ledger, the gap traces and the confirmed complaints, and is where the
block payment data is uploaded.

### A. Main FastAPI and WhatsApp flow

1. **Webhook verification.** `GET /webhooks/whatsapp` returns `503` if not configured,
   `403` if the mode or token is wrong, otherwise the challenge
   (`asha_shahayak/whatsapp.py:548`, `:555`, `:557`). "Configured" means five values: access
   token, phone number ID, verify token, app secret and graph version
   (`asha_shahayak/whatsapp.py:62`).
2. **Webhook authenticity.** `POST /webhooks/whatsapp` reads the raw body, verifies
   `X-Hub-Signature-256` with HMAC-SHA256 using the app secret, and returns `403` on an
   invalid signature (`asha_shahayak/whatsapp.py:573`, `:574`; helper at `:103`).
3. **Acknowledge, then process in background.** A valid payload goes to a background task
   and `{"received": true}` is returned at once (`asha_shahayak/whatsapp.py:577`, `:578`).
4. **Intake receipt.** `process_message` runs `IntakeStore.save_or_get` on the external
   WhatsApp message ID and saves the **whole webhook payload** as JSON
   (`asha_shahayak/whatsapp.py:521`, `asha_shahayak/storage.py:47`). A duplicate or an
   already `processed` message is skipped (`whatsapp.py:526`). If processing fails the
   status becomes `failed` (`whatsapp.py:533`, `:535`), so a resend of the same message
   from Meta is processed again (`tests/test_whatsapp.py:470`).
5. **Text path.** `reply_context_for_text` runs in this order (`whatsapp.py:353`):
   1. Registration gate. Active only when `session_salt` is set and the registry has
      entries (`whatsapp.py:362`). If the sender is not bound and the text has no `ASHA-...`
      ID, send `REGISTRATION_PROMPT` (`whatsapp.py:368`). If an ID is present, call
      `bind_sender`; a mismatch gives `REGISTRATION_REJECTED`; on success the reply says
      registration is done and the same message is not processed as a claim
      (`whatsapp.py:372`, `:374`).
   2. Yes/no and reset words. If the sender is known, `_confirmation_context`
      (`whatsapp.py:375`).
   3. Alias normalisation. Spoken names from the `head_aliases` table are replaced by the
      head (`whatsapp.py:379`).
   4. Activity missing but month present: reply with a numbered list of heads for that
      month (`whatsapp.py:386`).
   5. Date gate, intent and format hint (`whatsapp.py:397`, `:400`, `:418`).
   6. Reconciliation. `reconcile_saved_text` is called with the sender and `asha_id`;
      keyword arguments are added by inspecting the function signature (`whatsapp.py:420`).
   7. Query memory. If the date is clear and the claim is structured, the last confirmed
      query is saved for 30 minutes (`whatsapp.py:427`, `asha_shahayak/session.py:17`).
   8. Reply via `build_reply`. If a complaint was drafted, the reply is prefixed with the
      claim and ends with a "reply only yes or no" prompt (`whatsapp.py:439`).
6. **Audio path.** The media is downloaded from Meta, sent to ASR, and the reply is
   prefixed with what the bot heard (`whatsapp.py:491`, `:492`, `:498`). For Sarvam,
   `prepare_for_asr` runs (ffmpeg and ffprobe; a clip shorter than 1.5 seconds raises
   `AudioTooShortError`). For the stub ASR, `to_wav_16k` runs (`whatsapp.py:93`). The
   temporary file is always deleted (`whatsapp.py:98`).
7. **ASR failure.** An empty transcript or any exception sends a "voice was not clear,
   please retry" message with the receipt ID, and the message is marked `failed`
   (`whatsapp.py:493`, `:499`).
8. **Claim reply.** It contains the receipt ID, expected amount, the difference between
   reported and rate-card amount, a sample-rate warning, officer status and the state of
   the complaint draft (`whatsapp.py:179`).
9. **Outbound reply.** Sent only when `WA_REPLY=1`. If `validate_reply` fails, `safe_fallback`
   is sent instead. After a valid reply, a Hindi voice note is sent on a best-effort basis
   (`whatsapp.py:128`, `:130`, `:151`, `:155`).

### B. Reconciliation flow

`POST /v1/reconcile` checks `x-api-key`, then saves the text in `IntakeStore` with source
`"text"` (`asha_shahayak/api.py:107`, `:113`). A duplicate that is already `processed`
returns `already_received` (`api.py:119`). A `ValueError` gives `422`; any other exception
marks the message `failed` and is re-raised (`api.py:128`, `:131`). Then
`reconcile_saved_text` runs (`asha_shahayak/reconciliation.py:15`):

1. Text extraction and emergency detection (`reconciliation.py:22`,
   `asha_shahayak/extraction.py:149`).
2. Out-of-range amounts and extraction errors stop here. `month_not_found` is allowed
   (`reconciliation.py:25`, `:27`).
3. The sample rate-card CSV is loaded and the applicable card chosen (`:33`, `:36`).
4. `expected = card.rate * claim.count`, plus an explain trace (`:37`, `:38`).
5. The gap is computed and a ledger entry is written under the same receipt ID as the
   intake. If there is a gap, the trace is written once (`:47`, `:48`, `:53`).
6. If month and activity are known, the head is looked up through `head_aliases` and the
   officer status is fetched (for that ASHA's row if an ASHA ID is known). If the head is
   not found, `alias_missing` returns the heads for that month (`:65`, `:74`).
7. The complaint draft is created under the conditions in section 9 (`:81`).

### C. Dashboard data flow

An officer sends both CSVs with `POST /dashboard/data` (`asha_shahayak/dashboard.py:127`).
Both files are first validated in a temporary SQLite database (`dashboard.py:137`). If
validation passes, both tables in the real database are replaced with `replace=True`
(`dashboard.py:141`). If it fails, the page shows "CSV error: ..." and the old data is
untouched (`dashboard.py:151`). The chatbot reads the same database: `officer_status`,
`head_aliases` and `asha_registry`.

### D. Browser demo flow

The demo runs in two ways:

- **Standalone:** `python -m demo_test_bot.main`, port 8010 (`demo_test_bot/main.py:247`).
- **Inside the main app:** `/chat`, `/api/health`, `/api/text` and `/api/voice` are also
  mounted on the main app and use the same database (`asha_shahayak/api.py:163`, `:168`,
  `:173`).

The browser sends text to `/api/text` and recorded or uploaded audio to `/api/voice`
(`demo_test_bot/index.html:101`, `:126`). `bot_response` (`demo_test_bot/main.py:111`):

- If the text contains an `ASHA-...` ID, it checks the registry and stores the ID in a
  **module-level global** `selected_asha_id` (`main.py:29`, `:121`, `:127`).
- If words like "payment", "भुगतान", "पैसा" appear and an ID is selected, it shows all
  status rows for that ASHA (`main.py:30`, `:64`).
- All other text goes to `reply_context_for_text` with the sender `"local-demo"`
  (`main.py:84`).

`/api/voice` writes the raw body to `/tmp/last_audio.bin` and calls the Sarvam HTTP API
with `saarika:v2.5` (`main.py:219`, `:169`, `:175`).

---

## 2. What each source file does

### `asha_shahayak/*.py`

- `__init__.py:1`: package docstring.
- `api.py:1`: FastAPI app, `lifespan`, the `x-api-key` dependency, `/v1/intake`,
  `/v1/reconcile`, `/v1/complaints/{id}/decision`, logging middleware for the WhatsApp
  webhook (`api.py:155`) and the demo mount (`api.py:163`).
- `asr.py:1`: ASR protocol, `AsrStub` (raises `NotImplementedError` when no provider) and
  `SarvamAsr` (`saaras:v3`, `hi-IN`, `asr.py:26`).
- `audio.py:1`: ffmpeg conversion to WAV 16 kHz mono (`:12`), ffprobe duration check
  (`:38`), `prepare_for_asr` (`:59`) and conversion to OGG/Opus (`:69`).
- `complaints.py:1`: complaint table, draft and confirmed queries, latest draft bound to
  sender (24 hours), decision, reason code, retry counter and sender reset.
- `confidence.py:1`: classifies ASR, schema and business-rule evidence into high, medium
  or low (`:24`). Not called in the production flow.
- `config.py:1`: `Settings`, 16 fields with the `ASHA_` prefix and some explicit aliases
  (`:10`, `:45`).
- `dashboard.py:1`: officer-token dashboard, complaint reason, status and registry upload,
  combined upload and ASHA binding reset (`:170`).
- `data_handler.py:1`: `CsvDataHandler`, loads `registry.csv` and `status.csv` into memory
  with pandas (`:21`) and computes an ASHA's total and pending by phone (`:47`). Created at
  startup in `api.py:69`, but nothing reads `app.state.csv_data` (section 12).
- `dates.py:1`: turns Hindi and Awadhi weekday and relative-day words into IST dates; two
  candidates when the same weekday is ambiguous (`:21`).
- `extraction.py:1`: activity and month aliases (`:10`, `:23`), Hindi and English number
  parsing (`:106`), emergency words and structured claim extraction (`:149`).
- `factset.py:1`: models the facts in a reply with evidence state and source (`:8`, `:36`).
- `gaps.py:1`: difference between expected and reported amount, with a trace (`:11`).
- `intent.py:1`: rule-based lexicon for greeting, follow-up, grievance and payment-status
  intents, with safe canned replies (`:47`, `:64`).
- `ledger.py:1`: hash-chain ledger and gap traces. `append` reuses the caller's
  `receipt_id` and returns the same entry if called again (`:43`).
- `meta.py:1`: Meta Graph API client: send text, download media, upload media, send audio
  (`:17`, `:31`, `:43`, `:54`).
- `reconciliation.py:1`: joins the extracted claim with the rate card, gap, ledger,
  officer status and complaint draft (`:15`).
- `registry.py:1`: `AshaRegistry`. Imports the registry CSV (phone stored as a salted
  hash), binds an ASHA ID to a sender, resolves ASHA ID from sender, officer reset
  (`:36`, `:75`, `:99`, `:108`).
- `rules.py:1`: loads the rate-card CSV (lines starting with `#` skipped), `month_start`,
  selection of the applicable card, `calculate_amount` (`:18`, `:34`, `:50`, `:62`).
- `session.py:1`: `QueryMemory` (last confirmed query, 30 minute TTL) and `sender_hash`
  (`:10`, `:97`).
- `status.py:1`: `StatusStore`. Officer status CSV import, `head_aliases`, lookup
  (`:59`, `:154`, `:191`).
- `storage.py:1`: intake evidence table, receipt generation, idempotency and the
  `received`, `processed`, `failed` status (`:47`, `:78`, `:86`).
- `tts.py:1`: Sarvam Bulbul `bulbul:v3` Hindi TTS (`:13`).
- `validator.py:1`: validates amounts, dates and status claims in an outgoing reply
  against the `FactSet`. On failure, `safe_fallback` (`:34`, `:88`).
- `whatsapp.py:1`: orchestrates webhook, signature, registration, yes/no, alias, intent,
  reconciliation, ASR, reply and optional TTS.
- `templates/dashboard.html:1`: officer dashboard (section 4).

### `demo_test_bot/`

- `__init__.py:1`: package docstring.
- `main.py:1`: demo FastAPI app, `bot_response`, payment-status reply, Sarvam HTTP
  transcription, `/api/*` endpoints.
- `index.html:1`: WhatsApp-style chat UI with text, microphone and audio-file upload.
- `README.md:1`: how to run the standalone demo.

### Root and data files

- `README.md:1`: architecture, demo flow, run, dashboard and WhatsApp instructions.
- `PROJECT_REPORT.md`: this file.
- `pyproject.toml:1`: Python `>=3.11`. Dependencies: fastapi, pydantic-settings,
  rapidfuzz, jinja2, httpx, pandas, sarvamai, uvicorn, python-multipart. Dev: pytest,
  pytest-asyncio (`:10`, `:22`).
- `.env.example:1`: variable names only (section 10).
- `.gitignore`: ignores `.env` and `*.sqlite3` (`:153`, `:223`).
- `LICENSE`: MIT.
- `data/sample_rate_cards.csv:1`: sample rates for three activities. The first line says
  these are not official UP rates.
- `registry.csv:1`: 25 ASHA IDs and phone numbers (`ASHA-101` to `ASHA-125`) in plain
  text. The numbers look synthetic.
- `ASHA_Workers_Data.xlsx`: two sheets. `Registry` (25 rows, `asha_id, phone_number`) and
  `Status` (100 rows, the same 7 columns as the status CSV; 25 ASHAs, January to March,
  heads `vaccination`, `home_visit`, `asha_incentive`, `institutional_delivery`,
  `family_planning`). **No code loads this file** (section 5).
- `seed_demo.py:8`: seeds a `DEMO` ledger gap, a complaint and a status row.
- `Privacy policy/`: static HTML for Vercel (`privacy.html`, `terms.html`,
  `data-deletion.html`, `index.html`, `vercel.json`). The service is called "Asa Shayak"
  here. Deletion is a manual email process.

---

## 3. Database and direct SQLite calls

### Tables defined in source

| Table | Columns | Defined at |
|---|---|---|
| `intake_requests` | `receipt_id`, `source`, `external_message_id` (UNIQUE), `received_at`, `payload_json`, `status` (`received`/`processed`/`failed`) | `storage.py:28`, migration `:38` |
| `complaints` | `complaint_id`, `receipt_id`, `draft`, `state`, `updated_at`, `sender_hash`, `created_at`; added by migration: `reason_code`, `confirmation_retries` | `complaints.py:14`, migrations `:25` |
| `ledger_entries` | `sequence`, `receipt_id` (UNIQUE), `created_at`, `payload_json`, `previous_hash`, `entry_hash` | `ledger.py:18` |
| `gap_traces` | `id`, `receipt_id`, `stage`, `rupees`, `trace_json` | `ledger.py:30` |
| `officer_status` | `id`, `asha_id`, `month`, `head`, `claimed_amount`, `approved_amount`, `stage`, `stage_date` | `status.py:23`, migrations `:35` |
| `head_aliases` | `spoken_name` (PK), `head` | `status.py:49` |
| `asha_registry` | `asha_id` (PK), `expected_phone_hash`, `bound_phone_hash`, `bound_at` | `registry.py:22` |
| `confirmed_queries` | `sender_hash` (PK), `activity`, `query_date`, `receipt_id`, `confirmed_at` | `session.py:28` |

### Direct `sqlite3` use

Every store opens its own connection: `storage.py:20`, `complaints.py:43`, `ledger.py:41`,
`status.py:55`, `registry.py:32`, `session.py:39`. The schema is created in each class's
`__init__`, so a table appears the first time its class is instantiated. `data_handler.py:23`
does not use the database; it reads CSV with pandas. Tests inspect SQLite directly in
`tests/test_whatsapp.py:130` and `:163`.

**No Supabase or PostgreSQL code was found.** The README says storage may later move to
PostgreSQL (`README.md:19`), but all data is in one local SQLite file today
(`ASHA_DATABASE_PATH`, default `asha_shahayak.sqlite3`, `config.py:10`).

---

## 4. Dashboard

The dashboard router is included in the app at `api.py:81`.

| URL | Method | Behaviour |
|---|---|---|
| `/dashboard` | GET | Verifies the token and renders complaints, ledger, gap traces and merged ASHA data (`dashboard.py:68`, `:31`) |
| `/dashboard/complaints/{complaint_id}/reason` | POST | Saves one of 5 allowed reason codes, then redirects (`dashboard.py:78`, `:19`) |
| `/dashboard/data` | POST | Registry and status CSV together: validate first, then replace both (`dashboard.py:127`) |
| `/dashboard/registry/reset` | POST | Removes one ASHA's phone binding; 404 if the ID is unknown (`dashboard.py:158`, `registry.py:108`) |
| `/dashboard/status` | POST | Status CSV only, `replace=False`, so it **appends** (`dashboard.py:93`) |
| `/dashboard/registry` | POST | Registry CSV only, `replace=False` (`dashboard.py:109`) |

The template uses three of these: `/dashboard/complaints/.../reason`, `/dashboard/data` and
`/dashboard/registry/reset` (`templates/dashboard.html:69`, `:103`, `:120`). The last two
routes in the table are not linked from the UI but are still live.

Authentication is by header `x-officer-token` or query parameter `token`, compared with
`secrets.compare_digest` (`dashboard.py:22`). An empty `ASHA_OFFICER_TOKEN` gives 401 to
every request **(verified by running)**.

Sections on the page:

- ASHA-confirmed complaints with a reason form per complaint. The page states that these
  are not sent to any government system (`dashboard.html:61`).
- Ledger entries (receipt, previous hash, hash) and gap traces (`:82`, `:90`).
- Upload ASHA data (two file inputs, ASHA binding reset) with an example of the CSV rule
  (`:100`, `:126`).
- Merged ASHA data: union of registry IDs and status ASHA IDs with "Registered Yes/No"
  (`dashboard.py:35`, `dashboard.html:146`).

The Upload button is shown disabled once data exists, but that is browser-side only. The
server does not stop a second upload (`dashboard.html:110`).

---

## 5. Block payment sheet upload

**No Excel (`.xlsx`) upload or parsing code was found.** There is no `openpyxl`,
`pandas.read_excel` or `.xlsx` handling. `ASHA_Workers_Data.xlsx` sits in the repo but no
code reads it. To use its data, export both sheets to CSV and upload them.

The CSV path takes two files:

- **Registry CSV.** Exact columns `asha_id, phone_number`. UTF-8 (BOM accepted). Both
  values non-empty. The ID is upper-cased. The phone is stored as a **salted SHA-256
  hash**. If `session_salt` is empty the import stops (`registry.py:36`, `:37`, `:43`,
  `:51`).
- **Status CSV, new format.** `asha_id,month,head,claimed_amount,approved_amount,stage,stage_date`.
  Validation: month must be a known English name; `stage` is one of `anm_pending`,
  `moic_pending`, `bam_pending`, `paid`; `approved_amount` is a whole integer;
  `claimed_amount` is empty or an integer; negative amounts are rejected
  (`status.py:85`, `:88`, `:91`, `:95`, `:100`). **`stage_date` format and an empty `head`
  are not checked** (`status.py:105`, `:109`).
- **Status CSV, old format.** `activity,month,status,amount`. Still accepted, but `asha_id`
  is left empty (`status.py:66`, `:82`).
- Each row's `head` is also added to `head_aliases` as an alias for itself
  (`status.py:111`, `:131`).

**Sample workbook versus code.** Of the five heads in the workbook, only `vaccination` and
`home_visit` are among the three activities `extract_claim` recognises
(`extraction.py:10`). `asha_incentive`, `institutional_delivery` and `family_planning` have
neither a rate card nor an extraction alias, so a WhatsApp claim cannot reach them. The
demo's payment-status reply shows all heads (`demo_test_bot/main.py:64`).

---

## 6. How `expected_amount` is computed and where ₹300 comes from

`reconcile_saved_text` always loads the repository's `data/sample_rate_cards.csv`
(`reconciliation.py:33`). The vaccination rate in that file is `100`
(`data/sample_rate_cards.csv:3`). `count` comes from the claim (`extraction.py:162`), the
applicable card is chosen (`rules.py:50`), and the amount is:

```text
expected = card.rate * claim.count        (reconciliation.py:37)
```

For `count 3`:

```text
100 x 3 = 300
```

This is the same figure as the seed data `expected_300_reported_250` (`seed_demo.py:11`)
and the demo example `vaccination july count 3 amount 250` (`demo_test_bot/index.html:60`).
300 is not a hidden rate. The rate card is sample only, and the reply adds that the rate is
a sample and not verified (`whatsapp.py:210`).

**`rate_type` has no effect in production.** The CSV has a `rate_type` column and
`calculate_amount` has a rule that `flat_monthly` is not multiplied by count
(`rules.py:62`), but production reconciliation never calls `calculate_amount` and
multiplies directly. The rule runs only in a test (`tests/test_block_payment_records.py:80`).
Fixing this is Task 7 in section 13.

---

## 7. Receipt ID: one ID through the whole flow

1. `IntakeStore.save_or_get` makes `ASHA-` plus the first 12 hex characters of a UUID
   (`storage.py:67`).
2. `reconcile_saved_text` hands the same ID to the ledger (`reconciliation.py:48`).
   `Ledger.append` keeps the caller's ID and creates a new one only when `receipt_id=None`
   (`ledger.py:43`, `:45`).
3. In the result, `receipt_id` and `intake_receipt_id` have the same value
   (`reconciliation.py:57`, `:58`).
4. The complaint ID is derived from it: `complaint-{receipt_id}` (`reconciliation.py:93`).
5. The "रसीद ID" in the reply and the `Receipt:` line in the chat UI show the same ID
   (`whatsapp.py:193`, `demo_test_bot/index.html:108`).

**(verified by running)** The intake ID and the ID printed in the reply were identical.
A test checks this too: `tests/test_whatsapp.py:506`. If the same external message arrives
again, the old receipt and the old ledger entry are returned (`ledger.py:49`). The one
exception is `seed_demo.py:10`, which calls the ledger without a `receipt_id` and gets a
random one.

---

## 8. `audio.py`, `asr.py`, Sarvam and Bhashini

- `audio.py` does not call a provider; it is an ffmpeg and ffprobe utility. `to_wav_16k`
  **returns the original bytes** if ffmpeg is missing or fails (`audio.py:12`).
  `prepare_for_asr` does not: it raises `RuntimeError` if ffmpeg is missing, errors if
  ffprobe is missing, and raises `AudioTooShortError` for a clip under 1.5 seconds
  (`audio.py:59`, `:38`).
- `asr.py` imports `sarvamai.AsyncSarvamAI` (`asr.py:3`). `SarvamAsr.transcribe` calls
  `saaras:v3` with `mode="transcribe"`, `hi-IN` and WAV input (`asr.py:25`).
- In the WhatsApp path, `get_asr` picks `SarvamAsr` if a Sarvam key exists, otherwise
  `AsrStub` (`whatsapp.py:80`). The stub raises `NotImplementedError`, so without a key a
  voice message ends in the "voice was not clear" fallback.
- **A separate message for a very short clip is never sent.** The `AudioTooShortError`
  handler is in `send_reply`, where that error cannot be raised (`whatsapp.py:142`). The
  real error is raised in `voice_text` and caught by the generic `except Exception`
  (`whatsapp.py:499`).
- TTS is a separate module calling Sarvam Bulbul v3 (`tts.py:13`, `:26`).
- The local demo's voice path makes its own HTTP request and sends `saarika:v2.5`
  (`demo_test_bot/main.py:175`).
- **No Bhashini code was found.**

---

## 9. When and how a complaint is created

`reconcile_saved_text` drafts a complaint when one of these is true (`reconciliation.py:81`):

1. **Amount gap.** `gap.found` and `gap.rupees >= 0`: the reported amount is missing (gap
   is the whole expected amount) or underpaid (`expected > reported`) (`gaps.py:14`, `:25`).
2. **No block record.** The head was recognised and status was checked, but there is no
   row for that ASHA, month and head. A draft is made even if the amount is right
   (`reconciliation.py:81`, `tests/test_block_payment_records.py:53`).

Overpayment (`reported > expected`) records a gap but creates no draft unless (2) applies
(`gaps.py:19`).

The complaint ID is `complaint-{receipt_id}`. A second complaint is not created for the
same receipt; the existing one is returned (`reconciliation.py:85`, `:93`). The draft
holds receipt, activity, month, count, rate, expected and reported amount, difference and
rate-card source; "(rate unverified)" is added for a sample rate (`reconciliation.py:100`).
The draft is saved with the sender's salted hash when both sender and salt exist
(`reconciliation.py:112`).

**Two ways to change state** (initial state `draft`, `complaints.py:60`):

- **WhatsApp yes/no.** Applies to the same sender's latest draft within 24 hours
  (`complaints.py:91`, `:105`). A clear yes gives `filed`, a clear no gives `rejected`.
  Mixed answers such as "yes and no" are not treated as confirmation. Three unclear
  answers delete the draft and reset the process. Words like "रद्द", "cancel", "reset"
  clear the draft and the query memory (`whatsapp.py:271`, `:330`, `:309`, `:340`).
- **HTTP endpoint.** `POST /v1/complaints/{id}/decision` (needs `x-api-key`):
  `confirm=true` gives `filed`, `false` gives `rejected` (`api.py:142`,
  `complaints.py:131`).

A decided complaint is not changed again; `already_recorded` is returned
(`complaints.py:140`).

**The dashboard shows only `filed` complaints**, and a reason code can be saved only on a
`filed` complaint (`complaints.py:73`, `:121`, `:125`). In this repo `filed` means "the ASHA
said yes and the state changed in the database". No code connecting to a government system
was found.

---

## 10. Environment variables (names only)

The 22 names that come from `Settings` (`config.py:10`, `:18`, `:45`):

- `ASHA_DATABASE_PATH`
- `ASHA_REGISTRY_CSV_PATH`
- `ASHA_STATUS_CSV_PATH`
- `ASHA_CONFIDENCE_HIGH_THRESHOLD`
- `ASHA_CONFIDENCE_MEDIUM_THRESHOLD`
- `ASHA_RECEIPT_PREFIX`
- `ASHA_OFFICER_TOKEN`
- `ASHA_API_KEY`
- `ASHA_META_VERIFY_TOKEN` / `WHATSAPP_VERIFY_TOKEN`
- `ASHA_META_ACCESS_TOKEN` / `WHATSAPP_ACCESS_TOKEN`
- `ASHA_META_PHONE_NUMBER_ID` / `WHATSAPP_PHONE_NUMBER_ID`
- `ASHA_META_APP_SECRET` / `WHATSAPP_APP_SECRET`
- `ASHA_META_GRAPH_VERSION` / `WHATSAPP_GRAPH_VERSION`
- `ASHA_SARVAM_API_KEY`
- `ASHA_SARVAM_API_URL`
- `ASHA_SESSION_SALT` / `SESSION_SALT`

`WA_REPLY` is also read, directly from the process environment (`whatsapp.py:128`).

What happens when one is missing:

| Variable | If missing |
|---|---|
| `ASHA_OFFICER_TOKEN` | Nobody can enter the dashboard (401) |
| `ASHA_API_KEY` | `/v1/intake`, `/v1/reconcile`, `/v1/complaints/...` all return 401 |
| `ASHA_SESSION_SALT` | Registry upload errors; WhatsApp registration and yes/no are disabled |
| The 4 Meta values plus graph version | Webhook returns `503` |
| `ASHA_SARVAM_API_KEY` | Voice ASR and TTS are off |
| `WA_REPLY=1` | No outbound WhatsApp reply is sent |

`.env.example` contains only: `ASHA_SARVAM_API_KEY`, `ASHA_API_KEY`, `ASHA_OFFICER_TOKEN`,
`ASHA_SESSION_SALT`, `ASHA_REGISTRY_CSV_PATH`, `ASHA_STATUS_CSV_PATH` (`.env.example:1`).
The Meta variables, `ASHA_DATABASE_PATH` and `WA_REPLY` are missing from it. The values of
`.env` are deliberately not written here.

---

## 11. Tests and current pytest result

`pyproject.toml` runs pytest on the `tests` directory with the repository root on the
Python path (`pyproject.toml:29`). There are 24 test files and **152 tests**.

| File | Tests | What it checks |
|---|---|---|
| `tests/test_api.py:21` | 7 | Intake receipt and idempotency; rejection when the key is absent, wrong, empty or non-ASCII (`:50`); key on all machine endpoints (`:78`); a failed reconcile can run again (`:110`) |
| `tests/test_audio.py:11` | 3 | ffmpeg absent or failing fallback, and a real 16 kHz conversion |
| `tests/test_block_payment_records.py:23` | 8 | Correct reply for every stage (`:23`); clear message and complaint prompt when no block row (`:53`); `flat_monthly` (`:80`); heads list on alias miss (`:93`); unknown stage rejected (`:100`) |
| `tests/test_complaints_api.py:9` | 1 | A complaint is not `filed` before confirmation |
| `tests/test_confidence.py:10` | 5 | Confidence bands, thresholds, invalid score |
| `tests/test_dashboard.py:23` | 12 | Auth and empty state; bad CSV or month; uploaded CSV used in a WhatsApp reply (`:65`); seeded demo (`:94`); empty officer token (`:124`); combined upload replaces both datasets (`:160`); selected ASHA and payment follow-up in the demo (`:207`, `:236`) |
| `tests/test_date_confirmation.py:10` | 3 | Ambiguous, clear and missing date |
| `tests/test_dates.py:7` | 5 | Hindi weekday, relative date, ambiguity |
| `tests/test_demo_extraction.py:9` | 5 | Clean, Devanagari, absurd and unrecognised extraction; emergency |
| `tests/test_demo_pipeline.py:9` | 3 | Evidence-backed reply, noisy speech gate, blocked fallback |
| `tests/test_demo_scenarios.py:11` | 6 | Correct date for "बुधवार"; wrong amounts such as 20000 versus 2000 are blocked (`:28`, `:35`) |
| `tests/test_extraction.py:6` | 15 | Hindi and Awadhi numbers, Devanagari digits, aliases, emergency, range checks |
| `tests/test_extraction_dates.py:8` | 2 | Date and month from a weekday |
| `tests/test_factset.py:9` | 4 | Evidence states, derived amounts, conflicting sources |
| `tests/test_intent.py:10` | 14 | Follow-up, status, grievance, greeting, safe unknown replies |
| `tests/test_ledger.py:4` | 1 | Hash chain and unique receipts |
| `tests/test_meta.py:27` | 1 | Meta text API bearer request |
| `tests/test_reconciliation.py:16` | 10 | Sample rate card, gap traces, missing amount or month, complaint, overpayment |
| `tests/test_registry.py:30` | 6 | Bind, mismatch, only the ASHA's own record (`:59`), reset, dashboard upload and reset (`:116`) |
| `tests/test_session.py:9` | 5 | Query memory, TTL, sender isolation, plain sender not stored |
| `tests/test_storage.py:6` | 1 | Status migration of old intake rows |
| `tests/test_tts.py:10` | 2 | Sarvam Bulbul request and failure fallback |
| `tests/test_validator.py:5` | 4 | Unsupported status or amount blocked, safe fallback |
| `tests/test_whatsapp.py:88` | 29 | Webhook auth and signature; text and audio intake; dedupe; sender-bound and idempotent confirmation (`:386`, `:408`, `:426`, `:440`); retry after failed processing (`:470`); one receipt ID across the path (`:506`); ASR failure messages (`:549`, `:576`); voice note and TTS or conversion failure (`:632`, `:648`, `:662`); yes/no phrases (`:680`) |

### Result (fresh virtualenv, Python 3.12, `pip install -e '.[dev]'`)

```text
Without ASHA_SESSION_SALT:      1 failed, 151 passed, 1 warning
ASHA_SESSION_SALT=test-salt:    152 passed, 1 warning
```

**One test depends on the environment.** The fixture in `tests/test_dashboard.py:15` builds
`Settings` without `session_salt`. With an empty salt the registry import stops
(`registry.py:37`) and the page shows "CSV error: session salt is required for ASHA
registration", while `test_combined_dashboard_upload_replaces_both_datasets` (`:160`) looks
for "Uploaded 1 ASHA registrations ..." (`:201`). On a machine whose environment or `.env`
already has `ASHA_SESSION_SALT`, the test passes. The fix is Task 1 in section 13.

The one warning is the Starlette `TestClient` (httpx) deprecation.

---

## 12. Incomplete, stuck or risky

Each item points to the task in section 13 that fixes it. "Verified" means reproduced by
running the code.

### Security and data risks

1. **The demo chat is open without authentication and can show an ASHA's payment status.**
   `/chat`, `/api/text`, `/api/voice` and `/api/health` are mounted on the main app with no
   `x-api-key` and no officer token (`api.py:163`). The selected ASHA ID lives in a global
   variable shared by all visitors (`demo_test_bot/main.py:29`, `:127`). **Verified:** one
   visitor sent a registry ID, and a second anonymous visitor asking about payment received
   that ASHA's month, head, stage, approved amount and date. The demo does not bind a phone
   to an ID the way WhatsApp does, and the sample IDs are sequential (`ASHA-101` to `125`).
   `/api/health` also reveals the database path (`main.py:198`), and `/api/voice` lets
   anyone trigger a Sarvam call. Applies wherever the app is running. **Task 3.**
2. **The full webhook payload is stored.** `process_message` saves it in
   `intake_requests.payload_json` (`whatsapp.py:521`). A standard Meta payload contains the
   sender's number and profile name, and nothing filters or masks it. The dashboard line
   "Phone numbers are hashed and not stored" (`dashboard.html:119`) is true only for the
   registry table. No per-user deletion code was found, while `Privacy policy/data-deletion.html`
   promises deletion within 30 days (currently a manual email process). **Task 4.**
3. **The dashboard token travels in the query string** and is repeated in redirects and
   form actions (`dashboard.py:23`, `:90`, `:167`, `dashboard.html:69`). It can leak through
   browser history, server logs and referrers. **Task 9.**
4. **If the registry is empty, sender binding is off and anyone may see any ASHA's status.**
   Binding applies only when the salt is set and the registry has entries (`whatsapp.py:362`).
   Otherwise `asha_id=None` is passed and `StatusStore.find` returns the latest row for that
   head and month regardless of ASHA (`status.py:191`). **Verified:** with an empty registry,
   a status row for ASHA-104 and an unknown sender, the sender received "payment done: 300".
   With a registry present, the same sender was stopped at "send your ASHA ID first". This can
   arise by uploading only a status file through `/dashboard/status` (`dashboard.py:93`).
   **Task 2.**
5. **The phone hash is weak.** `sender_hash` is `sha256("{salt}:{number}")` (`session.py:97`).
   A fast hash over a small number space can be reversed if both salt and database leak. The
   salt defaults to empty (`config.py:40`) and nothing forces a unique salt per deployment.
   **Task 10.**
6. **Plain-text phone numbers in the repo:** `registry.csv` and `ASHA_Workers_Data.xlsx`.
   Both look synthetic. Real numbers must never reach git this way. **Task 11.**
7. **No upload size limit, and no check of `head` or `stage_date`** (`dashboard.py:127`,
   `status.py:105`). **Task 8.**
8. **Broad `except Exception` in `send_reply` and `_send_voice`.** Failures only go to the
   log; the caller gets no structured error (`whatsapp.py:146`, `:167`). **Task 12.**
9. **Demo audio goes to `/tmp/last_audio.bin`.** Every upload writes to the same fixed file,
   before the empty-body check, with no cleanup (`demo_test_bot/main.py:219`). **Task 3.**

### Incomplete or misleading flow

- **The numbered head prompt is unfinished.** When no activity is found the bot asks for the
  number of the right head (`whatsapp.py:386`, `:189`), but no code reads that number. A bare
  "2" falls to the format hint or an error. **Task 5c; full flow in 13.6.**
- **With an empty salt the reply makes a false promise.** `build_reply` always says a
  complaint draft exists and will not be filed without the ASHA's yes (`whatsapp.py:203`),
  while with an empty salt the yes/no path is closed (`whatsapp.py:304`) and the draft has
  no `sender_hash` (`reconciliation.py:112`). **Verified.** **Task 5b.**
- **A correct amount still gets "gap could not be determined".** When the amounts are equal
  `Gap.found` is False (`gaps.py:17`), so the "amount matches the rate card" branch
  (`whatsapp.py:204`) never runs and the ASHA sees "could not determine gap from records"
  (`whatsapp.py:209`). **Verified.** **Task 5a.**
- **A capitalised head in the CSV causes an alias miss.** The alias is saved as written on
  import (`status.py:111`) but lookup casefolds (`status.py:165`). For a row with
  "Vaccination", `resolve_head("vaccination")` returns `None`. **Verified.** **Task 6.**
- **`/dashboard/status` and `/dashboard/registry` append**, bypassing the "replace both"
  rule in the README. `find` takes the newest row, so duplicates can hide older ones
  (`dashboard.py:93`, `status.py:201`). **Task 8.**
- **`CsvDataHandler` has no consumer.** `api.py:69` builds it only when both `registry.csv`
  and `status.csv` exist, and the repo has no `status.csv`. Even if built, `app.state.csv_data`
  is never read. If both files exist but are malformed, the `ValueError` from `load()` stops
  the app from starting (`api.py:69`, `data_handler.py:21`). **Task 12.**
- **`evaluate_confidence` is still not called in production** (`confidence.py:24`);
  `process_message` goes from transcript straight to `reply_context_for_text`. **Task 12.**
- **The sample rate is hard-coded.** `reconcile_saved_text` always uses
  `data/sample_rate_cards.csv` (`reconciliation.py:33`), so the expected amount cannot be
  called an official government rate. `month_start` maps an upcoming month to the previous
  year (`rules.py:46`). **See 13.6.**
- **`rate_type` is ignored in production** (section 6). **Task 7.**
- **`AsrStub` and audio fallback.** With no provider the stub raises; `to_wav_16k` returns
  original bytes when ffmpeg is missing, so ASR may get an unsupported input (`asr.py:11`,
  `audio.py:12`).
- **No Excel importer and no government filing API** (sections 5 and 9).

### Deployment and documentation

- No `Dockerfile`, `render.yaml` or `Procfile` was found, and no script that installs
  `ffmpeg` and `ffprobe` (needed for voice). No CI workflow. The repo does not say whether
  the SQLite file sits on a persistent disk. **Task 11.**
- The README `curl` example does not send `x-api-key`, so it returns `401` today
  (`README.md:45`). The README does not mention `ASHA_API_KEY`, `ASHA_SESSION_SALT`, ASHA
  registration or `/chat`. **Task 11.**
- The privacy policy and terms call the service "Asa Shayak"; the code and README say
  "ASHA Shahayak". **Task 11.**

### Verification gaps

- Tests use mocked integrations. No end-to-end test with real Meta, Sarvam and ffmpeg was
  found, and the README also lists a live end-to-end test as pending (`README.md:103`).
- The suite passes fully only when `ASHA_SESSION_SALT` is in the environment (section 11).

---

## 13. Handover and next phase

This section is for the developer who picks the project up. It covers how to run
everything, the rules to follow, the work list in order, and the things that must wait for
real data.

### 13.1 Getting started

Setup (Python 3.11 or newer; `ffmpeg` and `ffprobe` are needed only for voice):

```bash
cd asha-shahayak
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q                                    # 151 pass, 1 fails until Task 1 is done
ASHA_SESSION_SALT=test-salt pytest -q        # 152 pass
```

Run the API and the dashboard (one terminal):

```bash
export ASHA_DATABASE_PATH=$PWD/local.sqlite3
export ASHA_SESSION_SALT=local-test-salt
export ASHA_OFFICER_TOKEN=officer-token
export ASHA_API_KEY=local-api-key
uvicorn asha_shahayak.api:app --reload --port 8080
```

Run the standalone demo bot (second terminal). It must use the **same** database path and
the **same** salt, because registry phone numbers are stored as salted hashes:

```bash
export ASHA_DATABASE_PATH=$PWD/local.sqlite3
export ASHA_SESSION_SALT=local-test-salt
python -m demo_test_bot.main                 # port 8010
```

Load data through the dashboard:

1. Open `http://localhost:8080/dashboard?token=officer-token`. In a Codespace use the
   forwarded URL for port 8080 followed by `/dashboard?token=...`.
2. Use the "Upload ASHA data" form with a registry CSV and a status CSV (formats in
   section 5). The page at `/` returns 404 by design; there is no root page.
3. The form field names for the two files are defined in `dashboard.py:127`. Read them there
   before scripting an upload with `curl`.

Notes that save time:

- Without `ASHA_OFFICER_TOKEN` every dashboard request returns 401, and without
  `ASHA_API_KEY` every `/v1/*` request returns 401. Both are intended (fail closed).
- The demo bot uses the sender `"local-demo"`. To test registration in the demo, put
  `local-demo` as the `phone_number` of a row in the registry CSV, then send that ASHA's ID
  in the chat.
- A minimal pair of CSVs for a first test:
  - registry: `asha_id,phone_number` then `ASHA-1,local-demo`
  - status: `asha_id,month,head,claimed_amount,approved_amount,stage,stage_date` then
    `ASHA-1,march,vaccination,250,300,moic_pending,`
- The example message `vaccination july count 3 amount 250` produces expected 300
  (100 x 3), reported 250, a gap of 50 and a complaint draft.
- Webhook testing needs the five Meta values and a public HTTPS URL. Outbound replies need
  `WA_REPLY=1`.

### 13.2 Working rules

1. One task per branch and one pull request per task. Keep diffs small.
2. Every behaviour change ships with a test that fails before the change and passes after.
3. Never read, print or commit values from `.env`. Never put real phone numbers in the repo.
4. No new dependencies unless a task says so.
5. If a business rule is unclear, do not choose one silently. Leave a `TODO(handover)`
   comment and add the question to section 13.7.
6. Run both environments before opening a pull request:

```bash
pytest -q
ASHA_SESSION_SALT=test-salt pytest -q
```

Both must be green once Task 1 is done.

### 13.3 Parameters

Existing settings these tasks touch:

| Name | Where | Current behaviour | Notes |
|---|---|---|---|
| `ASHA_SESSION_SALT` | `config.py` | Empty by default. Empty disables registration, yes/no confirmation and registry import. | Tests must stop depending on it being in the environment. |
| `ASHA_OFFICER_TOKEN` | `config.py` | Empty means every dashboard request gets 401. | Keep fail-closed. |
| `ASHA_API_KEY` | `config.py` | Empty means every `/v1/*` request gets 401. | Keep fail-closed. |
| `ASHA_DATABASE_PATH` | `config.py` | Defaults to `asha_shahayak.sqlite3`. | No endpoint may expose it. |
| `WA_REPLY` | `os.environ` in `whatsapp.py` | `1` enables outbound replies. | Leave as is. Add to `.env.example`. |

New settings introduced by the tasks below:

| Name | Default | Used by |
|---|---|---|
| `ASHA_ENABLE_DEMO` | `false` | Task 3. Controls whether `/chat` and `/api/*` are mounted on the main app. |
| `ASHA_MAX_UPLOAD_BYTES` | `1048576` (1 MB) | Task 8. Per-file limit on dashboard uploads. |
| `ASHA_STORE_RAW_PAYLOAD` | `false` | Task 4. If true, the old behaviour (full webhook payload stored) is kept for debugging. |

Fixed values:

- Stages: `anm_pending`, `moic_pending`, `bam_pending`, `paid`.
- Rate types: `per_dose`, `per_visit`, `flat_monthly`.
- `stage_date`: ISO 8601 date `YYYY-MM-DD`, or empty.
- Test salt: `test-salt`.
- Complaint reason codes: unchanged (the five already in `dashboard.py`).

### 13.4 Task list

Order matters. Tasks 1 to 4 should be merged before anything else because they remove the
data exposure and make the test baseline reliable.

#### Task 1. Make the test suite independent of the environment

Problem: `tests/test_dashboard.py::test_combined_dashboard_upload_replaces_both_datasets`
fails when `ASHA_SESSION_SALT` is not set, because the `Settings` fixture (around line 15)
does not supply `session_salt`.

Change: pass `session_salt="test-salt"` in that fixture. Check the other fixtures that build
`Settings` and make them explicit about every field they rely on.

Acceptance:
- `pytest -q` with the variable unset: 152 passed.
- `ASHA_SESSION_SALT=test-salt pytest -q`: 152 passed.

#### Task 2. Do not return another ASHA's status when no ASHA is identified

Problem: sender binding applies only when the salt is set and the registry has entries
(`whatsapp.py`, around line 362). Otherwise `asha_id=None` reaches `StatusStore.find`
(`status.py`, around line 191), which returns the latest matching row of any ASHA.
Reproduced (section 12, item 4).

Change:
- `StatusStore.find(asha_id=None, ...)` matches only rows whose own `asha_id` is empty (rows
  from the old four-column format, which are block level). It must never fall back to the
  latest row of anyone.
- In `reconcile_saved_text`, when no ASHA is identified, skip the officer status step and
  return a flag such as `status_lookup="skipped_no_asha"`.
- In `build_reply`, when that flag is present, tell the sender in Hindi that payment status
  is available only after ASHA ID registration. State no status.
- `/v1/reconcile` is called with an API key, so an explicit `asha_id` parameter stays allowed
  there.

Acceptance:
- Test: empty registry plus a status row for ASHA-104; an unknown sender asks for
  vaccination status. The reply contains no amount, stage or date from that row.
- Test: registry present, registered sender, own row returned as before.
- Test: a legacy four-column row (no `asha_id`) is still returned when `asha_id=None`
  (see Q3).

#### Task 3. Lock down the demo endpoints

Problem: `/chat`, `/api/health`, `/api/text` and `/api/voice` are mounted on the main app
(`api.py`, around line 163) with no key or token. The selected ASHA ID is a module global
(`demo_test_bot/main.py`, lines 29 and 127). `/api/health` returns the database path
(line 198). `/api/voice` calls Sarvam unchecked and writes every upload to the fixed file
`/tmp/last_audio.bin` before checking for an empty body (line 219).

Change:
1. Add `ASHA_ENABLE_DEMO` (default false). `api.py` mounts the demo only when it is true.
   When true, log a startup warning that the demo has no authentication and must be used
   with synthetic data only.
2. Replace the global with per-session state: a random session ID in an HttpOnly cookie,
   mapped to the selected ASHA ID in a small in-memory dict with a TTL (30 minutes, as in
   `QueryMemory`). State must not be shared between sessions.
3. `/api/health` returns only `{"status": "ok"}`.
4. `/api/voice`: reject an empty body first; then write to `tempfile.NamedTemporaryFile`
   and delete in a `finally` block; apply the same size limit as Task 8.
5. The standalone demo binds to `127.0.0.1` by default.

Acceptance:
- Default settings: `GET /chat` and `POST /api/text` return 404 on the main app.
- With `ASHA_ENABLE_DEMO=true`: two clients with separate cookies do not see each other's
  selected ASHA.
- `/api/health` has no path in its response.
- `/api/voice` with an empty body returns 400 and creates no file.

Note: after this change anyone who types a registry ID still sees that ASHA's rows. That is
acceptable only with synthetic data, and Task 11 must say so in the docs.

#### Task 4. Stop storing the full WhatsApp webhook payload

Problem: `process_message` saves the entire payload into `intake_requests.payload_json`
(`whatsapp.py`, around line 521). It includes the sender's number and profile name. The
dashboard says phone numbers are not stored, which is true only for the registry. No code
deletes a user's data, but the privacy page promises deletion within 30 days.

Change:
- By default store a reduced record: external message ID, message type, timestamp, and the
  text body or media ID. Drop the sender number and profile name.
- Keep the old behaviour only when `ASHA_STORE_RAW_PAYLOAD=true`.
- Add `delete_by_sender_hash(sender_hash)` to the stores that keep per-sender data
  (`complaints`, `session`, the `registry` binding). Intake rows will carry no sender after
  this change. Expose it as an officer-only dashboard action or a command-line tool (Q5).
- Correct the sentence at `templates/dashboard.html:119` so it claims only what is true.

Acceptance:
- After a webhook message, `payload_json` contains neither the sender number nor the profile
  name.
- With `ASHA_STORE_RAW_PAYLOAD=true`, the full payload is stored.
- Deleting by sender hash removes that sender's complaint draft, confirmed query and binding,
  and nothing else.

#### Task 5. Fix replies that say the wrong thing

5a. Equal amount reported as "gap could not be determined". When reported equals expected,
`Gap.found` is False (`gaps.py`, line 17), so the "amount matches the rate card" branch
(`whatsapp.py`, line 204) never runs and the sender gets the "could not determine" text
(line 209). Add an explicit match state to the gap result (for example `gap.status` of
`match`, `under`, `over`, `unknown`) and branch on that instead of on `found`.

5b. False promise when `session_salt` is empty. `build_reply` always says a draft exists and
will not be filed without the sender's yes (line 203). With an empty salt the yes/no path is
off and the draft has no `sender_hash`. When the salt is empty, do not show the confirmation
prompt and say nothing about confirmation.

5c. Head choice prompt without a handler. When the activity is missing but the month is
known, the bot asks for the number of the right head (`whatsapp.py`, lines 386 and 189) and
nothing reads it. Until the flow is built (13.6), change the prompt to list the heads by
name and ask the sender to write the name.

Acceptance: one test per item. For 5a, the reply says the amount matches and does not
contain the "could not determine" text.

#### Task 6. Case-insensitive head matching

Problem: aliases are saved as written (`status.py`, line 111) but lookup casefolds
(line 165), so a row with "Vaccination" is never found. Reproduced.

Change: casefold and strip both `spoken_name` and `head` on import and on query. Keep the
original spelling for display if needed.

Acceptance: a CSV with "Vaccination", " HOME_VISIT " and "home_visit" resolves correctly and
produces one alias, not duplicates.

#### Task 7. Use `rate_type` in the production path

Problem: `calculate_amount` (`rules.py`, line 62) knows `flat_monthly` is not multiplied by
count, but `reconcile_saved_text` multiplies directly (`reconciliation.py`, line 37).

Change: call `calculate_amount` from `reconcile_saved_text`. Fail with a clear error when
`rate_type` is missing or not one of the three known values. Do not default to per-unit.

Acceptance:
- Through `/v1/reconcile`, a `flat_monthly` card with count 3 gives expected equal to the
  rate, not rate times 3.
- An unknown `rate_type` is rejected with 422.

Note: the sample card is not official. This task fixes how the card is applied, not what is
in it.

#### Task 8. Upload validation and the append routes

Problem: `/dashboard/data` has no size limit and no check for an empty `head` or a malformed
`stage_date` (`dashboard.py`, line 127; `status.py`, lines 105 to 109). `/dashboard/status`
and `/dashboard/registry` append, are not in the UI, and can leave duplicates that hide older
rows. The Upload button is disabled only in the browser.

Change:
- Enforce `ASHA_MAX_UPLOAD_BYTES` per file. Return 413.
- Reject an empty `head`. Reject a non-empty `stage_date` that is not `YYYY-MM-DD`.
- Reject duplicate `(asha_id, month, head)` inside one status file.
- Remove `/dashboard/status` and `/dashboard/registry`, or keep them but require an explicit
  `mode=append` and document the duplicate behaviour. Removal is preferred (Q4).
- Validation keeps running against a temporary database first, so a failed upload leaves
  existing data untouched.

Acceptance: tests for oversized file, empty head, bad date and duplicate key. Each returns an
error, and a later read shows the old data unchanged.

#### Task 9. Dashboard token handling

Problem: the officer token is accepted in the query string and repeated in redirects and
form actions (`dashboard.py`, lines 23, 90, 167; `dashboard.html`, line 69).

Change: add `POST /dashboard/login` that checks the token with `secrets.compare_digest` and
sets an HttpOnly, SameSite=Strict cookie (Secure when served over HTTPS). Keep the
`x-officer-token` header for scripts. Remove `?token=` support. Redirects and forms must not
carry the token. State-changing forms use a CSRF token or rely on SameSite=Strict; whichever
is chosen must be written in the pull request.

Acceptance: `?token=...` alone returns 401. Cookie and header both work. Redirect URLs
contain no token. An empty configured token still gives 401 everywhere.

#### Task 10. Phone hash

Problem: `sender_hash` is `sha256("{salt}:{number}")` (`session.py`, line 97). The salt
defaults to empty and nothing enforces a unique value per deployment.

Change: use HMAC-SHA256 with the salt as key, applied the same way in `session.py` and
`registry.py`. Refuse to start the app outside tests if the salt is shorter than 16
characters, instead of silently disabling registration.

Migration: changing the hash invalidates existing bindings, complaint sender hashes and
confirmed queries. While the system is on synthetic data the simplest path is a documented
reset. If real users exist when this is merged, write a one-off migration instead (Q6).

Acceptance: the hash differs between two salts for the same number; the same salt and number
give the same hash; a salt shorter than the minimum raises at startup.

#### Task 11. Documentation and configuration hygiene

- README: add `x-api-key` to the `curl` example (it returns 401 today) and describe
  `ASHA_API_KEY`, `ASHA_SESSION_SALT`, registration, `/chat` and the new settings. State
  that the demo is for synthetic data only.
- `.env.example`: add the Meta variables, `ASHA_DATABASE_PATH`, `WA_REPLY`,
  `ASHA_ENABLE_DEMO`, `ASHA_MAX_UPLOAD_BYTES`, `ASHA_STORE_RAW_PAYLOAD`. Names only.
- `.gitignore`: add `registry.csv` and `status.csv` at the repository root; move the sample
  data to a clearly named `data/sample/` folder with a note that it is synthetic.
- Use one service name. The privacy pages say "Asa Shayak", the code and README say "ASHA
  Shahayak".
- Add a minimal `Dockerfile` or deployment note that installs `ffmpeg` and `ffprobe` and
  states where the SQLite file lives and how it is persisted.
- Add a CI workflow that runs `pytest` in both environments, so the salt problem cannot come
  back.

#### Task 12. Dead code and misplaced handlers

- `CsvDataHandler` (`data_handler.py`) is created at startup and never read. Remove it or
  wire it in. A corrupt `registry.csv` and `status.csv` pair currently stops the app from
  starting, for no benefit.
- `evaluate_confidence` is not called in production. Either call it in `process_message` or
  mark the module as unused in its docstring.
- The `AudioTooShortError` handler sits in `send_reply`, where the error cannot occur
  (`whatsapp.py`, line 142). The error is raised in `voice_text` and swallowed by the generic
  handler. Move the handler so that a very short clip gets its own message.
- Replace the broad `except Exception` in `send_reply` and `_send_voice` with specific
  handling, or at least log with the receipt ID.

### 13.5 Suggested sequence

| Step | Tasks | Reason |
|---|---|---|
| 1 | 1 | Reliable baseline |
| 2 | 2, 3 | Close the two reproduced data exposure paths |
| 3 | 4 | Data minimisation and deletion |
| 4 | 5, 6, 7 | Correct replies and amounts |
| 5 | 8, 9, 10 | Validation, auth, hashing |
| 6 | 11, 12 | Cleanup and documentation |

### 13.6 Blocked on real field data

Do not start these from assumptions. Each needs samples from the people who use the system.

1. **Spoken names of payment heads.** The bot must not guess when a name is ambiguous. It
   should offer options and ask. The alias table cannot be finalised without real SMS or
   voucher samples collected from ASHA workers.
2. **Heads without extraction support.** `asha_incentive`, `institutional_delivery` and
   `family_planning` appear in the sample workbook but have no rate card entry and no
   extraction alias, so a WhatsApp claim cannot reach them.
3. **The numbered head-selection flow (Task 5c).** It needs short-lived per-sender state for
   the offered list, and a decision on how long that state lives.
4. **Real rate cards**, with `rate_type` set per head, from the official source.
5. **Payment stage meaning.** The record-based flow should report the stage the record is in
   (ANM, MOIC, BAM, paid) and the date on record. It should not say a payment is "delayed"
   unless the data supports it. The stage deadlines (voucher by the 20th, MOIC by the 28th,
   BAM payment around the 5th of the next month) must be confirmed with the block office
   before they are encoded anywhere.

Out of scope for now: Excel (`.xlsx`) import (export to CSV instead), any integration with a
government portal, replacing SQLite with PostgreSQL, and replacing the sample rate card with
official rates.

### 13.7 Open questions

| # | Question | Needed for |
|---|---|---|
| Q1 | Which exact wording should the "register first" and "no record linked" messages use? | Task 2 |
| Q2 | Should the demo exist in production at all, or only locally? | Task 3 |
| Q3 | Are legacy four-column rows block-level data that any sender may see? | Task 2 |
| Q4 | Remove `/dashboard/status` and `/dashboard/registry`, or keep them with an explicit append mode? | Task 8 |
| Q5 | Deletion by sender: dashboard button or command-line tool? | Task 4 |
| Q6 | Are there real users yet? If yes, Task 10 needs a migration, not a reset. | Task 10 |
| Q7 | Who owns the 30-day deletion promise on the privacy page, and how is it tracked? | Task 4 |

### 13.8 Verification gaps carried forward

- Tests use mocked Meta, Sarvam and ffmpeg. Write a short manual checklist (one text, one
  voice note, one yes/no confirmation, one registration) and run it once on a staging number
  before this phase is closed.
- There is no CI yet (Task 11).

### 13.9 Definition of done

- Tasks 1 to 10 merged, each with tests.
- `pytest -q` passes with and without `ASHA_SESSION_SALT`.
- The two reproduced exposures (empty registry, shared demo state) have regression tests.
- README, `.env.example` and `.gitignore` updated (Task 11).
- This report is regenerated against the final code, and the "not found" and "verified by
  running" markers are re-checked.
- Every question in 13.7 has an answer or an owner.
