# Training video: capacity measurement

Measured 2026-11-13 on the shared dev host (not on production), against the current
`ghcr.io/amsyrzmi/csa-python` image. This is a finding only; no product code changed. It answers
two questions before the training worker is built: does a 320 MB container running ffmpeg on a
60-minute, ~500 MB video fit, and which ffmpeg do we ship.

## Recommendation

**Recommendation: separate training worker at 160 MB**

Every container peak is at most 132.6 MiB (49% of the 272 MiB bar, 41% of 320 MiB) and nothing was
OOM-killed. The 160 figure is the rule's output (measured need, rounded up to 32 MB). Two cautions
for whoever sets the limit, neither changes the recommendation:

- The measured baseline is the Python import set only. The real worker also holds a database pool,
  an S3 client and an HTTP upload buffer for a ~1.7 MB audio chunk; none of that was measured. The
  headroom at 160 MB is about 27 MiB over the worst container peak (and about 62 MiB over the worst
  non-reclaimable peak). Running the same limit as the existing workers, 320 MB, is still inside
  the rule (N at most 320) if more margin is wanted.
- Production reality was not provided (below), so whether the 2 GB host can take one more container
  of this size is not established by this document.

**Decision:** the production default limit is 256m (`CSA_PROD_MEM_TRAINING_WORKER`, tunable from the
host env file), not 160: the 160 was measured with the import set alone. The document's 160 MB is a
measurement, not the shipped value. Whether the production host can take the container is still not
established (see Production reality below).

## Limits on paper (`deploy/compose.production.yml`)

Defaults; every one can be overridden from the host env file (`CSA_PROD_MEM_*`).

| Service | `mem_limit` | `oom_score_adj` | Notes |
| --- | --- | --- | --- |
| db | 640m | -900 | last to be killed |
| gateway | 400m | 100 | |
| worker | 320m x 2 replicas = 640m | 500 | first to be killed |
| delivery | 200m | 300 | |
| frontend | 256m | 300 | |
| staging-embeddings | 1100m | 200 | "observed about 880 MB resident" per the compose comment |
| cloudflared | 96m | not set (0) | |
| **Sum of limits** | **3332m** | | host is 2048 MB: limits are 163% of RAM already |
| Sum with one more 160m worker | 3492m | | 171% of RAM |
| Sum with one more 320m worker | 3652m | | 178% of RAM |

The limits are caps, not reservations, and are already oversubscribed on purpose (the compose file
says the OOM killer should pick a worker or the frontend before Postgres). Whether one more
container fits therefore depends on resident use, which is the next section.

## Production reality

