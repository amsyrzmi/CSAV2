# Recorded console voice

The migrated console no longer runs the copied n8n upload flow or its separate
voice listener. This implementation records a bounded voice message in the sandbox,
transcribes it through an explicitly configured service, and places the transcript
in the text box for review. Sending uses the existing redacted sandbox turn path.
It does not implement continuous LiveKit rooms or Telegram voice ingress.

## Configuration and rollout

Apply ordered migration `0420` through the normal migration process, then configure
`VOICE_STT_ENDPOINT` with the full transcription POST URL, optional
`VOICE_STT_API_KEY`, and `VOICE_STT_MODEL`. The provider must accept multipart
`file`, `model`, and `response_format=json`, and return JSON containing `text`.
This is the documented [Groq transcription contract](https://console.groq.com/docs/speech-to-text),
which also allows a compatible self-hosted service. No cloud endpoint is selected
by default. Production endpoints require HTTPS. Provider requests never follow
redirects, and neither endpoint nor key is exposed to the browser.
The user selected Groq: the dev environment endpoint and model are configured.
For exactly `https://api.groq.com/openai/v1/audio/transcriptions`, the server uses
its existing `GROQ_API_KEY` when no separate `VOICE_STT_API_KEY` is supplied. That
key is never reused for other URLs. No secret was printed or copied.
No persistent migration, service restart, or live call was performed.

## Behavior and limits

- `GET /console/v1/chat/voice` reports configuration availability to authenticated
  humans. Browser speech dictation remains separate and local.
- `POST /console/v1/chat/conversations/{conversation_id}/voice/{request_key}` accepts
  raw audio with its `Content-Type`. Supported MIME types are WebM, Ogg, MP4, WAV
  and MPEG audio. The streaming upload is limited to 8 MB and 15 seconds; browser
  recording stops after 60 seconds. The server does not validate audio duration.
- A human writer session, CSRF and exact private conversation ownership are
  required. Machine credentials cannot submit audio. Tenant-composite foreign
  keys and staged RLS protect persisted jobs; RLS is not forced.
- A job and its audit entry commit before the provider call. The audio is held in
  memory for that call; no filesystem audio or public signed download URL is used.
  The provider's own retention policy must be reviewed before enabling it.
- The provider has a 5-second connection timeout and 35-second overall bound.
  Responses are capped at 64 KB; transcripts must be nonblank and at most 4000
  characters. Provider payloads/errors are not returned to users.
- The same key and audio return the existing job. Different audio with that key
  returns 409. Concurrent submissions invoke the provider only once. Provider
  failures and timeouts are final for that key; starting again requires an explicit
  new recording. A crash after job creation leaves a recoverable processing state.
- `GET` on the same job path checks persisted state. Processing jobs become timed
  out after 90 seconds when accessed. Completed transcripts become inaccessible
  after an hour, and the next voice read/write for that user clears their expired
  stored transcripts. This is access-triggered cleanup, not scheduled erasure.
- An uncertain upload retains the recording and key in component memory so the user
  can check status, then explicitly retry only if no job exists. Reloading the page
  discards that client recovery handle; there is no reload recovery UI in this slice.
- No transcript is automatically sent to chat, and no transcription creates a
  business action or customer delivery. Recorded voice requires explicit consent
  to the configured transcription service.

## Acceptance

Use synthetic audio and mocked provider HTTP with disposable PostgreSQL for
`tests/test_console_voice.py`, migration, readiness and contract checks. Run the
isolated frontend checks. Real microphone/browser, multilingual transcription,
provider retention and live service connectivity remain rollout acceptance items.
