# Training library retest checklist

Run this on staging after the rollout that brings the Training library (migrations `0540` and
`0541`, the `staging-training-uploads` volume and `TRAINING_UPLOAD_ROOT` on the gateway). Use a
staging test company and synthetic PDFs only: nothing confidential, because the summary step sends
the PDF's text to the language-model provider. How the library works is in
[TRAINING_LIBRARY.md](./TRAINING_LIBRARY.md).

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
   PDF", pick the landscape deck, give it a title and a category, and press "Add PDF". Expect
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
   - As the HR owner (plain staff, set on the Team page): "Add a PDF", "Edit details" and "Remove
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
7. **Refused files.** In "Add a PDF":
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
    lists `csa-staging_staging-training-uploads` with the other two upload volumes:

    ```bash
    cd /home/amsyrzmi/CSAV2 && .venv/bin/python -m scripts.staging_reset plan
    ```

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
