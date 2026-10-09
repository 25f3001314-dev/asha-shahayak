# ASHA Shahayak: Project Report

यह रिपोर्ट repository में मौजूद code और data files पर आधारित है। जहाँ कोई
capability code में नहीं मिली, वहाँ **नहीं मिला** लिखा है। इस रिपोर्ट में
`.env` की कोई value शामिल नहीं है।

## 1. मकसद और पूरा flow

प्रोजेक्ट का घोषित उद्देश्य ASHA workers के payment claims को safety-first तरीके
से लेना है: package docstring में payment-claim intake लिखा है
(`asha_shahayak/__init__.py:1`) और FastAPI app खुद को durable,
confirmation-first intake बताता है (`asha_shahayak/api.py:48`).

### A. मुख्य FastAPI/WhatsApp flow

1. **WhatsApp webhook verification:** `GET /webhooks/whatsapp` configuration और
   verify token की जाँच करके challenge लौटाता है
   (`asha_shahayak/whatsapp.py:307`, `asha_shahayak/whatsapp.py:312`).
2. **Webhook authenticity:** `POST /webhooks/whatsapp` raw body पढ़ता है,
   `X-Hub-Signature-256` को app secret से HMAC-SHA256 द्वारा verify करता है और
   invalid signature पर `403` देता है (`asha_shahayak/whatsapp.py:321`,
   `asha_shahayak/whatsapp.py:330`; signature helper
   `asha_shahayak/whatsapp.py:78`).
3. **Immediate acknowledgement/background processing:** valid payload को
   background task में भेजकर `{"received": true}` लौटाया जाता है
   (`asha_shahayak/whatsapp.py:334`, `asha_shahayak/whatsapp.py:335`).
4. **Message extraction और intake receipt:** हर message का external WhatsApp
   message ID लिया जाता है; `IntakeStore.save_or_get` durable SQLite row और
   idempotent receipt बनाता है (`asha_shahayak/whatsapp.py:223`,
   `asha_shahayak/storage.py:34`, `asha_shahayak/storage.py:58`).
5. **Text path:** text message के लिए `reply_context_for_text` claim extraction,
   date gate, intent handling और reconciliation चलाता है
   (`asha_shahayak/whatsapp.py:237`, `asha_shahayak/whatsapp.py:213`).
6. **Audio path:** Meta media download होता है, audio को WAV/16 kHz mono में
   बदला जाता है, फिर configured ASR से transcript लिया जाता है
   (`asha_shahayak/whatsapp.py:248`, `asha_shahayak/whatsapp.py:69`;
   conversion `asha_shahayak/audio.py:8`).
7. **ASR failure path:** empty/failed voice transcript पर receipt ID के साथ
   retry/message fallback बनाया जाता है (`asha_shahayak/whatsapp.py:258`).
8. **Claim response:** successful reconciliation के बाद Hindi reply में receipt,
   expected amount, reported-vs-rate-card difference और complaint-draft status
   आता है (`asha_shahayak/whatsapp.py:157`, `asha_shahayak/whatsapp.py:167`).
9. **Outbound reply:** `WA_REPLY` के enabling value पर reply validation के बाद Meta text
   message भेजा जाता है; valid reply के लिए optional Hindi TTS voice note भी
   भेजा जाता है (`asha_shahayak/whatsapp.py:104`, `asha_shahayak/whatsapp.py:115`,
   `asha_shahayak/whatsapp.py:130`).

### B. Reconciliation flow

`POST /v1/reconcile` पहले text को `IntakeStore` में save करता है
(`asha_shahayak/api.py:84`, `asha_shahayak/api.py:90`). उसके बाद
`reconcile_saved_text`:

1. text extraction और emergency detection करता है
   (`asha_shahayak/reconciliation.py:14`, `asha_shahayak/extraction.py:177`);
2. invalid amount/extraction errors रोकता है
   (`asha_shahayak/reconciliation.py:17`, `asha_shahayak/reconciliation.py:22`);
3. sample rate-card CSV load करके applicable card चुनता है
   (`asha_shahayak/reconciliation.py:29`, `asha_shahayak/reconciliation.py:31`);
