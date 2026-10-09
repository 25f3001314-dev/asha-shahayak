# Local demo test bot

This standalone browser bot tests the ASHA Shahayak text flow without WhatsApp.
It also records a microphone voice note and sends it to the existing Sarvam
speech-to-text integration when `ASHA_SARVAM_API_KEY` is configured.

From the repository root:

```bash
python -m demo_test_bot.main
```

Open <http://127.0.0.1:8010> locally, or open the forwarded port 8010 URL in
Codespaces. Try a text message such as:

```text
vaccination july count 3 amount 250
```

Text testing works without external credentials. Voice transcription requires
`ASHA_SARVAM_API_KEY`; credentials stay in the environment and are not stored
by this demo. Demo records are stored in `demo_test_bot/demo.sqlite3`.