Not provided. The owner's read-only `docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'`
and `free -m` from csa-prod were not supplied, and production was not contacted. The arithmetic
above is from the compose limits only. Before the rollout, the actual resident total plus the
worker's expected use (about 100 to 135 MiB at peak from this measurement) must be compared with
the host's available memory and swap.

## Image sizes

Base is local image id `1d39970bac02` (`ghcr.io/amsyrzmi/csa-python@sha256:1d39970bac024ff4f55074b6c4ff612c64b38cb050d63170e59ea2909df66413`,
created 2026-11-13 00:21 UTC, Debian 12). `docker images` reports two sizes; "disk usage" is
unpacked, "content size" is compressed (what a pull transfers).

| Image | Disk usage | Content size | Added over base (disk / content) |
| --- | --- | --- | --- |
| base (csa-python) | 523 MB | 124 MB | |
| A: base + static ffmpeg and ffprobe (`COPY --from=<digest>`) | 901 MB | 231 MB | +378 MB / +107 MB |
| B: base + Debian `apt-get install --no-install-recommends ffmpeg` | 1.16 GB | 295 MB | +637 MB / +171 MB |

Image size after this change: 901 MB disk usage (523 MB before), built locally on 2026-11-13 from
`deploy/Dockerfile` the way the CI `package` job builds it (release context, the same build
arguments, `linux/amd64`). That is image A above. In it, `ffprobe -version` runs as user 1000 and
reports 7.1.1, the two binaries' sha256 equal the CI pins below, and
`/etc/ssl/certs/ca-certificates.crt` exists: an `https` ffprobe with `-tls_verify 1 -ca_file` that
bundle completes the TLS handshake, and fails with `certificate verify failed` without it.

The static `ffmpeg` and `ffprobe` are about 135 MB each, because each carries the full codec set.
A copies both. If image size matters later, ffprobe can be replaced by `ffmpeg -i` parsing or the
two can be built into one multi-call binary; that was not tried.

## ffmpeg: both builds

| | A: `mwader/static-ffmpeg` | B: Debian bookworm package |
| --- | --- | --- |
| Version | 7.1.1 (gcc 13.2.1, Alpine, static-pie) | 5.1.9-0+deb12u1 (gcc 12, shared libs) |
| Licence | GPL, version 3 or later (`--enable-gpl --enable-version3`, not `--enable-nonfree`) | GPL, version 2 or later (`--enable-gpl`, not `--enable-nonfree`) |
| `libopus`, `libx264`, `libx265`, `libvpx` | yes, yes, yes, yes | yes, yes, yes, yes |
| `aac` encoder | yes | yes |
| Demuxer/muxer `mov`, `matroska`, `ogg` | mov E D, matroska E D, ogg D E | mov E D, matroska E D, ogg D E |
| Filters `astats`, `ametadata` | yes, yes | yes, yes |
| Also present: `ebur128`, `loudnorm`, `volumedetect`, `silencedetect` | yes, all | yes, all |
| Updates | none: a new digest is a deliberate bump | Debian security updates arrive with the base image rebuild |

Both builds are GPL. Neither is an LGPL build, so neither can be called LGPL-clean. The images are
used in-house on our own hosts and are not redistributed here; whoever later distributes an image
outside the organisation should read the GPL terms. This is not legal advice.

The check was `ffmpeg -buildconf`, `-formats`, `-filters` and `-encoders` on each build; its output
was not kept.

## Chosen ffmpeg source

Chosen: **A**, the static build, because it passes the capacity measurement and is pinned by digest.

```
FFMPEG_IMAGE=mwader/static-ffmpeg@sha256:11a44711684c0b9f754c047dcd64235b8b52deab251bd0e0a86f22faa160749c
```

- This is the digest of the multi-arch manifest list for tag `7.1.1` (confirmed with
  `docker buildx imagetools inspect`). Its linux/amd64 manifest is
  `sha256:6769881cc02c80d33e387750a8e144d162adfab2775e934dd97899261dda3a0c` and its linux/arm64
  manifest is `sha256:89f3465bf74a353f59317d6c6a48b6169dcc6f2579f76c6d31d804042f378a42`.
- Binaries are at `/ffmpeg` and `/ffprobe` in that image (it has no shell). Copied out with
  `docker create` and `docker cp`. Extracted file digests, for the CI pin check:
  `ffmpeg` sha256 `810f94020e76e2b58fb44759a322e86bea5d213ebededad7471f3a15b0bf2c5c`,
  `ffprobe` sha256 `4818b8964b5d7b699370628a4154c97e88205678ee506ca72e9330600e917667`
  (linux/amd64 build).
- B was built and its capabilities checked, but its memory was not measured: B was to be measured
  only if A failed, and A did not.

## What was measured

A synthetic worst-case file, generated once on the host. Real recordings will differ in content
(speech audio, screen content), so treat the CPU and wall figures as indicative, the memory figures
as robust (they depend on container structure and buffers, not on content).

- 3600.03 s, 509,685,688 bytes (486 MiB, 1,132,626 bit/s overall), H.264 1280x720 30 fps plus AAC
  (sine tone, 48 kHz, 64 kbit/s). `worst.mp4`: `ftyp, free, mdat, moov` (moov at the end).
  `worst-faststart.mp4`: `ftyp, moov, free, mdat`.
- Built by encoding one 60 s segment with these x264 settings (`-preset ultrafast -b:v 1050k
  -maxrate 1050k -bufsize 2M -g 60`, aac 64k), then looping it 60 times with `-stream_loop 59 -c copy`.
  A single 60-minute encode of `testsrc2` would have taken about 40 minutes on this loaded host
  (1.5x real time); the loop gives the same size, container layout and bit rate.
  Audio is repetitive, so audio encoder cost (the CPU figures below) is that of a pure tone.
- `docker run --rm --memory=320m --memory-swap=320m --user 1000:1000 --read-only --tmpfs /tmp`
  on image A, file mounted read-only at `/in/video.mp4`, cgroup v2. One container per
  (case, file) pair. In each, a Python baseline process imports `csa.core.settings,
  csa.storage.s3, csa.decide.groq, csa.agents.kb.embedder, csa.speech, psycopg_pool, httpx` and
  then sleeps; a small driver (about 16 MiB, included in the cgroup numbers) then runs the case
  with `nice 10` and samples `/proc/<pid>/status` and the cgroup every 20 ms.
- Input arguments match `input_args` in `csa/training/media.py`: `-protocol_whitelist file -f mov -i <file>`,
  `-threads 1`, `-nostdin`, no stderr read. Cases:
  1. `ffprobe -v error -print_format json -show_format -show_streams` (stdout 4.4 KB).
  2. Loudness over the whole first audio stream: `-vn -map 0:a:0 -af
     asetnsamples=n=1048576,astats=metadata=1:reset=0,ametadata=mode=print:key=lavfi.astats.Overall.Max_level:file=-
     -f null -` (stdout 14 KB).
  3. Chunk extraction, three runs in one container (`-ss` 0, 1800, 3300 before `-i`, `-t 300 -vn
     -map 0:a:0 -ac 1 -ar 16000 -c:a libopus -b:a 32k -threads 1 -f ogg /tmp/c<ss>.ogg`); each chunk
     is about 1.70 MB. All three oggs stay in tmpfs together, which is the `/tmp` share.

### Peak table

Container columns are for the whole container. "Peak" is the cgroup `memory.peak` (includes page
cache from reading the mounted file, which the kernel reclaims under pressure). "Anon+shmem" is
the highest sampled `anon` plus the highest sampled `shmem` from `memory.stat` (a sum of two
maxima, so slightly conservative), the part that cannot be reclaimed.
The Python baseline's `VmHWM` was 61.3 to 61.5 MiB in every run (imports took 17 to 29 s on this
loaded host; that timing is not tabulated here). OOM kills: 0 in every container (`memory.events`
`max 0 oom 0 oom_kill 0`).

| Case | File | Container peak MiB | Anon+shmem MiB | ffmpeg `VmPeak` MiB | ffmpeg `VmHWM` MiB | Wall s | CPU s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ffprobe | moov at end | 129.1 | 68.5 | 173.8 | 34.7 | 0.54 | 0.47 |
| ffprobe | faststart | 72.9 | 68.5 | 173.8 | 35.8 | 0.29 | 0.27 |
| loudness, whole audio | moov at end | 132.6 | 97.9 | 218.1 | 53.5 | 64.18 | 72.25 |
| loudness, whole audio | faststart | 100.8 | 96.3 | 218.1 | 53.8 | 74.52 | 77.05 |
| chunks, `-ss` 0 / 1800 / 3300 | moov at end | 87.4 | 83.7 | 180.9 each | 35.6 / 36.0 / 35.6 | 26.60 / 32.95 / 32.16 | 21.39 / 22.21 / 22.04 |
| chunks, `-ss` 0 / 1800 / 3300 | faststart | 103.3 | 83.7 | 180.9 each | 35.9 / 35.8 / 35.7 | 28.79 / 26.91 / 27.82 | 21.75 / 21.56 / 21.70 |

Worst of each: container peak 132.6 MiB (loudness, moov at end); `VmPeak` 218.1 MiB (loudness);
CPU 77.05 s and wall 74.52 s (loudness, faststart). The 320 MiB limit was never approached.
Conclusions from the table:

- `-vn` and `-threads 1` keep ffmpeg's resident set at 34 to 54 MiB, so the container is dominated
  by the Python baseline (61 MiB) and the driver; ffmpeg adds at most about 54 MiB resident.
- The 500 MB file is never held in memory. Where moov is at the end, the demuxer seeks to read it
  and the extra cost is page cache (up to 57.6 MiB for ffprobe, a figure not tabulated here), not
  anonymous memory.
- `-ss` before `-i` costs the same at 0, 1800 and 3300 s, so there is no start-offset penalty.
- Chunk extraction costs about 22 CPU seconds per 300 s of audio here, almost all libopus encoding
  of a pure tone (decode and resample alone: 4.2 s on the host; 60 s of the same tone through
  libopus alone: 2.7 s wall; both side measurements, not tabulated here). A full 60-minute video is
  12 chunks, so about 4.5 CPU minutes of extraction plus 77 s of loudness; content-dependent, so
  remeasure with a real recording.
- ffmpeg's `VmPeak` of 174 to 218 MiB is address space, far above its resident size: it matters
  only for `RLIMIT_AS`, below.

## Derived child limits

The rules: CPU = 4 x worst measured CPU seconds; address space = 2 x worst `VmPeak`, rounded up to
64 MiB; wall = 4 x worst wall.

| Constant | Worst measured | Rule | Value |
| --- | --- | --- | --- |
| `FFMPEG_CHILD_CPU_SECONDS` | 77.05 s | 4 x 77.05 = 308.2 | 309 |
| `FFMPEG_CHILD_MEMORY_BYTES` | 218.1 MiB | 2 x 218.1 = 436.2 MiB, up to 448 MiB (7 x 64) | 469762048 |
| `FFMPEG_CHILD_WALL_SECONDS` | 74.52 s | 4 x 74.52 = 298.08 | 300 |

These are single-process limits for the slowest command (the loudness pass). The chunk command's
own worst figures (22.2 CPU s, 33.0 s wall) would give 89 s and 132 s if per-command limits are
wanted. The wall limit was measured on a busy 8-core host (its core count is not tabulated here);
csa-prod is a 2 GB Lightsail instance
whose CPU speed was not measured, so the wall limit is a starting value to check on the real host.
The address-space limit of 448 MiB is above the container's own 160 to 320 MB cap, so the cgroup
limit, not `RLIMIT_AS`, decides first; `RLIMIT_AS` only guards against runaway mappings.

## Thumbnail frame

Measured 2026-11-16 on the same shared dev host (8 cores, busy with the staging stack and other
projects), for the generated training thumbnail (pictures design, section 9). This is a finding
only; no product code changed. It answers: can one frame of a large AV1, 2160p HEVC, H.264 or
duration-less WebM video be decoded under the existing child limits inside a 256 MiB container,
and which output format and arguments does the build use.

### Frame: what was measured

Four synthetic files, generated at run time from ffmpeg `testsrc2` (moving colour bars, never
black) plus a 440 Hz sine, one at a time, each deleted once its cases had run (none kept, none
committed). As for the audio file above, each was built by encoding one short segment and looping
it with `-stream_loop N -c copy`. All four are under `MAX_VIDEO_BYTES` (524,288,000 bytes) and
`MAX_VIDEO_SECONDS` (3600).

| File | Video | Audio | Duration | Bytes | Layout | Built from |
| --- | --- | --- | --- | --- | --- | --- |
| AV1 | AV1 1920x1080 30 fps 8-bit; libsvtav1 preset 12 (SVT maps it to 11), 1050 kbit/s VBR, GOP 60 | AAC 64k | 3601.26 s | 499,215,256 (476 MiB) | mp4, no `-movflags`, so moov at the end | 60 s segment x 60 |
| HEVC | HEVC Main 10 3840x2160 30 fps `yuv420p10le`, tag `hvc1`; libx265 ultrafast, 1100 kbit/s, keyframe every 60 frames, no B-frames | AAC 64k | 3407.24 s | 516,432,009 (492.5 MiB) | mp4, moov at the end | 10 s segment x 340 |
| H.264 | H.264 High 1920x1080 30 fps; libx264 veryfast, 1050k maxrate, GOP 60, B-frames | AAC 64k | 3601.26 s | 509,420,599 (485.8 MiB) | `ftyp, free, mdat, moov` (printed) | 60 s segment x 60 |
| VP9 WebM | VP9 1920x1080 30 fps; libvpx-vp9 realtime, cpu-used 8, 1050k target, GOP 100 | Opus 64k | 3420 s | 511,462,520 (487.8 MiB) | WebM written to a pipe with `-live 1 -map_metadata -1`: unknown-size Segment, no Duration, no Cues (MediaRecorder-like) | 60 s segment looped, cut with `-t 3420` |

- HEVC: the bit rate is far below a phone's (tens of Mbit/s); under the size cap a real 4K phone
  clip is only a minute or two long. The file tests resolution, bit depth and index length (about
  102,000 video samples), not the per-frame cost of a high-bit-rate stream.
- VP9: the realtime encoder overshot its target (8.97 MB a minute), so 60 minutes would have been
  538 MB, over the cap; the file was cut at 57 minutes. A first attempt written to a pipe without
  `-live 1` still carried a Duration (the segment's 60.008 s) and was discarded. On the kept file
  ffprobe reports `duration=N/A`; the product's `media.probe` measured it by its demux pass at
  3,420,013 ms in 25.2 s (that is the existing duration child, not part of the frame cost).
- `docker run --rm --memory=256m --memory-swap=256m --cpus=1 --user 1000:1000 --read-only --tmpfs
  /tmp` on the runtime image `ghcr.io/amsyrzmi/csa-python@sha256:d94c3274a157ea91c0df253d8af7209474ee4271f402ab1977411c7c276f650f`
  (the image staging runs; its `csa/training/media.py` and `csa/training/limits.py` are identical
  to main `e896131f`; its `/usr/local/bin/ffmpeg` is 7.1.1 with the pinned sha256 `810f9402...`).
  File mounted read-only at `/in/video.mp4` or `/in/video.webm`. One container per (file, t). In
  each, a Python baseline process imports the set above plus `csa.training.media` and
  `csa.media.images`, then sleeps (`VmHWM` 61.1 to 61.7 MiB, imports 13 to 25 s); a driver then
  runs the frame command and samples `/proc/<pid>/status` and the cgroup every 5 ms; CPU seconds
  are the child's `wait4` rusage. Before each container the host page cache of the file was
  dropped (`posix_fadvise` `DONTNEED`), so the container's read is charged to it.
- The driver builds the argv with the module's own `_COMMON`, `input_args` and `_command`, so the
  child runs through `_LIMITED_EXEC` exactly as `_run` would: `RLIMIT_CPU` 309 s, `RLIMIT_AS`
  469,762,048 (448 MiB, `FFMPEG_CHILD_MEMORY_BYTES`), `RLIMIT_FSIZE`, open files, no core, `nice
  10`, empty environment, own session, stdin and stderr to `/dev/null`. The tool's arguments:

```
-nostdin -hide_banner -nostats -loglevel error -filter_threads 1 -threads 1
-ss <t> -protocol_whitelist file -f mov -enable_drefs 0 -i file:/in/video.mp4
-map 0:v:0 -an -sn -dn -frames:v 1 -vf scale=320:-2 -c:v mjpeg -q:v 5 -threads 1
-f image2pipe pipe:1
```

  (`-f matroska` and no `-enable_drefs` for the WebM, as `input_args` gives.) `t` was 1 s and 10%
  of the duration.
- Encoder check: the pinned 7.1.1 has `mjpeg` (`ffmpeg -encoders`: `VFS... mjpeg`; `png` and
  `libwebp` are also there). The default AV1 decoder in this build is `libdav1d`.

### Frame peak table

Columns as in the peak table above. "Before" is `memory.peak` just before the ffmpeg child
started (the baseline's imports plus the driver; it varies with the import peak). Anonymous
memory just before the child was 71.5 to 72.0 MiB (81.5 in the second AV1 run).
`memory.events` was `max 0 oom 0 oom_kill 0` in every container: `memory.max` was never reached.

| File | `t` s | Container peak MiB (before) | Anon+shmem MiB | ffmpeg `VmPeak` MiB | ffmpeg `VmHWM` MiB | CPU s | Wall s | Output bytes | Exit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AV1 1080p | 1 | 138.2 (74.3) | 127.7 | 214.6 | 66.4 | 1.58 | 3.36 | 5,219 | 0 |
| AV1 1080p | 360.126 | 125.4 (85.1) | 117.2 | 214.9 | 66.6 | 1.99 | 2.84 | 5,233 | 0 |
| HEVC 2160p 10-bit | 1 | 249.5 (98.2) | 216.6 | 302.8 | 155.4 | 6.27 | 9.74 | 4,160 | 0 |
| HEVC 2160p 10-bit | 340.724 | 224.4 (81.3) | 216.2 | 302.8 | 155.5 | 9.69 | 14.03 | 4,344 | 0 |
| H.264 1080p, moov at end | 1 | 134.8 (101.2) | 108.6 | 204.8 | 58.4 | 1.56 | 1.91 | 5,165 | 0 |
| H.264 1080p, moov at end | 360.126 | 120.4 (74.3) | 111.9 | 207.9 | 60.4 | 2.28 | 2.84 | 5,186 | 0 |
| VP9 WebM, no duration | 1 | 98.0 (78.5) | 92.9 | 185.4 | 41.7 | 1.81 | 2.32 | 5,227 | 0 |
| VP9 WebM, no duration | 342.001 | 143.9 (80.6) | 90.4 | 182.8 | 39.2 | 3.51 | 4.66 | 5,612 | 0 |

Every output passed `csa.media.images.check_image(data, "thumb.jpg")` (JPEG, 320x180) and was
opened and looked at: colour bars at the expected place, none black or empty. Worst of each:
container peak 249.5 MiB, anon+shmem 216.6 MiB, `VmPeak` 302.8 MiB, `VmHWM` 155.5 MiB (all HEVC
2160p); CPU 9.69 s and wall 14.03 s (HEVC at 10%). Conclusions:

- 2160p 10-bit HEVC is the one tight case. Its decoder holds 4K 10-bit frames (`VmHWM` 155 MiB
  against 39 to 67 MiB at 1080p). The container peak of 249.5 MiB is 97% of 256 MiB, but 28.8 MiB
  of it is page cache; the non-reclaimable part, 216.6 MiB, leaves about 39 MiB over a baseline
  that holds only the imports. `VmPeak` 302.8 MiB is 68% of the 448 MiB address-space limit.
- 1080p AV1, H.264 and VP9: container at most 143.9 MiB, `VmHWM` at most 66.6 MiB, CPU at most
  3.51 s.
- CPU follows the number of frames decoded from the keyframe before `t` to `t` (accurate seek).
  4K 10-bit HEVC decodes at about 0.1 CPU s a frame on one thread here (side timing, not
  tabulated: the first frame alone 0.5 s user, 30 frames 2.0 s). At 340.724 s the keyframe before
  was at 338.705 s (the next at 340.726 s), so about 60 frames were decoded. `-noaccurate_seek`
  was tried on that case and saved nothing (11.68 CPU s, 15.74 s wall); it is not used.
- The WebM without Cues seeks: the matroska demuxer reads forward to `t` (49.2 MiB of page cache,
  about the first 342 s of the file) and lands on the asked frame (side check with `-copyts -vf
  showinfo`: pts 342.009 for `-ss 342.001`, 1.007 for `-ss 1`). No fallback to 1 s was needed.
- With `t = min(1 s, 10% of the duration)` (spec section 9.1), `t` is never above 1 s, so at most
  the first second of frames is decoded whatever the keyframe interval (a stream starts on a
  keyframe). The 10% rows are therefore the upper side. Moving `t` later in a long video would
  make the CPU follow the keyframe distance; a browser recording with sparse keyframes was not
  measured.
- Side checks on 2 s files in tmpfs (not tabulated): `-ss` past the end delivers no frame, the
  encoder then opens with limited-range `yuv420p` and refuses (`Non full-range YUV is
  non-standard`), exit 234, 0 bytes: a refusal, so no thumbnail. Inputs tagged `color_range=tv`
  (H.264 BT.709 and 10-bit HEVC BT.2020 HLG) give a JPEG with plain `scale=320:-2`, byte-identical
  to `scale=320:-2:out_range=full,format=yuvj420p`, so no format filter is needed. HDR is not
  tone-mapped: an HDR phone clip's thumbnail will look flat (not looked at).

JPEG size by quality (AV1 frame at 1 s, 320x180; synthetic bars compress well, so real frames
will be larger, not measured): `-q:v` 2: 7,839 bytes, 3: 6,526, 5: 5,219, 8: 4,140, 12: 3,323; no
`-q:v`: 6,526; PNG: 20,340. Every one is far under `PROBE_MAX_OUTPUT_BYTES` (256 KiB) and
`MAX_PICTURE_BYTES` (2 MiB).

Picture limits: 320 px wide is inside 64-4096. The height is 320 divided by the aspect ratio,
rounded to even: 180 for 16:9, 568 for 9:16 portrait, 240 for 4:3. A video wider than 5:1 gives a
height under 64, and one wider than 10:1 or taller than 1:10 breaks the 10-to-1 ratio rule (before
4096 is reached), so `check_image` refuses those: no thumbnail.

### Decision for Task 17

- **Format:** JPEG (`mjpeg`, present in the pinned 7.1.1), stored as `image/jpeg`. PNG is not
  needed.
- **Arguments:** `*_COMMON, "-threads", "1", "-ss", <t>, *input_args(source, container), "-map",
  "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1", "-vf", "scale=320:-2", "-c:v", "mjpeg", "-q:v",
  "5", "-threads", "1", "-f", "image2pipe", "pipe:1"`, with `t = min(1 s, 10% of the probed
  duration)`, through the same `_run`. Output read from stdout under the existing
  `PROBE_MAX_OUTPUT_BYTES`; the parse step runs `check_image(output, "thumb.jpg")`, and an empty
  output or a refusal means no thumbnail (no second attempt at another `t`).
- **Caps:** not raised. `RLIMIT_AS` stays 448 MiB, 1.48x the worst `VmPeak` (302.8 MiB): less
  than the 2x margin rule above (which would give 640 MiB); a stream that needs more fails
  and gets no thumbnail. By the 4x rule on the frame's own worst figures, per-command limits would
  be CPU 4 x 9.69 = 38.76, so 39 s, and wall 4 x 14.03 = 56.1, so 60 s; recommended if `_run` can
  take per-command limits, otherwise the shared 309 s and 300 s apply (larger, not raised).
- **No thumbnail:** none of the four measured cases. By the rule, a video gets none when no frame
  reaches the encoder (exit non-zero), when the JPEG fails `check_image` (wider than 5:1, or taller
  than 1:10), or when the child is killed or passes its limits. Caution for 2160p 10-bit HEVC in the
  256 MiB production container: about 39 MiB of non-reclaimable headroom over the import baseline
  only, and the real worker holds more. If the cgroup OOM killer fires, ffmpeg (the largest
  resident set, 155 MiB) is the likely victim, which is a `SIGKILL`, `MediaUnavailable("failed")`,
  caught by the step: no thumbnail. If it picks the worker instead, the job fails, so run the
  frame step while no other child or upload buffer is alive.

## Not measured

- Production memory (not provided; production not contacted).
- Image B's memory, CPU and wall (not needed once A passed).
- Real recordings (speech, screen content, HEVC or VP9 input). Only H.264 plus AAC MP4 was run
  for the audio commands; the thumbnail frame was run on the four synthetic files above.
- For the thumbnail frame: real camera content and high-bit-rate 4K, an HTTPS source, how an HDR
  clip's frame looks, a browser recording with sparse keyframes, and the real worker's memory
  beside the child.
- Input over HTTPS from S3 (`-protocol_whitelist https,tls,tcp`); the file was local. A network
  source adds TLS buffers and removes the page-cache cost.
- Two ffmpeg commands at once, or the real worker's pool, S3 client and upload buffers.
- CPU speed of the production instance.

## Reproduce

The drivers and the media were not kept (no media or extra Dockerfiles are committed). To repeat,
build image A (`FROM` the base id, `COPY --from=<FFMPEG_IMAGE> /ffmpeg /ffprobe /usr/local/bin/`),
create the two files as above, and run each case with the `docker run` line under "What was
measured".

Thumbnail frame: generate the four files as in its table (encode one segment from `testsrc2`, then
`-stream_loop N -c copy`; the WebM is written to a pipe with `-f webm -live 1 -map_metadata -1` so
it has no Duration and no Cues), one at a time. Drop the file's page cache, then run one container
per (file, `t`) with the `docker run` line under "Frame: what was measured" on the runtime image,
with a driver that builds the argv from `csa.training.media` (`_COMMON`, `input_args`, `_command`)
so the child gets the product's limits. Delete each file once its cases have run.