4. expected amount, gap और explain-trace बनाता है
   (`asha_shahayak/reconciliation.py:32`, `asha_shahayak/reconciliation.py:45`);
5. ledger entry लिखता है और gap होने पर trace लिखता है
   (`asha_shahayak/reconciliation.py:46`, `asha_shahayak/reconciliation.py:49`);
6. non-negative gap होने पर complaint draft बनाता है
   (`asha_shahayak/reconciliation.py:63`, `asha_shahayak/reconciliation.py:79`).

### C. Local demo browser flow

`demo_test_bot` standalone FastAPI app है (`demo_test_bot/main.py:25`).
Browser page text को `/api/text` और recorded/uploaded audio को `/api/voice` भेजता
है (`demo_test_bot/index.html:58`, `demo_test_bot/index.html:96`,
`demo_test_bot/index.html:126`). `/api/voice` raw body को `/tmp/last_audio.bin`
में लिखता है, Sarvam transcription करता है, फिर उसी bot reply path में भेजता है
(`demo_test_bot/main.py:106`, `demo_test_bot/main.py:108`,
`demo_test_bot/main.py:122`).

## 2. हर source file का काम

### `asha_shahayak/*.py`

- `asha_shahayak/__init__.py:1` — package का छोटा module docstring; package
  purpose बताता है।
- `asha_shahayak/api.py:1` — FastAPI app, request models, intake,
  reconciliation और complaint-decision endpoints।
- `asha_shahayak/asr.py:1` — ASR protocol, no-provider stub और Sarvam SDK
  adapter; `SarvamAsr.transcribe` `saaras:v3` चलाता है
  (`asha_shahayak/asr.py:18`).
- `asha_shahayak/audio.py:1` — ffmpeg से input audio को WAV/16 kHz mono और
  reply audio को OGG/Opus में convert करता है (`asha_shahayak/audio.py:8`,
  `asha_shahayak/audio.py:25`).
- `asha_shahayak/complaints.py:1` — complaint table बनाता है, drafts पढ़ता है,
  reason code save करता है और filed/rejected decision लिखता है
  (`asha_shahayak/complaints.py:7`, `asha_shahayak/complaints.py:62`).
- `asha_shahayak/confidence.py:1` — ASR/schema/business-rule evidence को
  high/medium/low band और action में classify करता है
  (`asha_shahayak/confidence.py:5`, `asha_shahayak/confidence.py:22`).
- `asha_shahayak/config.py:1` — Pydantic settings, aliases, database path,
  thresholds और Meta/Sarvam/session configuration define करता है
  (`asha_shahayak/config.py:5`, `asha_shahayak/config.py:9`).
- `asha_shahayak/dashboard.py:1` — officer-token protected dashboard routes,
  complaint reason update और status CSV upload संभालता है
  (`asha_shahayak/dashboard.py:22`, `asha_shahayak/dashboard.py:63`).
- `asha_shahayak/dates.py:1` — Hindi/Awadhi weekday और relative-day words को
  IST dates में resolve करता है; ambiguous same-weekday को दो candidates रखता है
  (`asha_shahayak/dates.py:22`, `asha_shahayak/dates.py:44`).
- `asha_shahayak/extraction.py:1` — activity/month aliases, Hindi/English
  number parsing, emergency detection और structured claim extraction
  (`asha_shahayak/extraction.py:10`, `asha_shahayak/extraction.py:134`,
  `asha_shahayak/extraction.py:177`).
- `asha_shahayak/factset.py:1` — reply में इस्तेमाल होने वाले facts को
  evidence state/source के साथ model करता है (`asha_shahayak/factset.py:7`,
  `asha_shahayak/factset.py:30`).
- `asha_shahayak/gaps.py:1` — expected और reported amount का difference तथा
  trace tuple बनाता है (`asha_shahayak/gaps.py:5`).
- `asha_shahayak/intent.py:1` — greeting, status, grievance और payment-status
  जैसे text intents classify करके safe canned replies देता है
  (`asha_shahayak/intent.py:3`, `asha_shahayak/intent.py:44`).
