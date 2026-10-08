# ASHA Shahayak

Safety-first payment claim intake for ASHA workers.

## Current foundation

- `POST /v1/intake` durably records a WhatsApp, IVR, or SMS request before
  returning `202 Accepted`.
- Every request receives a receipt such as `ASHA-4C8A0F13E2B1`.
- The external message ID is an idempotency key. Replays return the original
  receipt with `"duplicate": true` and do not create a second row.
- `asha_shahayak.confidence.evaluate_confidence` is a pure, independently
  testable gate. It never calculates payment amounts.
- Confidence thresholds are configuration values, not literals in the gate:
  `ASHA_CONFIDENCE_HIGH_THRESHOLD` and
  `ASHA_CONFIDENCE_MEDIUM_THRESHOLD`.

The current local store uses SQLite to make the durable-ingestion boundary
executable without provisioning infrastructure. The storage interface is
intentionally isolated so it can be replaced with PostgreSQL before pilot
deployment. The reconciliation path is text-first; ASR (Sarvam Saaras) and Hindi TTS are
optional integrations that need a Sarvam key. `data/sample_rate_cards.csv` is clearly marked sample-only and is not an
official UP rate card.

## Demo flow

1. Send text to `POST /v1/reconcile`; fixed-list extraction parses activity,
   month, count, and spoken amount.
2. The pure rules engine reads the sample CSV, then the hash-chain ledger
   records the claim and a gap trace compares expected versus reported rupees.
3. A gap creates a complaint draft; only
   `POST /v1/complaints/{complaint_id}/decision` with `{"confirm": true}` marks
   it filed.

## Run locally

```bash
python -m pip install -e '.[dev]'
uvicorn asha_shahayak.api:app --reload
```

Example:

```bash
curl -X POST http://127.0.0.1:8000/v1/intake \
  -H 'content-type: application/json' \
  -d '{"source":"whatsapp","external_message_id":"wamid-123","payload":{"text":"pachaas"}}'
```

Run tests with:

```bash
python -m pytest
```

## Officer dashboard

Set a token before starting the server:

```bash
export ASHA_OFFICER_TOKEN=my-local-token
uvicorn asha_shahayak.api:app --reload
```

Open `http://127.0.0.1:8000/dashboard?token=my-local-token`. The page reads
pending complaints, ledger hashes, gap traces, and uploaded officer status rows
from SQLite. Upload CSVs with exactly `activity,month,status,amount`; invalid
columns or months show a clear error. For local-only demo data, run
`python seed_demo.py --database asha_shahayak.sqlite3`; every seeded row is
labelled `DEMO`.

## WhatsApp voice/text webhook

Never put these values in source control. Set them only in the environment:

```bash
export ASHA_META_VERIFY_TOKEN=local-verify-token
export ASHA_META_ACCESS_TOKEN=meta-test-token
export ASHA_META_PHONE_NUMBER_ID=your-test-number-id
export ASHA_SARVAM_API_KEY=your-sarvam-key
```

Start the app, then expose port `8000` from Codespaces using the forwarded-port
URL, or run `ngrok http 8000`. In Meta's test WhatsApp number settings use
`https://YOUR-PUBLIC-URL/webhooks/whatsapp` and the verify token above. The
sender must first join Meta's test number. Webhook messages are deduplicated
by WhatsApp message ID, receipt acknowledgement is sent immediately, and
voice failures go to human callback. Raw voice bytes are removed after ASR.

### Voice replies and requirements

- **ASR:** voice notes are transcribed with Sarvam Saaras when
  `ASHA_SARVAM_API_KEY` is set. Without a key, voice goes to human callback.
- **Voice reply:** replies are sent only when `WA_REPLY=1`. The validated text
  is sent first; then a best-effort Hindi voice note (Sarvam TTS, converted to
  OGG/Opus) follows. If TTS, conversion or upload fails, the text reply still
  stands. Blocked replies get the safe text fallback and no voice note.
- **ffmpeg with libopus** must be installed for voice conversion.
- **Status:** the Sarvam and Meta integrations are mock-tested. A live
  end-to-end test with real credentials is still pending.
- **Officer dashboard:** set `ASHA_OFFICER_TOKEN`; there is no default.
