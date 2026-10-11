# ASHA Sahayak

**Voice-first incentive transparency and grievance support for ASHA workers.**

ASHA workers are paid per activity, and the payment record sits with the block office. An ASHA who wants to know "has my incentive been paid, and if not, why?" often has to travel to ask. ASHA Sahayak lets her ask in Hindi, by voice or text, and answers from the same record the block officer uploaded.

> **No evidence, no claim.** The assistant never states an amount, date or payment status that the record does not support. An LLM is never allowed to decide money, dates or status.

Built for the Hack for Social Cause hackathon (Healthcare & Wellbeing). Team Aryavart.

## Important: what is real and what is a sample

- All data in this repository is **synthetic**. Phone numbers are dummy numbers.
- `data/sample_rate_cards.csv` is **sample only** and is **not** the official Uttar Pradesh rate card.
- The Sarvam (speech) and Meta (WhatsApp) integrations are covered by mock-based tests. A live end-to-end test with a verified WhatsApp Business number is still pending.
- Hindi/Awadhi speech accuracy has not been tested with real ASHA workers yet.
- Much of the code was written with AI coding assistants and then reviewed and tested by the team.

## Try it in 2 minutes

1. Open the Block Officer Dashboard (token-protected) and upload the two sample CSV files from [`data/sample_upload/`](data/sample_upload): `registry.csv` and `status.csv`.
2. Open the chat page: <https://asha-shahayak.onrender.com/chat>
3. Ask a question, for example `vaccination january count 3 amount 300`.
4. The reply comes only from the uploaded record. If there is no evidence, the assistant says so.

The API documentation is at <https://asha-shahayak.onrender.com/docs>. The demo runs on free hosting, so the first request can take up to a minute while the server wakes up.

## How it works

```
Hindi voice note or text
  -> ffmpeg (16 kHz mono WAV)
  -> Sarvam Saaras speech-to-text (voice only)
  -> rules-based extraction: activity, month, count, amount
  -> reconciliation against the rate card and the uploaded status record
  -> FactSet with evidence states
  -> template reply + output validator
  -> Sarvam Bulbul text-to-speech voice reply (optional)
```

- **Evidence states:** VERIFIED, SMS, DERIVED, REPORTED, UNKNOWN, CONFLICTING.
- **Output validator:** blocks any rupee amount, date or status in a reply that the evidence does not support. Blocked replies fall back to a safe text.
- **Grievances:** a gap between expected and reported amounts creates a complaint draft. It is filed only after the ASHA confirms, and she receives a receipt ID. Receipts are recorded in a hash-chained ledger.
- **Idempotency:** the external WhatsApp message ID is a unique key, so a replayed webhook never creates a second record.
- **Privacy:** phone numbers are hashed with a salt and are not stored in plain text. Raw voice bytes are deleted after transcription. Session memory is salted, hashed and expires.

## Officer dashboard

The dashboard shows ASHA-confirmed complaints, ledger entries and gap traces. A block officer uploads two CSV files, and the chatbot reads exactly this data.

Registry CSV (`asha_id,phone_number`):

```csv
asha_id,phone_number
ASHA-101,9876501001
```

Working status CSV, with this exact header:

```csv
asha_id,month,head,claimed_amount,approved_amount,stage,stage_date
ASHA-101,january,vaccination,300,300,paid,2026-01-28
```

- `month` is an English month name; amounts are whole numbers; `stage_date` is `YYYY-MM-DD` and may be empty.
- `stage` is one of `anm_pending`, `moic_pending`, `bam_pending`, `paid` (ANM voucher pending, MOIC approval pending, BAM payment pending, payment complete).
- The two files are validated together. A successful upload replaces the previous data. An invalid file leaves the existing data unchanged.
- Access needs `ASHA_OFFICER_TOKEN`. Never put the token in source control, screenshots or slides.

## Run locally

Requires Python 3.11+ and ffmpeg (with libopus) for voice conversion.

```bash
python -m pip install -e '.[dev]'
export ASHA_OFFICER_TOKEN=my-local-token
uvicorn asha_shahayak.api:app --reload
```

Open `http://127.0.0.1:8000/chat` and `http://127.0.0.1:8000/dashboard?token=my-local-token`.

Run the tests:

```bash
python -m pytest
```

Optional local demo data (every seeded row is labelled `DEMO`):

```bash
python seed_demo.py --database asha_shahayak.sqlite3
```

A standalone browser test bot is in [`demo_test_bot/`](demo_test_bot).

## Configuration

Set these as environment variables only. Never commit them. See `.env.example`.

| Variable | Purpose |
|---|---|
| `ASHA_OFFICER_TOKEN` | Protects the officer dashboard |
| `ASHA_SESSION_SALT` | Salt for hashing phone numbers and session memory |
| `ASHA_SARVAM_API_KEY` | Speech-to-text and text-to-speech (optional) |
| `ASHA_META_VERIFY_TOKEN`, `ASHA_META_ACCESS_TOKEN`, `ASHA_META_PHONE_NUMBER_ID`, `ASHA_META_APP_SECRET` | WhatsApp Cloud API webhook (optional) |
| `WA_REPLY=1` | Enables outgoing WhatsApp replies |
| `ASHA_REGISTRY_CSV_PATH`, `ASHA_STATUS_CSV_PATH` | Default CSV paths |

Without a Sarvam key, voice notes go to human callback. Text works without any external credentials.

## API

| Route | Purpose |
|---|---|
| `GET /chat` | Demo chat page |
| `GET /dashboard` | Officer dashboard (token required) |
| `POST /dashboard/registry`, `/dashboard/status`, `/dashboard/data` | Officer CSV uploads (token required) |
| `POST /v1/intake` | Durably records a request and returns a receipt |
| `POST /v1/reconcile` | Text claim -> extraction -> reconciliation -> gap trace |
| `POST /v1/complaints/{id}/decision` | Confirm or reject a complaint draft |
| `GET/POST /webhooks/whatsapp` | WhatsApp Cloud API webhook (signed) |

Full documentation: `/docs`.

## Repository layout

- `asha_shahayak/` application code (API, extraction, rules, FactSet, validator, ledger, dashboard, ASR/TTS, WhatsApp)
- `data/` sample rate card and sample CSVs
- `demo_test_bot/` standalone browser test bot
- `tests/` automated tests
- `Privacy policy/` privacy policy and data-deletion pages
- `PROJECT_REPORT.md` detailed code report

## Roadmap

- Pilot with real ASHA workers and a block office; test Hindi/Awadhi speech accuracy.
- Replace the sample rate card with the verified UP incentive schedule and connect real payment records.
- Verified WhatsApp Business number, IVR for basic phones, Bhashini as an alternative speech provider, PostgreSQL.

## License

MIT. See [LICENSE](LICENSE).