- `asha_shahayak/ledger.py:1` — hash-chain ledger entries और gap traces को
  SQLite में append करता है (`asha_shahayak/ledger.py:15`, `asha_shahayak/ledger.py:43`,
  `asha_shahayak/ledger.py:72`).
- `asha_shahayak/meta.py:1` — Meta Graph API के लिए WhatsApp text/audio
  send, media download और media upload client देता है
  (`asha_shahayak/meta.py:4`, `asha_shahayak/meta.py:20`, `asha_shahayak/meta.py:43`).
- `asha_shahayak/reconciliation.py:1` — extracted claim को rate card, gap,
  ledger, officer status और complaint draft से जोड़ता है
  (`asha_shahayak/reconciliation.py:14`).
- `asha_shahayak/rules.py:1` — CSV rate cards load करता है, month start निकालता
  है, applicable card चुनता है और rate × count amount निकालता है
  (`asha_shahayak/rules.py:12`, `asha_shahayak/rules.py:29`,
  `asha_shahayak/rules.py:47`).
- `asha_shahayak/session.py:1` — sender का आखिरी confirmed structured query
  salted hash के साथ short-lived SQLite memory में रखता है
  (`asha_shahayak/session.py:10`, `asha_shahayak/session.py:49`).
- `asha_shahayak/status.py:1` — officer status CSV validate/import करता है और
  activity/month से latest status lookup करता है
  (`asha_shahayak/status.py:34`, `asha_shahayak/status.py:64`).
- `asha_shahayak/storage.py:1` — incoming message evidence boundary, receipt
  generation और external-message idempotency संभालता है
  (`asha_shahayak/storage.py:8`, `asha_shahayak/storage.py:34`).
- `asha_shahayak/tts.py:1` — Sarvam Bulbul Hindi TTS call करता है और base64
  audio bytes लौटाता है (`asha_shahayak/tts.py:11`).
- `asha_shahayak/validator.py:1` — outgoing reply के amounts, dates और payment
  status को FactSet से validate करता है; failure पर safe fallback देता है
  (`asha_shahayak/validator.py:30`, `asha_shahayak/validator.py:66`).
- `asha_shahayak/whatsapp.py:1` — WhatsApp webhook, signature verification,
  message dispatch, ASR, replies और optional TTS orchestration।

### `demo_test_bot/`

- `demo_test_bot/__init__.py:1` — खाली package marker; कोई अतिरिक्त logic
  नहीं मिला।
- `demo_test_bot/main.py:21` — local demo FastAPI app, demo intake persistence,
  text/voice endpoints और Sarvam HTTP transcription।
- `demo_test_bot/index.html:31` — browser chat UI, microphone recording,
  audio-file upload और `/api/text`/`/api/voice` calls।
- `demo_test_bot/README.md:1` — local demo चलाने और Sarvam key की आवश्यकता का
  वर्णन।
- `demo_test_bot/demo.sqlite3` — demo bot का SQLite database file; schema
  declaration source में `demo_test_bot/main.py:21` के configured path से जुड़ी
  है। Binary DB के अंदर की प्रत्येक row का source line नहीं होता।

### Root/data files

- `seed_demo.py:8` — DEMO ledger gap, complaint और officer status rows seed करता
  है; default database path argument `seed_demo.py:23` पर है।
- `data/sample_rate_cards.csv:1` — तीन activities के sample rates और effective
  date; पहली line इसे official UP rates नहीं बताती।
- `README.md:1` — architecture foundation, demo flow, run instructions,
  dashboard और WhatsApp integration documentation।
- `pyproject.toml:1` — package metadata, runtime/dev dependencies, pytest
  configuration और package discovery।
- `asha_shahayak/templates/dashboard.html:1` — officer dashboard का Jinja HTML:
  pending complaints, ledger, gaps, reason forms और status CSV upload।
- `asha_shahayak.sqlite3:1` और `demo.sqlite3:1` — checked-in/runtime SQLite
  database artifacts; generated data का source code line नहीं मिला।

