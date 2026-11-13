# Training library retest checklist

Run this on staging after the rollout that brings the Training library (migrations `0540` and
`0541`, the `staging-training-uploads` volume and `TRAINING_UPLOAD_ROOT` on the gateway). Use a
staging test company and synthetic PDFs only: nothing confidential, because the summary step sends
the PDF's text to the language-model provider. How the library works is in
[TRAINING_LIBRARY.md](./TRAINING_LIBRARY.md). Video has its own rows, from step 11 in the
[Video checklist](#video-checklist) below, and its own rollout (migrations `0600` and `0601`).

You need four accounts in the test company: a tenant admin, a `tenant_staff` member who is the
company's HR owner, a plain `tenant_staff` member, and a `read_only` member. You also need two PDFs
with real text: a slide deck exported in landscape (PowerPoint or Google Slides) and a portrait
document.

Make the three bad files on your own machine, from the repo checkout:

```bash
.venv/bin/python -c "from pypdf import PdfWriter; w = PdfWriter(); [w.add_blank_page(842, 595) for _ in range(301)]; w.write('/tmp/training-301-pages.pdf')"
truncate -s 26M /tmp/training-26mb.pdf
printf 'This is not a PDF.\n' > /tmp/training-renamed.pdf
```

## Checklist

1. **Turn the package on.** As a platform admin, open Services, choose the test company and
   activate "Training library" with a reference. Expect "Training library" in the company's rail
   for its members. Before this, a member who types `/portal/training` reads "Training library is
   not switched on for this company. Ask your administrator."
2. **Add and read a PDF as the admin.** As the tenant admin, open Training library, press "Add a
   PDF or video", pick the landscape deck, give it a title and a category, and press "Add PDF". Expect
   "Added <title>. Everyone in your company can open it now." Open it:
   - it opens as slides: "Slide 1 of N", Next and Previous, the thumbnails, the arrow keys after
     you click the slide, and "Full screen" (on a phone, swipe left and right);
   - press "Document": every page in one scroll, with "Zoom in", "Zoom out" and "Fit width";
   - add the portrait document too: it opens as a document by default;
   - press "Download PDF": the file saves under the title.
3. **Edit the details.** Press "Edit details", change the title, the description, the category and
   "Show as", then "Save details". Expect "Saved the details of <title>." and the list, the filter
   and the item page to show the new values.
4. **Who sees the manage buttons.**
   - As the HR owner (plain staff, set on the Team page): "Add a PDF or video", "Edit details" and "Remove
     PDF" are there and work. Add one PDF and remove it again.
   - As the plain staff member and as the `read_only` member: the list and the viewer work, and
     none of the three buttons is there, on the list or on the item page.
5. **Summarise.** This needs the real provider key on the staging gateway.
   - As the `read_only` member, open a PDF that has no summary yet. Expect "Slide text is sent to
     our language-model provider when you make a summary." next to "Summarise this PDF". Press it:
     the summary and its key points appear, as plain text. There is no "Make a new summary".
   - As another member, open the same PDF: the same summary shows at once, with no button press.
   - As the tenant admin, press "Make a new summary", read "This replaces the summary everyone in
     your company sees.", then press "Replace the summary". A new summary replaces the old one.
   - Judge the summary: is it right, in the PDF's language, and free of anything the PDF did not
     say? Note what you see.
6. **Remove a PDF and check the file is gone.** Before you remove it, list the company's folder in
   the volume (read-only; put the company's id in place of `<company id>`):

   ```bash
   IMAGE=$(docker --host unix:///var/run/docker.sock inspect csa-staging-gateway-1 --format '{{.Image}}')
   docker --host unix:///var/run/docker.sock run --rm --network none --read-only --cap-drop ALL \
     --security-opt no-new-privileges:true --user 1000:1000 \
     -v csa-staging_staging-training-uploads:/data:ro \
     --entrypoint ls "$IMAGE" -la /data/<company id>
   ```

   Each PDF is one `<uuid>.pdf` (a new random name, not the item's id). As the tenant admin press
   "Remove PDF", read "This removes the PDF for everyone in your company.", then confirm. Expect
   "Removed <title> from the library." Run the command again: there is one `.pdf` fewer, and no
   `.part` file is left.
7. **Refused files.** In "Add a PDF or video":
   - `/tmp/training-26mb.pdf`: "This PDF is over 25 MB. Make it smaller, then add it again." It is
     refused before anything is sent.
   - `/tmp/training-renamed.pdf` (text named `.pdf`): "Only PDF files can be added. Choose a PDF
     file." This one is refused by the server.
   - Run step 6's command again: no file was left behind by either.
8. **Too many pages.** Add `/tmp/training-301-pages.pdf`. Expect "This PDF has more than 300
   pages. Split it into smaller PDFs, then add them." and no file left behind.
9. **The headers on the real path.** In the browser's developer tools, open a PDF and check the
   Network tab:
   - the `/console/v1/training/items/<id>/file` request answers `Content-Type: application/pdf`
     and `Cache-Control: private, no-store`, and Cloudflare's `cf-cache-status` is not `HIT`;
   - the `pdf.worker.min.<hash>.mjs` request answers `200` with a JavaScript `Content-Type`.
10. **The staging reset covers the new volume.** Run the reset's read-only plan (see "Resetting
    staging" in [deploy/STAGING_PIPELINE.md](../deploy/STAGING_PIPELINE.md)) and check that it
    lists `csa-staging_staging-training-uploads` with the other two upload volumes. Run it from
    the main checkout:

    ```bash
    .venv/bin/python -m scripts.staging_reset plan
    ```

## Video checklist

Run this after the rollout that brings video (migrations `0600` and `0601`, the `training-worker`
service, ffmpeg in the image) **and** after video is switched on for staging
(`CSA_STAGING_TRAINING_VIDEO=true`, a reviewed change; see the Training video steps in
[RUNBOOK.md](./RUNBOOK.md#training-video-the-training-worker)). Until then every upload answers "Adding
videos is not switched on yet. Ask your administrator." Use the same four accounts and a staging test
company. The sound of each video is sent to the speech-to-text provider and the transcript text to
the language-model provider, so use only recordings of made-up, non-confidential content. Rows marked
**production** cannot be run on staging (it keeps files on a local volume, not S3): run them once
production's switch is on, as the owner.

Make the test videos on your own machine (ffmpeg needed). The first is about 45 MB, so about six
pieces; check its size with `ls -l`:

```bash
ffmpeg -f lavfi -i testsrc2=duration=60:size=1280x720:rate=30 -f lavfi -i sine=frequency=300:duration=60 -c:v libx264 -preset ultrafast -b:v 6000k -c:a aac -shortest /tmp/training-big.mp4
ffmpeg -f lavfi -i testsrc2=duration=30:size=320x180:rate=25 -f lavfi -i anullsrc=r=16000:cl=mono -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest /tmp/training-silent.mp4
ffmpeg -f lavfi -i testsrc2=duration=30:size=320x180:rate=25 -c:v libx264 -pix_fmt yuv420p -an /tmp/training-no-sound.mp4
ffmpeg -f lavfi -i color=c=black:s=160x120:r=1 -f lavfi -i anullsrc=r=8000:cl=mono -t 3660 -c:v libx264 -preset ultrafast -crf 45 -pix_fmt yuv420p -c:a aac -b:a 8k /tmp/training-61-min.mp4
```

A tone is not speech, so these four prove the upload, the player and the refusals but not a
transcript. For a transcript you need a real recording of someone speaking made-up text, with a
picture (a phone video is fine), saved as MP4 with H.264: a short one of about 2 minutes, and one of
about 12 minutes (so it has three 5-minute parts) with a few plainly different topics, for example a
refund rule, a parking rule and a dress code, each stated once with a clear sentence you can
check later.

11. **Add a video and watch it go through.** As the tenant admin, press "Add a PDF or video" and pick
    the 2-minute recording. The dialog shows "The sound in this video is sent to our speech-to-text
    provider to make the transcript." Add it and give it a title. Expect the row to read
    "Uploading", then "Transcribing, 0 of 1 part" (parts of 5 minutes), then "Writing the summary",
    then to be a normal row. The page polls every 5 seconds; no reload is needed. Open it: the
    player, the transcript beside it (below it on a phone) and the summary, with "Written by the
    assistant from this video's transcript." Judge the transcript and the summary against what was
    said: right words, the right language, nothing invented. In the worker's log
    (`docker logs csa-staging-training-worker-1`) there is one `training_video_job` line per stage
    with `outcome=summarising` then `outcome=ready`, and no title, file name or transcript text.
12. **One real piece upload through the console on staging.** Add `/tmp/training-big.mp4` (about six
    pieces). Expect it to upload and be accepted; this is the first time a real browser sends pieces
    through staging's Cloudflare and the console's Next rewrite (the build proved a loopback
    stand-in, not this path). A tone has no speech, so it ends "This video has no speech, so there is
    no transcript or summary." (or, if the provider hears words in the tone, a short transcript: note
    which). The row's size reads about 44 MB.
13. **A throttled connection, then Continue.** In the browser's developer tools set the Network
    throttling to a slow profile (or "Offline" for a few seconds in the middle of the upload) while
    adding `/tmp/training-big.mp4` again under another title. Expect the pieces that fail to be sent
    again by themselves up to three times each, then "Upload stopped. Check your connection and select
    Continue." Remove the throttling and select "Continue": only the missing pieces are sent (watch
    the Network tab: pieces already sent are not sent again), and the video is accepted. Leaving the
    page during an upload asks first.
14. **A piece retried.** In the Network tab block one `/pieces/` request (right-click, "Block
    request URL"), then remove the block within a few seconds, before the three retries (after 1, 2
    and 4 seconds) are used up. Expect the console to send that piece again and finish without any
    message to you.
15. **A WebM recorded in Chrome.** A WebM from the browser's own recorder has no length in its header.
    Record 30 seconds of yourself speaking with a picture, from the DevTools console of an https page:

    ```js
    const s = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
    const r = new MediaRecorder(s), c = [];
    r.ondataavailable = (e) => c.push(e.data);
    r.onstop = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(c, { type: "video/webm" })); a.download = "training-recorded.webm"; a.click(); s.getTracks().forEach((t) => t.stop()); };
    r.start(); setTimeout(() => r.stop(), 30000);
    ```

    Add the file. Expect it to upload (the dialog does not refuse a file whose length it cannot read),
    and the worker to measure it and transcribe it. Note whether it plays and whether seeking in it
    works (a WebM with no index may not seek well).
16. **Playback in the browsers that matter (production for S3).** On staging the video comes from the
    gateway. On production it comes from a link to the bucket that lives 15 minutes. With the 2-minute
    recording (H.264 MP4) open it in desktop Chrome, desktop Safari and Safari on an iPhone, and for
    each: press play, drag the bar to the middle (it must seek, not restart), and let it play to the
    end. In Chrome pause it, wait 16 minutes so the link has expired, then press play: it must carry
    on from the same position after a moment, with no message. On an iPhone the video plays in the page,
    not full screen by itself. Note anything that does not play.
17. **Seek from the transcript, and `?t=`.** On the 12-minute recording press a time in the
    transcript: the video jumps there and plays, and the line is marked while it plays. Type a word in
    "Search the transcript": only matching lines stay. Then open the item's address with `?t=300`
    added: the video opens at 5:00. As a different account that is a plain staff member, do the same.
18. **No speech and no sound.** Add `/tmp/training-silent.mp4`: it becomes a normal row and its page says
    "This video has no speech, so there is no transcript or summary." and the worker made no provider
    call (no `training_transcribe_chunk` line for it). Add `/tmp/training-no-sound.mp4`: "This video has no
    sound, so there is no transcript or summary."
19. **Refused videos.** Add `/tmp/training-61-min.mp4`: Chrome can read its length and refuses before
    sending: "This video is longer than 60 minutes. Cut it into shorter videos, then add them." Add a
    renamed text file called `training-fake.mp4`: after the first piece "Only MP4, M4V, MOV or WebM
    videos can be added. Choose one of those files." and nothing is left behind (run step 6's command:
    no new file and no `.part`). If you can make a 61-minute WebM from the recorder (no length in its
    header), the worker refuses it and the row is a failed video with the same sentence, its file
    deleted and its size no longer counted.
20. **Retry.** "Try again" appears only on a failed video whose failure is one of the three that can
    be retried. Staging has no switch that makes the speech service fail, so run this row when one
    happens (a video failed with "Select Try again"): press it and expect the row to go back to
    "Transcribing" or "Writing the summary" and finish, without the earlier parts being sent again
    (the worker's log shows chunks starting where the earlier run stopped). If none happens, record
    it as not run.
21. **Delete while uploading, and while transcribing.** Start adding `/tmp/training-big.mp4` and, while
    it is uploading, open the library in a second tab and remove it ("Remove video", then confirm:
    "This removes the video, its transcript and its summary for everyone in your company."). Expect
    the upload to stop with a message, the row to go, and, with step 6's command, no `.part` or video
    file left for it. Then add the 12-minute recording and remove it while it reads "Transcribing":
    the row goes at once; the worker's log shows that job's `outcome=lost`, and nothing for it
    stays. As the database owner (see the runbook), these must both return nothing for the item's id:
    `SELECT 1 FROM public.training_video_jobs WHERE item_id = '<item id>';` and
    `SELECT 1 FROM public.training_transcript_segments WHERE item_id = '<item id>';`.
22. **An abandoned upload is swept.** Start adding a video and close the tab after one piece. About 24
    hours later, within an hour, the row is gone, the file is gone and the worker's log has
    `training_upload_abandoned` and then a `training_sweep` line with `abandoned=1`. (Before that,
    the manager still sees it as "Uploading".)
23. **Who sees what.** As the `read_only` member and as plain staff: the list shows only videos that
    are ready, with no "Add a PDF or video", "Edit details" or "Remove video" and no "Try again", and
    a video that is still being processed is not there. As the HR owner: add, edit and remove a video.
    As the `read_only` member, open a ready video: the player, the transcript and the summary work and
    there is no "Summarise" button.
24. **Ask the assistant, and "Watch from".** On the 12-minute recording's page ask the assistant
    "What does this video say about parking?" (use a topic you recorded). Expect an answer from the
    video that ends "From *title*, at mm:ss" and a "Watch from mm:ss" link that opens the video at
    that time. Ask about something it does not cover: it says so, with no time and no link. From
    another page ask by name ("What does the <title> video say about refunds?"). Clicking the same
    "Watch from" link again after you moved the video elsewhere does nothing: this is a known
    follow-up, not a failure of this row.
25. **A long transcript.** Add a recording of about 40 minutes of continuous speech (a transcript of
    about 35,000 characters; read aloud made-up text, or play a long text-to-speech file). When it
    is ready, ask the assistant a question about it. Expect an answer. If it says "I can't search this
    video right now. Try again later.", the staging model's per-request token limit was probably
    passed (a whole 35,000-character transcript is about 10,000 or more tokens, more in Malay): note
    the model and the limit.
26. **The assistant smoke after rollout.** From the staging host, with the real model key:

    ```bash
    docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke
    ```

    Expect PASS for every case, including `training_ask-en-1` and `training_ask-ms-1`, and the
    existing `training-*` cases still choosing an answer (no training request) and the training
    request cases still choosing `training_submit`. This was not run during the build; it is the
    check that adding the `training_ask` action did not move the planner's choice for HR training
    requests. Its rows are in [ASSISTANT_RETEST.md](./ASSISTANT_RETEST.md).
27. **Real speech, real CPU and time for ffmpeg (production).** The limits for one ffmpeg child
    (CPU 309 s, wall 300 s, address space 448 MiB) came from a looped 60-second test file with a sine
    tone, on a busy development host, not from real speech or the production instance. Add a real
    60-minute recording of speech (within 500 MB) on production and watch it: the `training_media_child`
    lines carry `elapsed_ms` (every one must be well under 300000), and
    `docker stats --no-stream csa-prod-training-worker-1` taken a few times during the job must stay
    under its 256m limit. A child killed by its limit shows as the job retrying; note the numbers
    either way.
28. **The real bucket (production).** Before the switch, the two checks in
    [OBJECT_STORAGE.md](./OBJECT_STORAGE.md) ("Checks only the real bucket can answer"): a piece with a
    wrong checksum is refused with `BadDigest`, and `ListParts` returns `ChecksumSHA256`; after the
    switch, `docker exec csa-prod-gateway-1 python -m scripts.storage_smoke` with every line PASS. The
    first real video on production is also the check of the https read in the real runtime image: the
    worker's `training_transcribe_probe` line for it shows a duration, and
    `docker exec csa-prod-training-worker-1 ls -l /etc/ssl/certs/ca-certificates.crt` shows the CA
    bundle ffmpeg verifies the link against.
29. **The ffmpeg host-name gap: the owner's decision.** The pinned ffmpeg checks the certificate chain
    of the link it reads from S3 but not the host name (see "Known limits of video" in
    [TRAINING_LIBRARY.md](./TRAINING_LIBRARY.md)). Record that you accept it for production, or that the
    follow-up (the worker fetches the bytes itself) is funded first. It does not affect staging, which
    reads local files.

## What staging cannot show

- **Real phones.** The browser tests run Chromium at phone widths. Swipes, full screen (iPhone
  Safari has none, so the button is hidden there), memory on a large deck and a phone's own
  zoom need a real Android phone and a real iPhone.
- **S3.** Staging stores files on a local volume. The `training/` prefix, its IAM statement, SSE-S3
  and the 7-day recovery of a deleted object are covered by `scripts.storage_smoke` and the S3
  fakes in the tests only.
- **Summary quality.** Step 5 shows one or two summaries from the real provider. How good they are
  across real decks, and in Malay, needs more real material than staging holds.
- **Very large or hostile PDFs under real CPU limits.** The upload checks run in a child process
  with time and memory limits. The tests use synthetic files; a 25 MB scan, a 300-page deck or a
  crafted file on the production host's CPU and memory has not been tried.
- **The production edge.** Step 9 checks the worker and the file through staging's Cloudflare and
  Caddy. Production's cache rules and headers are not proven by it: run step 9 again there.
- **Video from the real bucket.** Staging stores files on a local volume and plays them from the
  gateway, so a presigned link, S3's own Range answers, its multipart checks (`BadDigest`,
  `ListParts`) and the https read by ffmpeg are covered by the S3 fakes in the tests only, until rows
  16, 27 and 28 are run on production. The build had no bucket and no Safari: H.264 playback in
  Safari and on an iPhone is not proven.
- **Video quality and speed on real material.** Transcripts and summaries across accents, noisy rooms
  and Malay, the real model's answer to a question about a video (step 26, not run during the
  build), and how long a 60-minute recording takes the training worker on the production instance
  have not been measured. The capacity measurement used a looped test file with a tone.
- **Production capacity.** Whether the 2 GB production host can run the training worker and its
  ffmpeg next to the other services was not measured (the owner's `docker stats`, `free -m` and
  `df -h /` were not provided).
