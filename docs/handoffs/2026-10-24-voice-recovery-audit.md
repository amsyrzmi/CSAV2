# Voice work recovery audit — 2026-10-24

## Stabilization follow-up — current working tree

The authorized first fix slice is implemented in the working tree:

- Voice and ordinary text send share a source-tagged pending request. A voice
  transcript remains locked during uncertain acceptance, even after history
  polling observes it. Ordinary Send cannot create another key for that transcript.
  Resume preserves the original key/body; explicit discard preserves the user's
  earlier typed draft and does not cancel an accepted message.
- Voice requests have individual timeouts, including CSRF, and their overall
  transcription/reply deadlines apply across network waits. Stop aborts them
  without changing the recovery identity.
- The real sandbox is now mounted in the browser regression. It checks lost
  acknowledgement, Stop after acceptance, background polling, ordinary Send,
  same-key resume, two-turn playback, microphone cleanup and discard. Browser
  stderr, console errors and Playwright traces are available on a failure.

Initial post-fix integrated browser validation: **1 passed in 106.47 seconds**
(lost acknowledgement, Stop and discard scenarios). Final focused browser
validation: **1 passed in 189.49 seconds**, covering lost acknowledgement, Stop,
a hung accepted request that times out, and discard. Focused frontend checks:
**23 passed**, plus focused ESLint. The final full-suite run uses this settled source.
Final repository lint and the final isolated frontend tests/lint/types/build/trace
checks passed. Full-suite results are pending. Logs: `/tmp/csav2-voice-fix-validation.log`,
`/tmp/csav2-voice-fix-browser.log` and `/tmp/csav2-voice-fix-frontend.log`. Preserve the earlier failed/crashed browser
evidence below; no cause for those historical crashes has been established.

No persistent service was restarted, no persistent database was reset, and no live
provider was called for this fix. Full interactive LiveKit sessions remain the
next planned phase, not part of this stabilization patch.

## Interactive session foundation — current working tree