## 3. Database और direct SQLite calls

### Source-defined tables और columns

| Table | Columns | Definition |
|---|---|---|
| `intake_requests` | `receipt_id`, `source`, `external_message_id`, `received_at`, `payload_json` | `asha_shahayak/storage.py:27` |
| `complaints` | `complaint_id`, `receipt_id`, `draft`, `state`, `updated_at`; बाद में `reason_code` | `asha_shahayak/complaints.py:14`, migration `asha_shahayak/complaints.py:23` |
| `ledger_entries` | `sequence`, `receipt_id`, `created_at`, `payload_json`, `previous_hash`, `entry_hash` | `asha_shahayak/ledger.py:18` |
| `gap_traces` | `id`, `receipt_id`, `stage`, `rupees`, `trace_json` | `asha_shahayak/ledger.py:30` |
| `officer_status` | `id`, `activity`, `month`, `status`, `amount` | `asha_shahayak/status.py:19` |
| `confirmed_queries` | `sender_hash`, `activity`, `query_date`, `receipt_id`, `confirmed_at` | `asha_shahayak/session.py:28` |

Current checked-in SQLite snapshots में directly observed tables:
`asha_shahayak.sqlite3` में `complaints`, `gap_traces`, `intake_requests`,
`ledger_entries`, `officer_status`; `demo.sqlite3` में `complaints`,
`gap_traces`, `ledger_entries`, `officer_status`; और
`demo_test_bot/demo.sqlite3` में `complaints`, `gap_traces`,
`intake_requests`, `ledger_entries`, `officer_status`। `confirmed_queries`
का snapshot table इन files में नहीं मिला; वह session flow चलने पर source code
से बनता है (`asha_shahayak/session.py:25`).

### Direct `sqlite3` calls

- `asha_shahayak/storage.py:1`, `asha_shahayak/storage.py:20`,
  `asha_shahayak/storage.py:28`, `asha_shahayak/storage.py:45`,
  `asha_shahayak/storage.py:61` — intake connection, schema, select और insert।
- `asha_shahayak/complaints.py:1`, `asha_shahayak/complaints.py:28`,
  `asha_shahayak/complaints.py:34`, `asha_shahayak/complaints.py:44`,
  `asha_shahayak/complaints.py:52`, `asha_shahayak/complaints.py:63`,
  `asha_shahayak/complaints.py:71` — complaint schema/migration, insert,
  select और updates।
- `asha_shahayak/ledger.py:3`, `asha_shahayak/ledger.py:40`,
  `asha_shahayak/ledger.py:49`, `asha_shahayak/ledger.py:57`,
  `asha_shahayak/ledger.py:68`, `asha_shahayak/ledger.py:74`,
  `asha_shahayak/ledger.py:82` — ledger/gap schema, hash-chain reads और writes।
- `asha_shahayak/status.py:3`, `asha_shahayak/status.py:30`,
  `asha_shahayak/status.py:56`, `asha_shahayak/status.py:64`,
  `asha_shahayak/status.py:70` — status schema, CSV inserts और lookups।
- `asha_shahayak/session.py:4`, `asha_shahayak/session.py:39`,
  `asha_shahayak/session.py:53`, `asha_shahayak/session.py:70`,
  `asha_shahayak/session.py:81` — confirmed-query schema, upsert, select और TTL delete।
- Demo/tests में direct SQLite call भी हैं: `demo_test_bot/main.py:21` केवल
  configured database path देता है; test introspection
  `tests/test_whatsapp.py:125` और `tests/test_whatsapp.py:158` पर है।

## 4. Dashboard

Dashboard router app में include होता है (`asha_shahayak/api.py:54`).

| URL | Method | व्यवहार |
|---|---|---|
| `/dashboard` | GET | officer token verify करके pending complaints, ledger entries, gap traces और officer statuses render करता है (`asha_shahayak/dashboard.py:34`, `asha_shahayak/dashboard.py:42`) |
| `/dashboard/complaints/{complaint_id}/reason` | POST | allowed reason code save करके dashboard पर redirect करता है (`asha_shahayak/dashboard.py:49`, `asha_shahayak/dashboard.py:82`) |
| `/dashboard/status` | POST | uploaded CSV पढ़कर `StatusStore.import_csv` चलाता है और success/error message सहित वही page render करता है (`asha_shahayak/dashboard.py:63`, `asha_shahayak/dashboard.py:73`, `asha_shahayak/dashboard.py:88`) |

