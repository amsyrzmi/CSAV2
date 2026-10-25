# Console voice

The migrated console no longer runs the copied n8n upload flow or its separate
voice listener. Recorded-message mode transcribes bounded audio through an
explicitly configured service and places the transcript in the text box for
review. Hands-free mode, with separate consent, submits finalized utterances
through the same redacted sandbox turn path and reads replies using a local
browser voice. Its microphone is off during thinking/playback; interruption by
speech and continuous LiveKit rooms remain future work.

Telegram voice-note transcription is separately implemented in the ingress worker.
It downloads audio from Telegram, transcribes and redacts it, then caches the result
or failure for worker replay. Existing coordinator/attachment review rules apply;
this does not provide Telegram calls or spoken Telegram replies.

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
Verify persistent migration/readiness and development provider configuration
separately before live use; source and synthetic checks do not establish rollout.

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
- Recorded-message transcripts require review and manual Send. Hands-free mode
  requires explicit consent to automatic submission and local reply playback.
  Both modes remain within the sandbox's existing capability restrictions.
- Hands-free and text send share a pending-request record. While a voice request
  is unresolved, ordinary Send, new conversations, selection and other input modes
  remain locked. Background history polling cannot release that voice recovery
  lock. Resume uses the original audio, transcript and key; explicit discard
  removes local recovery without cancelling an accepted message. Existing typed
  drafts are preserved and never replaced with an uncertain voice transcript.
- Hands-free configuration/status/turn requests have individual 15-second bounds,
  including preceding CSRF requests; audio upload has a 45-second bound.
  Transcription and reply waits also enforce overall 95/100-second deadlines
  across individual requests and polling pauses. Stop aborts active requests;
  later recovery keeps the same key. A session has a 15-minute overall bound.

## Interactive session foundation

Migration `0430` and `csa/voice/` add the trusted session/utterance layer for the
planned LiveKit conversation. It is disabled unless `VOICE_INTERACTIVE_ENABLED=true`
with `VOICE_LIVEKIT_URL`, `VOICE_LIVEKIT_API_KEY`, `VOICE_LIVEKIT_API_SECRET` and a
separate `VOICE_MEDIA_HMAC_SECRET` (both secrets at least 32 characters). The URL
must be WSS, or a loopback WS URL outside production. When disabled the session
routes answer 503. No media worker, browser LiveKit connection, STT/TTS wiring or
interruption exists yet; this slice only defines and persists authority.

- `POST /console/v1/chat/voice/sessions` creates a sandbox-profile session bound
  server-side to the human writer's user, tenant, login session and exact private
  conversation. Explicit `consent: true`, a request key and `en`/`ms` language are
  required. The same key replays the existing session; different settings return 409.
  Sessions expire with the login session's idle/absolute bounds, capped at 15 minutes.
- `GET .../{session_id}`, `POST .../end` and `GET .../utterances/{utterance_id}` report
  status, end a session with an audit entry, and read an accepted receipt. All
  checks revalidate the actor through `voice_lock_actor()`, which holds share locks
  on the user, login session and membership for the transaction and returns no
  authority once the user is disabled, the session revoked/expired/idle, or the
  membership removed. Platform admins keep tenant-scoped access; readers are refused.
- `POST .../connection` mints a room-specific LiveKit participant token limited to
  microphone publish and audio subscribe, with a 60-second TTL clipped to the
  session expiry, a two-participant room and a `csa-voice` agent dispatch carrying
  only the session and tenant IDs. Provider credentials stay server-side.
- `POST /internal/voice/sessions/{session_id}/state` and
  `.../utterances/{utterance_id}` are media-worker callbacks on the gateway, outside
  the console router and its OpenAPI snapshot. They refuse cookies or bearer
  headers and require an HMAC over method, path, a timestamp within 30 seconds and
  the body hash; bodies are capped at 32 KB and 15 seconds. A finalized utterance
  derives its request key from the session and utterance IDs and enqueues one
  ordinary sandbox turn through the shared `enqueue_sandbox_turn` path inside the
  same transaction. Duplicate finals return the stored receipt; different text for
  the same utterance returns 409. A queue failure rolls back the receipt.
- Tables carry tenant RLS (not forced), composite foreign keys to the login
  session, conversation and queued turn, and `csa_app`-only grants. Expose the
  internal route only to the media worker; the public proxy must not forward it.

## Acceptance

Use synthetic audio and mocked provider HTTP with disposable PostgreSQL for
`tests/test_console_voice.py`, migration, readiness and contract checks.
`tests/test_voice_sessions.py` covers session replay, media HMAC rejection, token
grants, expiry/logout/membership revocation, atomic utterance queueing with rollback,
tenant hiding and composite key/privilege checks against a disposable database. Run the
isolated frontend checks and `tests/test_voice_browser.py`. The browser test mounts
the real sandbox with synthetic microphone, playback and transport to check
lost acknowledgements, Stop, a hung accepted request, recovery, draft preservation
and discard. Real microphone/audio quality, multilingual transcription, provider
retention and live service connectivity remain rollout acceptance items. See the
[recovery evidence](handoffs/2026-10-24-voice-recovery-audit.md).
