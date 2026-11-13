# Training library

A company keeps its own training PDFs and videos in the console, so a new colleague opens one page
and reads or watches them instead of hunting through chat and drives. PDFs came first (version 1);
videos came with migrations `0600` and `0601` and are described in [Video](#video) below. The HTTP
contract (routes, codes, headers) is in [CONSOLE_API.md](./CONSOLE_API.md#training-library); this
file is what it does, how to run it and what it leaves out. The designs are in
`docs/superpowers/specs/2026-11-06-training-library-design.md` (PDFs) and
`docs/superpowers/specs/2026-11-13-training-library-video-design.md` (video).

It is not HR's Training tab. That tab lets staff request a course and a manager decide. The library
only stores and shows documents, and shares nothing with it except the HR-owner check below.

## What it does

- Staff of the company list the items and open a PDF (the web client draws it in the browser) or
  watch a video with its transcript beside it (see [Video](#video)).
- Tenant admins and the company's HR owner add, edit and remove items.
- Any member, including a `read_only` one, can ask for a short summary of a PDF's text. It is made
  once, stored, and shown to everyone in the company.
- Every row and every file belongs to one company. Rows are under row security on `app.client_id`
  (`training_items`, `ENABLE` only, never `FORCE`, grants to `csa_app` only) and every object key
  starts with the company id. Another company's item id is a 404.

## Who can do what

| | Read (list, open, ask for a summary) | Manage (add, edit, remove, regenerate a summary) |
|---|---|---|
| Platform admin, acting on a company | yes | yes |
| Tenant admin | yes | yes |
| HR owner (a `tenant_staff` member) | yes | yes |
| Other `tenant_staff` | yes | no |
| `read_only` | yes | no |
| Machine token | no | no |

The package must be active for the company, for every role. Reading is open to every role by design:
the library is a shelf, with no per-item or per-role visibility. A `read_only` member can make
exactly one write, asking for a summary (`POST /training/items/{id}/summary`); the exemption in
`csa/console/access.py` (`reader_may_post`) matches that one route and nothing else.

## Limits

| What | Limit |
|---|---|
| One file | 25 MB |
| Pages in one PDF | 300 |
| PDFs per company | 300 |
| Total size of PDFs per company | 500 MB |
| Videos | counted apart from PDFs; see [Limits for video](#limits-for-video) |
| Title / description / category | 120 / 1000 / 60 characters |
| Text sent for one summary | first 24,000 characters |

They are constants in `csa/training/limits.py`; the item and size limits are checked under a
per-company lock, so a race for the last slot has one winner. The 25 MB file limit sits under the two
other caps on the path: the console's Next rewrite (31 MB) and Cloudflare (100 MB). The shared upload
parser's own default (10 MB, used by the KB and HR uploads) is unchanged; the training route raises
it for itself with `max_file`.

### Known limits

- **Some images may not draw in the viewer.** pdf.js decodes JPEG 2000 images and ICC colour
  profiles with WebAssembly files that the console does not serve (no `wasmUrl`), so a page with
  such an image can show blank where the image is. The rest of the page draws as usual.

## Settings

| Setting | Meaning |
|---|---|
| `STORAGE_BACKEND` | `local` (default) or `s3`; see [OBJECT_STORAGE.md](./OBJECT_STORAGE.md). |
| `TRAINING_UPLOAD_ROOT` | Local backend only: the folder for training files (PDFs and videos), stored as `<root>/<company id>/<uuid>.<ext>`. Unset means the store is not configured and every training route answers 503 `training_storage_unavailable`. It may not be, contain or sit inside `KB_UPLOAD_ROOT` or `HR_UPLOAD_ROOT`. |
| S3 `training/` prefix | With `STORAGE_BACKEND=s3` the files go under `training/<company id>/<name>` in the same bucket, with the same SSE-S3 settings. `TRAINING_UPLOAD_ROOT` must then be unset (anything else stops the process at startup). The application key needs list, read, write and delete on `training/*`, and for video also abort and list parts of a multipart upload there: see the IAM statements and the lifecycle rules in [OBJECT_STORAGE.md](./OBJECT_STORAGE.md). A deleted object stays recoverable there for 7 days as a non-current version, a video included. |
| `TRAINING_VIDEO_ENABLED` | `false` by default; read by the gateway and the training worker. See [Settings for video](#settings-for-video). |
| `VOICE_STT_ENDPOINT` | The speech-to-text endpoint the training worker sends video audio to. See [Settings for video](#settings-for-video). |
| `GROQ_API_KEY`, `GROQ_MODEL` | The language-model provider's key and model name, used by summaries (the same settings the assistants use). `GROQ_API_KEY` is a required setting, so the model client is always built at startup when the console's identity block runs; a first summary answers 503 `training_assistant_unavailable` for want of a client only when that block did not run. |

Staging mounts a volume for the local root on the gateway and the training worker
(`staging-training-uploads`, in `deploy/compose.staging-pipeline.yml`, at
`/var/lib/csa/training-uploads`); the customer-facing worker and delivery never mount it.
`scripts/staging_reset.py` lists that volume, so a reset empties it with the database. A migration
stops every app service when applied, the training worker included, as the rollout tools say;
`0600` and `0601` are the latest.

## Turning the package on for a company

The package is `training_library` (title "Training library", standalone, no requirements, no
capabilities, tab `training_library`, sellable). Nothing is backfilled: an existing company does not
have it until it is activated for them, and until then its tab and every route answer 403
`package_inactive`.

1. A request is optional. A tenant admin or a platform admin may request it:
   `POST /console/v1/services/training_library/request`.
2. A platform admin activates it for the company, with or without a request first:
   `POST /console/v1/services/training_library/activate` and the body `{"reference": "...",
   "note": "..."}` (`note` may be left out; the Services tab does the same). That writes the
   `service.activated` audit row.

   A platform admin adds `?client_id=<company>` to both calls (400 without it). Both answer 409 when
   the package is already active.
3. The gate accepts `active` only. If the package is suspended the routes answer `package_inactive`;
   the stored items and files stay and come back when it is activated again.

## The HR-owner dependency

The company's HR owner can manage the library even when they are plain staff. Who the HR owner is
lives in `hr_client_owners`, which row security hides from the app role except the caller's own row,
so the app cannot simply query it. Migration `0541` adds `public.training_is_hr_owner()`:

- It takes no arguments. It reads `app.client_id` and `app.hr_user_id` from the transaction, the way
  `hr_owner_read` (0522) does, and answers true only when that pair is the company's row in
  `hr_client_owners` and the user and their membership of that company are enabled and the role is
  not `read_only`.
- It cannot be pointed at another user or company: the Python side sets `app.hr_user_id` from the
  authenticated session's user with the helper `hr_owner.py` uses, never from anything the request
  carries. A header, query parameter or body field naming a user changes nothing (tests cover each).
- It is `SECURITY DEFINER`, pinned to its `search_path`, with `EXECUTE` for `csa_app` only. It is the
  only place the library touches an HR table.
- A platform admin never goes through it: that role is allowed by the role check.
- Nothing is cached. Every manage request asks again, and an upload asks twice (before the body is
  read, and again under the lock), so a person who stops being the HR owner while a file is being
  checked is refused. The HR owner is moved from the Team page (`/team/hr-owner`); the next request
  of the old owner is a 403 `training_forbidden` and the new owner's is allowed.

The HR package does not have to be active for the library to ask this; if the company has no HR
owner row the answer is simply false, and only tenant admins and platform admins manage.

## How PDFs are parsed

A PDF from outside is untrusted input, and pypdf runs in Python on the gateway, which every tenant
shares. Every parse, for an upload check and for summary text, therefore runs in a short-lived child
process (`csa/training/pdf_guard.py`, with `pdf_worker.py` as its entry point):

- **CPU:** 10 seconds (`RLIMIT_CPU`; the kernel kills the child past it).
- **Memory:** 1 GiB of address space (`RLIMIT_AS`). The worker itself needs about 64 MiB, so a
  limit set far too low shows up as an infrastructure error rather than as a bad file.
- **Wall clock:** 20 seconds. Past it the gateway kills the child's whole process group.
- **Output:** at most 1 MiB, which must be exactly one JSON result in the worker's shape. Anything
  else is treated as unreadable.
- **Empty environment:** the child is started with no environment variables at all, because the
  gateway's holds database and API keys. It runs `python -I`, in `/`, with stderr discarded, no core
  dump and at most 32 open files; the worker sets its limits before it imports pypdf or reads a byte.
  The bytes of the file go in on stdin; the uploaded file name never reaches the child's command line.
- **Concurrency:** at most 2 children at once per gateway process; further callers wait for a slot.
  Each parse costs roughly a quarter of a second of start-up.
- Beyond the child, pypdf is also bounded in-process: decoded stream sizes, object-stream and xref
  budgets, page-tree size and depth, and the content bytes read for text (all in
  `csa/training/limits.py`).

What happens on a timeout or a kill: a child that is killed, runs out of time or memory, crashes or
answers anything else means the file could not be checked in the allowed time, so the file is refused
as 422 `training_pdf_unreadable` and nothing is stored. A child that cannot be started, or a worker
that cannot get ready, and the 30 second outer wait for a slot plus the parse, are infrastructure
failures: 503 `training_parser_unavailable`, retry. Neither connections nor locks are held while the
child runs. Logs carry the action, the outcome, the refusal code and the elapsed time, never the name,
bytes or text.

The upload checks, in order, are in [CONSOLE_API.md](./CONSOLE_API.md#the-checks-on-an-upload): size
and multipart bounds, a safe file name, `.pdf` and `%PDF-`, then (in the child) not encrypted,
readable, 1 to 300 pages and no document-level JavaScript or launch action, then the limits and the
idempotency key under the company lock. Annotation-level actions, form scripts and embedded files are
not inspected; the viewer is meant to run with scripting off.

## The summary

- **What is sent, and when.** Only when someone asks for a summary of an item that has none (or a
  manager regenerates one). The server extracts the PDF's text, at most 24,000 characters, and sends
  it to the language-model provider (the Groq client with `GROQ_MODEL`) as one JSON completion. Nothing
  else about the item goes: not the title, the file name or the company. The first-time notice next to
  the button is the web client's job.
- **Prompt.** The text is placed inside one `<pdf_text>` fence that the text cannot close (any
  `pdf_text` in it is neutralised). The system prompt says the text is data and never instructions,
  the model has no tools, and the summary is in the PDF's own language. The reply is validated
  (summary up to 1500 characters, up to 6 key points of up to 200) before it is stored; a reply that
  does not fit is a provider failure and stores nothing. One call has a 20 second timeout and a 900
  token cap, and at most 4 calls run at once per process.
- **Stored once.** The text is saved as JSON in `training_items.summary`, with `summary_truncated`,
  `summary_model` and `summarised_at`. Every later request, by anyone in the company, reads it with no
  model call, and a stored summary is served even if the provider is switched off. Ten simultaneous
  first clicks in one gateway process make one call; with several gateway processes a race can cost
  one extra call, but one summary is stored (first writer wins).
- **The request** is `POST /training/items/{id}/summary` with a JSON body, which is required (an empty
  POST is 422): send `{}` for a plain read-or-make, or `{"regenerate": true}` to regenerate.
- **Regenerate** is manage-only, always calls the model and replaces the stored text.
- **No readable text** (a scan) is 422 `training_no_text`; a provider failure is 503
  `training_assistant_unavailable`. Neither stores anything.
- **The output is shown to every member**, and it comes from a model reading a file a member
  uploaded. The frontend must escape it and never render it as HTML or Markdown.
- Audit: `training.summary_generated`, `training.summary_regenerated`. Logs hold the item id, the
  company id, the model name and byte counts, never text.

## Video

A manager adds a video of up to 500 MiB (MP4, M4V, MOV or WebM). The console sends it in 8 MiB pieces
through the gateway, so a dropped connection costs one piece and the upload resumes. The training
worker then transcribes the audio with timestamps, writes one summary of the whole transcript and
indexes the transcript so the staff assistant can answer questions about it with the time to watch.
Everyone in the company can watch the video, read the transcript beside the player (a click on a time
seeks), read the summary, and open the video at a time with `?t=<seconds>` on its page address. The
file plays as uploaded: nothing is re-encoded, so a browser that cannot play its picture codec cannot
play it (HEVC is accepted, with a note to the manager). The routes, codes and fields are in
[CONSOLE_API.md](./CONSOLE_API.md#video-uploads) and
[Video processing](./CONSOLE_API.md#video-processing).

Readers see only `ready` videos. Managers see every state, with a plain sentence for where a video is
or why it failed, and "Try again" for a failure worth another go.

### Who can do what with video

| | Watch, read the transcript and the summary | Ask the assistant about a video | Add, edit, remove, try again |
|---|---|---|---|
| Platform admin, acting on a company | yes | yes | yes |
| Tenant admin | yes | yes | yes |
| HR owner (a `tenant_staff` member) | yes | yes | yes |
| Other `tenant_staff` | yes | yes | no |
| `read_only` | yes | no | no |
| Machine token | no | no | no |

The manage rule is the PDF rule (`can_manage`, with the HR-owner check below). Asking is the staff
assistant's own rule: it admits platform admins, tenant admins and `tenant_staff`, never `read_only`,
and it needs the assistant switched on for the company (`WORKSPACE_ASSISTANT_ENABLED`) and the
Training library package (without the package the assistant answers as it does for a locked tab).
Every video write is a manage route and the summary is made by the worker, so `reader_may_post` is
unchanged: a `read_only` member still has the one PDF summary exemption and nothing else.

### Limits for video

Constants in `csa/training/limits.py`.

| What | Limit |
|---|---|
| One video | 500 MiB |
| Length | 60 minutes (the worker measures it; the console also refuses an over-long file before sending when the browser can read its length) |
| Videos per company | 50; a video refused for a reason in its file keeps its row (so the manager sees why) and counts toward the 50 until it is removed, although its bytes no longer count |
| Total size of videos per company | 5 GiB, counting an upload in progress at its declared size; a video refused for a reason in its file stops counting when its file is deleted |
| Uploads open at once, per company | 2 |
| Piece | 8 MiB, the last piece the remainder; 4 pieces at once per gateway process (the console sends up to 3 at once) |
| An upload with no new piece | removed after 24 hours |
| Processing per company | 5 hours of video in any rolling 24 hours |
| Audio chunk sent to the speech endpoint | 5 minutes |
| A transcript sent whole to answer a question | up to 40,000 characters; longer: the 8 nearest 60-second windows |
| A transcript summarised in one call | up to 24,000 characters; longer: parts of up to 20,000 characters, then one call that combines them |
| A playback link, and the worker's read link | 15 minutes |
| A job's heartbeat counts as stale after | 10 minutes (600 s) |
| Attempts per stage, and the wait between them | 3, and 60 s times the attempt number |
| Usage rows kept | 2 days |
| One ffmpeg or ffprobe child | CPU 309 s, address space 448 MiB, wall clock 300 s |
| Accepted picture codecs | H.264, VP8, VP9, AV1, HEVC |
| Accepted sound codecs | AAC, MP3, Opus, Vorbis (a file with no sound is accepted and has no transcript) |
| Counted as silent | a first audio stream whose loudest point is at most -50 dBFS |

Videos and PDFs are counted apart: a full video quota still accepts a PDF and the reverse.

### Settings for video

| Setting | Meaning |
|---|---|
| `TRAINING_VIDEO_ENABLED` | `false` by default, read by the gateway and the training worker. Staging sets it from `CSA_STAGING_TRAINING_VIDEO`, production from the same name in `deploy/production-rollout.env` (a reviewed change, never a host edit). While false: the four upload routes answer 503 `training_video_unavailable`; existing videos are still listed, edited, played and removed, and removal aborts an unfinished upload; "Try again" still queues a job; the training worker only touches its liveness file. While true: the worker refuses to start without `VOICE_STT_ENDPOINT`, without the training store, and (on S3) with the disabled placeholder key. |
| `VOICE_STT_ENDPOINT` | The speech-to-text endpoint, unset by default (opt-in) and required by the training worker when video is on. Compose sets it for the training worker only, to Groq's transcription URL, so the key is the model key (`GROQ_API_KEY`, through `voice_transcription_key()`) unless `VOICE_STT_API_KEY` is set. `VOICE_STT_MODEL` defaults to `whisper-large-v3-turbo`. In production the endpoint must be https. The sound of every video goes to this provider in 5-minute chunks: review its retention terms first, as [CONSOLE_VOICE.md](./CONSOLE_VOICE.md) asks for voice notes. |
| `GROQ_API_KEY`, `GROQ_MODEL` | The summary and the assistant's answers about a video, as for PDF summaries. |
| `TEI_ENDPOINT` | The embedding service that indexes transcript windows (the knowledge base's embedder). |

### The training worker

`python -m apps.training_worker.main`, the compose service `training-worker` on staging and production,
in the same image as the others. That image now carries ffmpeg and ffprobe (a static 7.1.1 build,
GPL v3 or later, pinned by digest): 523 MB became 901 MB on disk.

- **One process, always.** It handles one job at a time, and the claim rule "no other job of this
  company is processing" relies on a single claimer. Never give it replicas.
- **Idle while video is off.** It opens nothing and touches `/tmp/training-worker.alive` (the
  compose healthcheck reads it), so the service can ship before the switch. With video on it opens a
  small owner pool (to list active companies) and a small app pool (everything else, inside each
  company's transaction).
- **Where it reads the files.** On staging the `staging-training-uploads` volume (local backend, no S3
  secret). On production its own S3 key (`csa-prod-training`): list, read, delete and abort under
  `training/`, and no write (policy in
  [OBJECT_STORAGE.md](./OBJECT_STORAGE.md#iam-policy-for-the-training-worker-key)). Until the owner
  makes that key it mounts the disabled placeholder.
- **Memory.** The production default limit is 256m (`CSA_PROD_MEM_TRAINING_WORKER`, `oom_score_adj`
  600, so the OOM killer takes it before a customer-turn worker). If the OOM killer takes the ffmpeg
  child, the attempt fails as worth trying again and is retried after its wait, the file kept; if it
  takes the Python process, the job is requeued once its heartbeat is 10 minutes old. Either costs
  one of the stage's 3 attempts. [TRAINING_VIDEO_CAPACITY.md](./TRAINING_VIDEO_CAPACITY.md) recommended 160 MB, but
  that was measured with the Python imports only, without the pools, the S3 client and the chunk
  upload buffer, so the shipped default is higher. Whether the 2 GB production host can take the
  extra container is not established: see the capacity document and `deploy/PRODUCTION.md`, "Training
  video".
- **ffmpeg runs hardened,** like the PDF parser: a child with an empty environment, no stdin, CPU,
  address-space and wall-clock limits (above), its stderr discarded and its output bounded; the input
  format is forced from the stored container, so no playlist or `concat` file the video names is ever
  followed; only the first audio stream is read and the picture is never decoded.

### How a video is processed

The states staff see: `uploading`, `uploaded`, `transcribing`, `summarising`, `ready`, or `failed`.

1. **Upload.** Starting reserves the declared size and a slot under the company lock and shows the
   item as `uploading`. Completing checks every piece against its hash, publishes the file, makes the
   item `uploaded` and queues one job at the stage `transcribing`.
2. **Claim.** Each poll (every 5 seconds when idle), for every active company, in that company's
   transaction: jobs whose heartbeat is older than 600 s are requeued (or failed once their three
   attempts are used), then the oldest due job is claimed, and only while none of the company's
   jobs is processing. A claim adds one attempt.
3. **Transcribing.** Probe the duration and codecs (refusals below). No sound stream: `no_audio`,
   `ready`. A silent track: `no_speech`, `ready`, no provider call. Then the daily cap is reserved
   (below). Then, for each 5-minute chunk from `chunks_done`: ffmpeg reads that chunk's audio from
   the source (on S3 through a presigned link made for that chunk; locally from the file), the speech
   endpoint transcribes it, and the chunk's lines and `chunks_done` are committed in one transaction.
   A restart therefore repeats at most the chunk in progress. At the end the lines are grouped into
   windows of about 60 seconds, embedded and stored; fewer than 20 characters of kept text (lines the
   provider scored as likely not speech are dropped first) is `no_speech`, otherwise `speech`.
4. **Summarising** (only for `speech`). One call for a transcript up to 24,000 characters; a longer one
   is summarised in parts of up to 20,000 characters, each part saved as it finishes, then combined
   by one more call, so the last part counts as much as the first. The text is validated as a PDF
   summary is and stored in `training_items.summary` (`summary_truncated` false). A restart resumes
   from the saved parts. A silent or soundless video is never summarised.
5. **Ready.** The item becomes `ready` and readers see it.

Every write a stage makes carries the job's stage and attempt, so an attempt that was requeued and
claimed again, or whose item was removed, writes nothing and stops quietly.

**How it fails.**

| Kind | What | What happens |
|---|---|---|
| Worth trying again | The speech service, the model, the embedding service or the store did not answer; the worker or its ffmpeg child was killed (the OOM killer) or stopped beating; ffmpeg failed on the loudness or a chunk after the probe had accepted the file (for example a full `/tmp`); on S3, the probe failed on an attempt before the last; an unexpected error in a stage | The job goes back to `queued` and waits 60 s times the attempt number; at most 3 attempts per stage (the summary stage starts again at 0). After the third the video is `failed` with `training_transcription_unavailable`, `training_summary_unavailable` or `training_storage_unavailable`, and a manager can press "Try again" (`POST /training/items/{id}/retry`): the job is queued again at the stage that failed with its attempts reset, and resumes at `chunks_done` or the saved parts |
| The file itself | `training_video_unreadable` (ffprobe cannot read it, ends on its CPU or file-size limit or crashes; on S3, the store holds the full size and the read still fails on the stage's last attempt), `training_video_too_long` (over 60 minutes), `training_video_codec` (a picture or sound codec outside the lists) | `failed` at once, no retry. The stored file is deleted (if that delete fails the hourly sweep does it), the row stays so the manager sees why, and its bytes stop counting. The manager adds a new file |
| The daily cap | 5 hours of video per company in any rolling 24 hours, booked per video before its first chunk is sent | The job waits (`processing.waiting`, the list says "Waiting to start") with its attempt given back, and runs when enough booked time is more than 24 hours old. Deleting a video does not give its seconds back |

### The hourly sweep

The training worker sweeps every active company when it starts and then every hour
(`training_sweep` in its log, with the counts):

- An upload with no new piece for 24 hours (or none since it started) is removed with its item
  (audit `training.upload_abandoned`, no actor) and its store upload is aborted.
- Stored files that no row keeps are removed, and so is the file of a refused video whose delete
  failed.
- On S3, multipart uploads whose name has no `uploading` row are aborted (an abort that failed on
  removal, or a start whose answer was lost). The bucket's own lifecycle rule is the backstop.
- Usage rows older than 2 days are deleted.

A failure in one company is logged (`training_sweep_failed`) and the others go on.

### Cost (to be confirmed)

No price is written anywhere in the repository. These figures are from the design and are from
memory: check them against each provider's price page before relying on them.

| Item | Assumption (unverified) | Per unit |
|---|---|---|
| Transcription, Groq `whisper-large-v3-turbo` | about $0.04 per audio hour; a minimum billed length of about 10 s per request | about $0.04 for a 60-minute video, $0.01 for 15 minutes |
| Summary, Groq `openai/gpt-oss-120b` | about $0.15 per million input tokens and $0.60-0.75 per million output; about 4 characters a token | about 17,000 tokens in and 2,000-4,000 out (with reasoning) for 60 minutes: about $0.004-0.006 |
| Embeddings | self-hosted | no provider charge |
| One assistant question | as the summary model | about 10,000 tokens in, 500 out: about $0.002, plus the planner call every turn already makes |
| S3 storage | about $0.025 per GB-month (Singapore region assumed) | about $0.13 a month for a full 5 GiB company |
| S3 data transfer out (playback) | about $0.09-0.12 per GB after the free allowance | a 200 MB video watched by 20 staff is 4 GB: about $0.40-0.50. The largest recurring cost, larger than processing |

Processing a typical 15-minute video costs about one US cent. If playback volume grows, a CDN in
front of the bucket is the later fix.

### Playback, the transcript and the assistant

- **Playing.** On S3 the page gets a presigned link under the company's own prefix, valid 15 minutes,
  and S3 answers the browser's Range requests, so seeking works and the video never passes through
  the gateway. On the local store (staging) the link is the gateway's own `/video` route, which
  answers one byte range per request. If a paused video's link expires, the page asks for a new one
  and goes back to the position. Neither link is logged.
- **The transcript** is plain text, never HTML or Markdown. It is in the company's tables only
  (`training_transcript_segments` and `training_transcript_windows`), never in the knowledge base
  the customer bot reads.
- **Asking the assistant.** The planner's `training_ask` action finds the video in the caller's
  company (by the open page, a picked option or the title words), and answers only from a `ready`
  video with speech: the whole transcript up to 40,000 characters, else the 8 windows nearest the
  question and the stored summary. The server keeps only the times the model cited that fall inside
  what it sent, snaps each to a line start, and the dock shows "Watch from mm:ss" links that open the
  video there. The turn is stored as an ordinary `answer`. Logs hold the item id and sizes, never the
  question, the transcript or the answer.
- **What leaves the company.** The sound goes to the speech-to-text provider; transcript text goes to
  the language-model provider for the summary and for a question. The add dialog says so.

### Storage and retention

- Files are `<uuid>.<ext>` under `training/<company id>/` on S3 (a multipart upload until completed)
  or `<TRAINING_UPLOAD_ROOT>/<company id>/` locally (with `.<name>.part` while arriving).
- **The 7-day S3 version retention covers videos.** A deleted video stays recoverable by an admin as a
  non-current version for 7 days and is billed for that time ([OBJECT_STORAGE.md](./OBJECT_STORAGE.md)).
  The bucket's rule that aborts incomplete multipart uploads must say 2 days, not 1, before video is
  switched on: a video upload stays open while pieces arrive and is abandoned only after 24 hours
  without one.
- Deleting a video removes its file or upload, pieces, job, transcript, windows and summary; only its
  processing-usage row stays. Assistant turns that quoted it are not rewritten.

### Operations notes for video

- **One gateway process.** The limit on pieces in flight (4) and the gate that closes a video to new
  pieces while it completes are per process. The gateway runs one uvicorn process: do not add
  `--workers` or a second gateway without moving them into the database.
- **Completing holds the company lock.** `complete` holds the company lock and one pooled database
  connection while the store checks the pieces (locally it re-hashes the whole file, up to 500 MiB; the
  plan estimated 1 to 3 seconds on staging, not measured separately; on S3 it is the completion
  call). An upload start, a PDF upload or a removal's cleanup in the same company waits behind it.
- **Slow uploaders hold piece slots.** A piece's body has no read timeout, so a client that sends
  slowly holds one of the gateway's 4 piece slots until it finishes or disconnects. Four of them make
  other companies' pieces answer 503 `training_busy` (the console retries by itself).
- **What a manager gets for a video that cannot play.** Playback and the video route answer 404 for a
  manager's video that is still uploading, or that failed for a reason in the file itself (its file is
  deleted); its transcript stays readable. A failed video's summary is 404 for a reader (readers do
  not see it at all) and `{"summary": null}` for a manager.

### Known limits of video

- **ffmpeg does not check the host name of the S3 link (S3 only).** The pinned ffmpeg 7.1.1 verifies
  the certificate chain of the presigned link against the image's CA bundle but not its host name.
  Someone who can intercept the worker's traffic and holds any publicly trusted certificate could
  read that one video's link for its 15 minutes and feed the worker other bytes, which are still
  decoded in the hardened child. The owner accepts this or funds the follow-up (the worker fetches the
  bytes itself and gives ffmpeg a local source). A test turns red when a newer build fixes it.
- **A single gateway process, the company lock during completion and slow-uploader slots**, above.
- **A WebM with no seek index** (browser MediaRecorder output) is read from the start for every chunk:
  on S3 later chunks read more bytes, bounded by the 300 s wall clock of the child.
- **A stopped worker costs an attempt.** A worker that is stopped mid-stage leaves its job
  `processing` until the heartbeat is 10 minutes old; it is then requeued and that costs one of the
  3 attempts.
- **A retry is not counted twice.** A video already booked against the daily cap is not booked again
  by "Try again" while its usage row exists (up to 2 days); after the row is pruned it books again.
- **An audio-only file is refused** with the codec sentence, which does not say why.
- **Real-model and real-bucket behaviour was not exercised in the build:** see
  [TRAINING_RETEST.md](./TRAINING_RETEST.md).

## Out of scope

- PPTX, audio-only files or any file that is not a PDF or a video (MP4, M4V, MOV, WebM).
- For video: transcoding or adaptive streaming, captions inside the player and transcript editing,
  making a new summary of a video on request, uploads straight from the browser to S3, and asking the
  assistant about a PDF (it answers about videos only).
- Courses, ordered lessons, required items, due dates, reminders, quizzes, certificates, comments.
- Per-person progress or view tracking. Views and downloads are not logged.
- Per-item or per-role visibility: everyone in the company sees every item.
- A customer-facing library, and the customer bot reading transcripts (it never does). The workspace
  assistant can open the tab and answer from a video's transcript; it cannot add, edit or remove an
  item.
- Any change to HR training requests.

## Operations

- **Removing an item** deletes the row and writes `training.item_removed`, then sweeps the company's
  files. For a video, in any state, the row's pieces, job, transcript and search windows go with it;
  when an upload was open the store's multipart upload is aborted before the sweep (a failed abort is
  logged as `training_abort_failed` and the hourly sweep of open uploads settles it); the video's
  processing-usage row stays. A download already open still finishes. If the sweep fails the row is already gone, the
  failure is logged (`training_cleanup_failed`), and the next sweep removes the file.
- **The reconcile sweep** (`cleanup` in `csa/training/store_sweep.py`, also importable from
  `csa/training/service.py`) lists the company's stored names and deletes those that have no row,
  under the same per-company lock as an upload. A row keeps its `storage_name`, and a video still
  `uploading` also keeps `.<storage_name>.part`, so a sweep after a failed PDF never removes a video
  that is being uploaded. It runs after
  every failed upload that got as far as writing and after every removal. A failed upload therefore
  leaves no orphan and no row; a lost commit acknowledgement is settled against the committed rows
  rather than by guessing.
- **A stray file** (an orphan from a crash between a write and the sweep) is deleted by the next
  failed upload or removal in that company. The sweep only ever touches names of the form
  `<uuid>.pdf`, `<uuid>.mp4`, `.m4v`, `.mov` or `.webm` (and the local store's `.<name>.part`
  temporary files). Any other name in the
  company's folder or prefix is left alone, so remove it by hand from the volume or the `training/`
  prefix. Never delete a `<uuid>.pdf` that has a row: the item would answer 503
  `training_storage_unavailable` on open (the log line `training_file_missing` carries the item id).
- **Checking the store on S3:** `python -m scripts.storage_smoke` covers the `training` prefix; see
  [OBJECT_STORAGE.md](./OBJECT_STORAGE.md#how-to-verify).
- **Rolling back migrations `0600` and `0601`** (video) is in [RUNBOOK.md](./RUNBOOK.md), with the
  training worker's operations ([Training video](./RUNBOOK.md#training-video-the-training-worker)).
- **Rolling back migration `0541`** drops the table and leaves the files in the store with no row.
  They are unreachable and no sweep runs without the table, so remove them by hand if they matter.
  The rollback steps are in the migration's header.
- **Memory:** a video is never held whole: a piece is 8 MiB, 4 pieces at most are in flight per
  gateway process (32 MiB), and the worker holds one 5-minute chunk. A PDF is read whole into memory
  for the checks and for a summary (up to 25 MB each), never in bulk. Summaries of several distinct items at once multiply that; the child slots (2) and
  model slots (4) bound CPU and provider calls, not this read. A queued upload or summary holds its
  file, up to 25 MB, in the gateway while it waits for one of the 2 parser slots, and the staging
  compose (`deploy/compose.staging-pipeline.yml`) sets no container memory limit.
- **Retesting on staging** after a rollout: [TRAINING_RETEST.md](./TRAINING_RETEST.md).