Authentication header `x-officer-token` या query parameter `token` से होती है;
comparison `secrets.compare_digest` से है (`asha_shahayak/dashboard.py:25`).
Dashboard template:

- pending complaint list और reason-code form
  (`asha_shahayak/templates/dashboard.html:7`, `asha_shahayak/templates/dashboard.html:12`);
- ledger receipt IDs, previous hashes और hashes
  (`asha_shahayak/templates/dashboard.html:24`);
- gap trace rows (`asha_shahayak/templates/dashboard.html:30`);
- officer status CSV upload form और status table
  (`asha_shahayak/templates/dashboard.html:36`, `asha_shahayak/templates/dashboard.html:38`).

## 5. Block payment sheet upload

**नहीं है** — block payment sheet के लिए Excel (`.xlsx`) upload या parsing code
नहीं मिला। `openpyxl`, `pandas.read_excel` और `.xlsx` handling नहीं मिली।

एक सीमित CSV रास्ता है, लेकिन वह **block payment sheet importer नहीं** है:
dashboard `/dashboard/status` पर multipart file लेता है
(`asha_shahayak/dashboard.py:63`, `asha_shahayak/dashboard.py:73`) और केवल exact
columns `activity, month, status, amount` स्वीकार करता है
(`asha_shahayak/status.py:7`, `asha_shahayak/status.py:40`). CSV UTF-8,
known month, non-negative integer amount और non-empty activity/status validate
होते हैं (`asha_shahayak/status.py:36`, `asha_shahayak/status.py:43`,
`asha_shahayak/status.py:47`).

## 6. `expected_amount` कैसे निकलता है और ₹300 कहाँ से आया

`reconcile_saved_text` हमेशा repository के `data/sample_rate_cards.csv` को
load करता है (`asha_shahayak/reconciliation.py:29`). CSV में vaccination
का rate `100` है (`data/sample_rate_cards.csv:2`). Claim से `count` निकाला जाता
है (`asha_shahayak/extraction.py:189`), applicable rate card चुना जाता है
(`asha_shahayak/rules.py:38`) और expected amount सीधे:

```text
expected = card.rate * claim.count
```

(`asha_shahayak/reconciliation.py:32`).

इसलिए transcript/request में `count 3` होने पर:

```text
₹100 × 3 = ₹300
```

यही seed/demo data में भी explicitly `expected_300_reported_250` के रूप में
लिखा है (`seed_demo.py:10`). Test/demo examples vaccination count 3 और amount
250 भी इस्तेमाल करते हैं (`tests/test_demo_pipeline.py:9`,
`demo_test_bot/index.html:31`). इसलिए ₹300 कोई hidden अलग rate नहीं है; वह
sample rate 100 को count 3 से multiply करने से आया है। Rate card file खुद
sample-only है (`data/sample_rate_cards.csv:1`).

## 7. दो अलग receipt IDs क्यों दिखती हैं

दो अलग IDs दो अलग layers बनाती हैं:

1. **Intake receipt:** `IntakeStore.save_or_get` source message के लिए
   `ASHA-` prefix और UUID के पहले 12 uppercase hex characters बनाता है
   (`asha_shahayak/storage.py:58`). यह ID `intake_requests.receipt_id` में
   रहती है (`asha_shahayak/storage.py:27`).
2. **Ledger receipt:** `Ledger.append` claim ledger entry के लिए फिर से अलग
   `ASHA-` + नया UUID बनाता है (`asha_shahayak/ledger.py:45`). यह
   `ledger_entries.receipt_id` में रहती है (`asha_shahayak/ledger.py:18`).