The interactive plan's second slice (trusted session/utterance contracts) was
implemented in the same working tree on 2026-10-24 after the stabilization patch,
but was not recorded until the 2026-10-25 review. It consists of untracked
`csa/voice/` (contracts, media HMAC, service), `csa/console/voice_sessions.py`,
migration `0440_console_voice_sessions.sql`, eight `db/sql/console/voice_session_*`
and `voice_utterance_*` files, `tests/test_voice_sessions.py`, an exactly pinned
`livekit-api==1.2.2` dependency with lockfile update, `VOICE_INTERACTIVE_*` settings,
and a refactor of `csa/console/chat.py` that exposes `enqueue_sandbox_turn` so media
finals and text sends persist through one path. Gateway and migration tests now
expect schema `0440`. Behaviour is documented in
[the voice contract](../CONSOLE_VOICE.md#interactive-session-foundation).

Validation on 2026-10-25 against this settled source with `NO_LOCAL_ENV=1 ENV=ci`:
repository lint, formatting, Pyright and import contracts **passed**; focused
`test_voice_sessions.py`, `test_migrate.py`, `test_settings.py`,
`test_console_contract_snapshot.py` and `test_health.py` finished **34 passed,
11 warnings** in 135.68 seconds on a disposable database. The full-suite result is
recorded in `PROGRESS.md`. No media worker, browser LiveKit connection, STT/TTS
wiring or interruption exists yet; the feature is disabled by default and the
internal media route must not be forwarded by the public proxy.

The branch is based on `97c9172` and main has since advanced 38 commits through
PR #27 (staging deployment work). Only `docs/PROGRESS.md` overlaps and main added
no migrations. Staging currently runs image revision `7cdc156` with schema `0410`;
deploying this branch requires migrations `0420`, `0430` and `0440` first or readiness fails.

## Start here

The lost session's implementation is saved in commit `72f0e30`
(`feat(console): recorded voice transcription (WIP)`) on
`feat/console-recorded-voice`. At the start of this audit, the only worktree
changes were the existing deletions of two `docs/reviews/2026-10-17-*` documents.
Those deletions were preserved. No voice source changes were pending.

Read this record, [the voice contract](../CONSOLE_VOICE.md),
[the workspace voice roadmap](2026-10-21-workspace-agents-roadmap.md#voice-ingress-plus-conversational-rendering-not-a-privileged-agent),
and [LG15](../langgraph-rebuild/LG15_console_turnover.md) before continuing.
The earlier voice entries in `PROGRESS.md` and `CONSOLE_VOICE.md` predate the final
saved implementation: their statements that hands-free browser voice and Telegram
transcription are absent are stale.

## Saved implementation

- Authenticated recorded-audio upload, private transcription jobs, same-key
  replay/conflict handling, provider failure states, expiry and audit are implemented
  in `csa/console/voice.py`, `csa/speech.py`, migration `0420`, and console voice SQL.
  The recorded-message control puts the transcript in the text box for review.
- `VoiceConversation.tsx` implements an opt-in hands-free browser loop: speech
  boundary recording, transcription, an ordinary sandbox text turn, polling for
  replies, local speech playback, then another recording. It supports English and
  Malay playback when the matching local browser voice is installed. Recording
  ends before thinking/playback. It retains the request key during uncertain sends.
- `csa/channels/voice.py` and `IngressWorker._transcribe_voice` implement Telegram
  voice-note download/transcription after durable ingress. Redacted transcripts or
  transcription failure are cached in the ingress row for worker replay. Existing
  coordinator routing and attachment review policy remain in force. This is not
  Telegram speech synthesis or an interactive Telegram call.
- `scripts/dev_voice_runtime.py` validates a fixed isolated dev target before
  starting a selected service. Its existence does not establish which services
  currently run or which persistent migrations have been applied.
- This is a sandbox browser loop, not the roadmap's bound LiveKit media session,
  barge-in, or authorized HR/action voice workflow. Legacy LiveKit containers are
  separate and were not inspected internally or modified by this audit.

## Findings needing work

### 1. Uncertain hands-free sends can also be sent as a new text request

Reproduced in a synthetic component event/state harness; an integrated browser
regression is still missing:
`VoiceConversation.complete` calls `onDraft(recording.body)` before posting the
turn. On a lost acknowledgement, Stop, or polling error, its `finally` releases
`onBusy(false)` while keeping its private `pending` request. The parent sandbox
does not receive that pending key. Its ordinary Send button becomes available for
the transcript, and `Sandbox.send()` creates a new UUID. If the voice turn was
already accepted, clicking ordinary Send can enqueue the same question again.

Relevant source: `VoiceConversation.tsx` lines 59–63 and 94–96;
`SandboxWorkspace.tsx` lines 170–172 and 263–264. Reconcile voice recovery with the
parent's send state so all retries preserve the accepted request identity. Test
the actual integrated sandbox, including loss after server acceptance, Stop, and
the ordinary Send control. The existing browser harness mounts only the voice
component, so it cannot catch this parent/child interaction.

### 2. Hands-free network waits can outlast their stated polling deadlines

Source-review finding: voice status GETs, turn POST, and turn polling GETs receive
only the session signal, whose timeout is 15 minutes. The 95/100-second deadlines
are checked between requests. A hung request can therefore leave the UI waiting
until the overall session timeout or explicit Stop. Add individual request bounds
while preserving the same recovery key.

### 3. Browser and full-suite acceptance were unfinished

Recovered logs show the first browser attempt failed on the staged login-page
path. The saved test now locates the nested login path. The later attempt failed
with `Page.wait_for_function: Target crashed`; no passing final browser result was
recovered. This establishes a failed acceptance check, not its root cause.

The full suite also has no recovered final pass. Do not treat successful focused
tests or builds as proof that the full branch is green.

## Recovered validation evidence

These are historical local logs from the lost session, not fresh results:

| Log under `/tmp/` | Recorded outcome |
| --- | --- |
| `csav2-telegram-voice-tests.log` | 23 passed, 3 warnings |
| `csav2-voice-worker-regression.log` | 44 passed, 34 warnings |
| `csav2-voice-final-config-tests.log` | 29 passed, 9 warnings |
| `csav2-voice-final-all-lint.log` | Ruff, formatting, Pyright and import contracts passed |
| `csav2-voice-final-handsfree-build.log` | Isolated frontend check reached a successful build |
| `csav2-voice-browser-acceptance.log` | Failed: staged login-page path missing |
| `csav2-voice-browser-final.log` | Failed: browser target crashed |
| `csav2-voice-complete-final.log` | Full test run terminated: make exit 143 |
| `csav2-voice-all-channels-full.log` | Collected 2934 tests; incomplete output, no summary |

Earlier lint/type errors are retained in other temporary logs; later checks above
passed. An older full-suite pytest process was still running when inspected and
was left untouched. Temporary logs may be removed by host cleanup; this record
preserves their outcomes.

## Fresh audit validation

All checks below used the unchanged saved application source. This audit changes
only this recovery record and the current progress pointer.

Commands were run from the repository root with
`PATH=/tmp/fourstacks-toolbin:$PATH` so the already installed Node runtime is
available:

```sh
NO_LOCAL_ENV=1 ENV=ci make lint
NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_console_voice.py tests/test_telegram_voice.py tests/test_voice_browser.py tests/test_settings.py tests/test_health.py tests/test_console_contract_snapshot.py tests/test_migrate.py
npm --prefix CSA_WebConsole/csa-console/web run check:isolated
```

- Repository lint, formatting, Pyright and import contracts: **passed**.
- Focused pytest selection: **37 passed, 1 failed, 12 warnings**, 821.40 seconds;
  no skips or deselections. The failure was
  `test_handsfree_two_turns_recovery_and_stop`, with
  `Locator.select_option: Target crashed` while selecting Malay before starting
  voice. This reproduces the browser-crash class of failure; its root cause is
  unproven. The passing selection includes console voice, Telegram transcription
  and worker replay, settings, readiness, contract snapshots and migration tests,
  including `test_make_reset_only_disposable_container`. No persistent reset ran.
- Isolated frontend transport/state tests, ESLint, route types, TypeScript,
  production build and trace checks: **passed**. This does not replace browser
  acceptance.
- A temporary Node harness at `/tmp/csa-voice-recovery-audit/reproduce.cjs`
  transpiled the actual saved `VoiceConversation` and `Sandbox` source, supplied
  synthetic transport/browser primitives and hook state, and executed their event
  handlers. It confirmed an enabled ordinary Send after a lost voice
  acknowledgement, followed by identical text with a different request UUID.
  Run with `node /tmp/csa-voice-recovery-audit/reproduce.cjs` from the repository
  root while that temporary artifact exists. This is component logic evidence;
  it does not exercise React rendering, effects, real media or authenticated HTTP.
- Full suite: **not launched again** during this audit. The older full run was
  still active when inspected, without a final summary. No full-suite pass is
  claimed.
- Documentation local file links and `git diff --check`: **passed**.

## Environment and rollout limits

This audit did not read `.env.local` contents, restart existing services, connect
to a persistent database, or call a live Telegram/STT/model provider. `.env.local`
is ignored by Git. Fresh backend checks use `NO_LOCAL_ENV=1 ENV=ci`, synthetic
provider HTTP and disposable Testcontainers databases. The reset check must use
`test_make_reset_only_disposable_container`; do not run a bare persistent reset.

Recovered runtime log filenames exist for worker, delivery and poller. Their
existence and sanitized status counts do not prove a complete voice conversation,
current service health, or a safe persistent database target. Earlier prose saying
no service startup ever occurred should not be used as a current environment
inventory. Persistent migration/readiness, actual microphone behavior, installed
playback voices, multilingual STT quality, provider retention and live connectivity
still need separate evidence. Expiry cleanup is access-triggered; scheduled erasure
and recovery after page reload are absent.

## Bounded next task

For the requested expansion to interactive conversation, follow the proposed
[interactive voice implementation plan](2026-10-24-interactive-voice-plan.md).

Phases 1 and 2 of that plan are implemented in the working tree (see the two
sections above). Commit them, rebase onto main, then proceed to Phase 3: an isolated
media worker plus one end-to-end sandbox conversation. Keep live rollout separate
until the isolated target, migration/readiness and provider/consent conditions are
verified.

Copy-ready continuation prompt:

> Continue the voice work on `feat/console-recorded-voice`. Read `AGENTS.md`,
> `docs/handoffs/2026-10-24-voice-recovery-audit.md`, `docs/CONSOLE_VOICE.md` and
> `docs/handoffs/2026-10-24-interactive-voice-plan.md`. Phases 1 (stabilization) and
> 2 (trusted sessions, migration 0440, disabled by default) are committed; the branch
> must be rebased onto main before a PR. Next is Phase 3: an isolated media worker and
> one end-to-end sandbox conversation. Use only synthetic providers and disposable
> PostgreSQL, with `NO_LOCAL_ENV=1 ENV=ci`.
> Do not read/print environment secrets, use production, modify legacy LiveKit,
> restart persistent services, or perform live provider calls as part of this slice.
> Record precise focused, browser, lint, full-suite and disposable-reset results.
