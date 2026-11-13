# Training library

A company keeps its own training PDFs in the console, so a new colleague opens one page and reads
them instead of hunting through chat and drives. This is the backend, version 1: PDFs only. The HTTP
contract (routes, codes, headers) is in [CONSOLE_API.md](./CONSOLE_API.md#training-library); this
file is what it does, how to run it and what it leaves out. The design is in
`docs/superpowers/specs/2026-11-06-training-library-design.md`.

It is not HR's Training tab. That tab lets staff request a course and a manager decide. The library
only stores and shows documents, and shares nothing with it except the HR-owner check below.

## What it does

- Staff of the company list the items and open a PDF (the web client draws it in the browser).
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
| Videos per company | 50, 5 GB (counting uploads in progress), 2 uploads open at once; PDFs and videos are counted apart |
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
| `TRAINING_UPLOAD_ROOT` | Local backend only: the folder for training PDFs, stored as `<root>/<company id>/<uuid>.pdf`. Unset means the store is not configured and every training route answers 503 `training_storage_unavailable`. It may not be, contain or sit inside `KB_UPLOAD_ROOT` or `HR_UPLOAD_ROOT`. |
| S3 `training/` prefix | With `STORAGE_BACKEND=s3` the files go under `training/<company id>/<name>` in the same bucket, with the same SSE-S3 settings. `TRAINING_UPLOAD_ROOT` must then be unset (anything else stops the process at startup). The application key needs list, read, write and delete on `training/*`: see the IAM statement and the lifecycle rules in [OBJECT_STORAGE.md](./OBJECT_STORAGE.md). A deleted object stays recoverable there for 7 days as a non-current version. |
| `TRAINING_VIDEO_ENABLED` | `false` by default. While false, starting a video upload answers 503 `training_video_unavailable`; existing videos are still listed, edited and removed, and removal aborts an unfinished upload. |
| `GROQ_API_KEY`, `GROQ_MODEL` | The language-model provider's key and model name, used by summaries (the same settings the assistants use). `GROQ_API_KEY` is a required setting, so the model client is always built at startup when the console's identity block runs; a first summary answers 503 `training_assistant_unavailable` for want of a client only when that block did not run. |

Staging mounts a new volume for the local root on the gateway and the training worker
(`staging-training-uploads`, in `deploy/compose.staging-pipeline.yml`, at
`/var/lib/csa/training-uploads`); the worker and delivery services never mount it. `scripts/staging_reset.py` lists that volume, so a reset empties it with
the database. Migrations `0540` and `0541` stop all four services when applied, as the staging tool
already says.

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

## Out of scope for version 1

- Video, audio, PPTX or any file that is not a PDF. The `kind` column leaves room for video, which
  waits for the move to S3 (browser-direct multipart upload and a presigned or proxied range read).
- Courses, ordered lessons, required items, due dates, reminders, quizzes, certificates, comments.
- Per-person progress or view tracking. Views and downloads are not logged.
- Per-item or per-role visibility: everyone in the company sees every item.
- A customer-facing library; summaries of video; training actions in the workspace assistant (it only
  knows the `training_library` tab key).
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
- **Rolling back migration `0541`** drops the table and leaves the files in the store with no row.
  They are unreachable and no sweep runs without the table, so remove them by hand if they matter.
  The rollback steps are in the migration's header.
- **Memory:** a PDF is read whole into memory for the checks and for a summary (up to 25 MB each),
  never in bulk. Summaries of several distinct items at once multiply that; the child slots (2) and
  model slots (4) bound CPU and provider calls, not this read. A queued upload or summary holds its
  file, up to 25 MB, in the gateway while it waits for one of the 2 parser slots, and the staging
  compose (`deploy/compose.staging-pipeline.yml`) sets no container memory limit.
- **Retesting on staging** after a rollout: [TRAINING_RETEST.md](./TRAINING_RETEST.md).