Reconciliation में ledger ID result का `receipt_id` बन जाती है, जबकि original
intake ID अलग field `intake_receipt_id` में रखी जाती है
(`asha_shahayak/reconciliation.py:46`, `asha_shahayak/reconciliation.py:54`).
इसलिए reply के ऊपर दिखने वाली entry/ledger receipt और नीचे UI द्वारा जोड़ी गई
`Receipt: ...` value अलग layers से आ सकती हैं। Local demo text endpoint भी
पहले demo intake receipt बनाता है (`demo_test_bot/main.py:31`) और फिर
`reply_context_for_text` को वही receipt देता है (`demo_test_bot/main.py:41`);
reconciliation के बाद reply में result का ledger receipt इस्तेमाल होता है
(`asha_shahayak/whatsapp.py:162`, `asha_shahayak/whatsapp.py:167`).

## 8. `audio.py`, `asr.py`, Sarvam और Bhashini

- `audio.py` provider call नहीं करता; वह ffmpeg conversion utility है
  (`asha_shahayak/audio.py:8`, `asha_shahayak/audio.py:25`).
- `asr.py` `sarvamai.AsyncSarvamAI` import करता है
  (`asha_shahayak/asr.py:3`). `SarvamAsr` client बनाकर `speech_to_text.transcribe`
  को `saaras:v3`, `hi-IN`, WAV input के साथ call करता है
  (`asha_shahayak/asr.py:18`, `asha_shahayak/asr.py:23`).
- WhatsApp path पहले `to_wav_16k` चलाता है और फिर `get_asr` से `SarvamAsr`
  या no-key `AsrStub` चुनता है (`asha_shahayak/whatsapp.py:64`,
  `asha_shahayak/whatsapp.py:69`).
- TTS अलग module में Sarvam Bulbul v3 call करता है
  (`asha_shahayak/tts.py:1`, `asha_shahayak/tts.py:17`).
- Local demo का voice path अलग direct HTTP request करता है और
  `saarika:v2.5` भेजता है (`demo_test_bot/main.py:49`, `demo_test_bot/main.py:54`).
- **Bhashini का कोई code नहीं मिला।** `Bhashini`, `bhashini` या Bhashini API
  import/URL नहीं मिला।

## 9. Complaint कब और कैसे बनती है

`reconcile_saved_text` में gap निकाला जाता है (`asha_shahayak/reconciliation.py:45`).
यदि `gap.found` true हो और `gap.rupees >= 0` हो, तो complaint ID
`complaint-` + UUID से बनती है (`asha_shahayak/reconciliation.py:63`).
इसका मतलब:

- reported amount missing हो तो positive gap बन सकता है और draft बनता है
  (`asha_shahayak/gaps.py:12`, `asha_shahayak/reconciliation.py:63`);
- underpayment (`expected > reported`) पर draft बनता है
  (`asha_shahayak/gaps.py:22`);
- overpayment (`reported > expected`, negative rupees) पर gap record होता है,
  पर complaint draft नहीं बनता (`asha_shahayak/gaps.py:15`,
  `asha_shahayak/reconciliation.py:63`).

Draft में ledger receipt, activity, month, count, rate, expected/reported amount,
difference और rate-card source होता है (`asha_shahayak/reconciliation.py:68`).
Initial state `draft` है (`asha_shahayak/complaints.py:36`). Dashboard reason
code केवल pending draft पर save करता है (`asha_shahayak/complaints.py:52`).
Decision endpoint `confirm=true` पर `filed`, `false` पर `rejected` लिखता है
(`asha_shahayak/api.py:113`, `asha_shahayak/complaints.py:62`).

## 10. Environment variables — सिर्फ़ नाम

Code/config में मिले नाम:

- `ASHA_DATABASE_PATH`
- `ASHA_CONFIDENCE_HIGH_THRESHOLD`
- `ASHA_CONFIDENCE_MEDIUM_THRESHOLD`
- `ASHA_RECEIPT_PREFIX`
- `ASHA_OFFICER_TOKEN`
- `ASHA_META_VERIFY_TOKEN`
- `WHATSAPP_VERIFY_TOKEN`
- `ASHA_META_ACCESS_TOKEN`
- `WHATSAPP_ACCESS_TOKEN`
- `ASHA_META_PHONE_NUMBER_ID`
- `WHATSAPP_PHONE_NUMBER_ID`
- `ASHA_META_APP_SECRET`
- `WHATSAPP_APP_SECRET`
- `ASHA_META_GRAPH_VERSION`
- `WHATSAPP_GRAPH_VERSION`
- `ASHA_SARVAM_API_KEY`
- `ASHA_SARVAM_API_URL`
- `ASHA_SESSION_SALT`
- `SESSION_SALT`
- `WA_REPLY`

पहले 19 `Settings` fields/env-prefix या explicit aliases से आते हैं
(`asha_shahayak/config.py:9`, `asha_shahayak/config.py:13`,
`asha_shahayak/config.py:39`). `WA_REPLY` सीधे process environment से पढ़ा
जाता है (`asha_shahayak/whatsapp.py:106`). `.env.example` में केवल
`ASHA_SARVAM_API_KEY` का नाम मिला (`.env.example:1`). `.env` की values इस
रिपोर्ट में जानबूझकर नहीं लिखी गई हैं।

## 11. Tests और वर्तमान pytest result

`pyproject.toml` pytest को `tests` directory और repository root Python path
पर चलाता है (`pyproject.toml:26`).

- `tests/test_api.py:22` — intake receipt और duplicate idempotency।
- `tests/test_audio.py:11` — ffmpeg absent/failure fallback और real 16 kHz
  conversion।
- `tests/test_complaints_api.py:9` — complaint confirm से पहले filed नहीं होता।
- `tests/test_confidence.py:10` — confidence bands, thresholds और invalid score।
- `tests/test_dashboard.py:20` — dashboard auth, ledger view, bad CSV/month,
  seeded demo और complaint pending state।
- `tests/test_date_confirmation.py:10` — ambiguous/clear/no date behavior।
- `tests/test_dates.py:7` — Hindi weekday, relative date और ambiguity।
- `tests/test_demo_extraction.py:9` — clean/Devanagari/absurd/unrecognized
  extraction और emergency।
- `tests/test_demo_pipeline.py:9` — evidence-backed reply, noisy speech gate
  और blocked fallback।
- `tests/test_extraction.py:6` — Hindi/Awadhi numbers, Devanagari digits,
  aliases, normalization, emergency और range checks।
- `tests/test_extraction_dates.py:8` — weekday से date/month resolution।
- `tests/test_factset.py:9` — evidence states, reported/derived amounts,
  unknown payment और conflicting sources।
- `tests/test_intent.py:10` — follow-up/status/grievance/greeting intent और
  safe unknown replies।
- `tests/test_ledger.py:4` — hash chain और unique ledger receipts।
- `tests/test_meta.py:27` — Meta text API bearer request।
- `tests/test_reconciliation.py:16` — sample rate card, rate selection, gap
  traces, missing amount/month, complaint और overpayment।
- `tests/test_session.py:9` — query memory save/read, TTL, sender isolation और
  non-plain sender storage।
- `tests/test_tts.py:10` — Sarvam Bulbul request और failure fallback।
- `tests/test_validator.py:5` — unsupported status/amount blocking, matching
  amount और safe fallback।
- `tests/test_whatsapp.py:83` — webhook auth/signature, text/audio intake,
  dedupe, ASR failures, Meta env aliases, validator, TTS and voice replies।

मैंने वर्तमान repo पर plain `pytest -q` चलाया। परिणाम:

```text
115 passed, 227 warnings in 2.11s
```

Failures: **0**. Warnings में pytest-asyncio event-loop deprecations और
Starlette/httpx deprecation शामिल हैं (test run output).

## 12. अधूरा, अटका हुआ या जोखिम वाला

### Hardcoded/sample behavior

- Production reconciliation हमेशा `data/sample_rate_cards.csv` hardcode करता
  है (`asha_shahayak/reconciliation.py:29`); CSV खुद sample-only कहती है
  (`data/sample_rate_cards.csv:1`). इसलिए expected amount official government
  rate होने का दावा नहीं किया जा सकता।
- `month_start` future month को previous year में map करता है
  (`asha_shahayak/rules.py:29`); यह policy code में है, external verified
  calendar/rate source नहीं मिला।
- Local demo audio हर upload को `/tmp/last_audio.bin` में लिखता है
  (`demo_test_bot/main.py:108`); filename fixed है और concurrent users के लिए
  safe isolation नहीं दिखती।

### Incomplete or potentially misleading flow

- `evaluate_confidence` स्वतंत्र module में मौजूद है
  (`asha_shahayak/confidence.py:22`), लेकिन main WhatsApp processing इसे call
  नहीं करती; `process_message` transcript/error path से सीधे
  `reply_context_for_text` में जाता है (`asha_shahayak/whatsapp.py:248`,
  `asha_shahayak/whatsapp.py:253`). Main path में confidence score का
  end-to-end enforcement नहीं मिला।
- Read-back text बनता है, पर WhatsApp process flow में incoming “हाँ/नहीं” को
  complaint decision endpoint से automatically जोड़ने वाला code नहीं मिला।
  Complaint decision केवल explicit HTTP endpoint है
  (`asha_shahayak/api.py:113`).
- Complaint `filed` state database में set हो जाती है, पर external government
  filing API/integration नहीं मिला (`asha_shahayak/complaints.py:62`).
- Officer status import केवल CSV है; block payment sheet Excel importer नहीं
  मिला (`asha_shahayak/status.py:34`).
- `AsrStub` configured provider न होने पर `NotImplementedError` देता है
  (`asha_shahayak/asr.py:11`); यह deliberate fallback है, production ASR नहीं।
- `audio.py` ffmpeg missing/failure पर original bytes वापस भेज देता है
  (`asha_shahayak/audio.py:9`, `asha_shahayak/audio.py:20`), जिससे ASR को
  unsupported input मिलने का जोखिम है।

### Security/data risks

- Dashboard token query string में स्वीकार और redirect किया जाता है
  (`asha_shahayak/dashboard.py:25`, `asha_shahayak/dashboard.py:60`); इससे
  token browser history/server access logs/referrer में leak हो सकता है।
- Dashboard token का default empty है (`asha_shahayak/config.py:13`), और
  `officer_token` के खाली रहने पर `compare_digest` में empty supplied value
  व्यवहार को production deployment configuration पर निर्भर बनाता है
  (`asha_shahayak/dashboard.py:25`). Secure deployment में non-empty secret
  enforce करने का code नहीं मिला।
- `send_reply` broad `except Exception` पर warning देकर return करता है
  (`asha_shahayak/whatsapp.py:121`); outbound failure caller को structured
  error नहीं मिलता।
- `_send_voice` भी broad exception catch करता है
  (`asha_shahayak/whatsapp.py:130`); voice failure text flow को नहीं रोकता,
  लेकिन diagnosis केवल log तक सीमित है।
- Local demo `open("/tmp/last_audio.bin", "wb")` करता है
  (`demo_test_bot/main.py:108`) और raw audio cleanup code नहीं मिला।
- Meta client के `_headers` में actual token दिखाने के बजाय masked literal
  `"******"` है (`asha_shahayak/meta.py:15`); यह source leak से बचाता है, लेकिन
  इस checkout में real outbound Meta call की operational correctness सीधे
  verify नहीं हुई।
- `QueryMemory` sender का salted hash रखता है, plain sender नहीं
  (`asha_shahayak/session.py:43`), लेकिन `session_salt` empty हो सकता है
  (`asha_shahayak/config.py:39`); unique per-deployment salt enforce नहीं मिला।

### Verification gaps

- Tests mocked integrations के साथ हैं; real Meta/Sarvam/ffmpeg deployment
  end-to-end test नहीं मिला। README भी live end-to-end test pending बताता है
  (`README.md:78`).
- Existing tests 115/115 pass हैं, लेकिन deprecation warnings मौजूद हैं; warning
  cleanup के लिए code change इस report के scope में नहीं किया गया।
