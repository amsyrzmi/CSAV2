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
  the rule (N at most 320) if the controller prefers margin.
- Production reality was not provided (below), so whether the 2 GB host can take one more container
  of this size is not established by this document.

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

Capability output is in `/tmp/training-video-scratch/caps-a.txt` and `caps-b.txt` (scratch, not
committed). The check was `ffmpeg -buildconf`, `-formats`, `-filters` and `-encoders`.

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
- B was built and its capabilities checked, but its memory was not measured: the brief measures B
  only if A fails, and A did not.

## What was measured

A synthetic worst-case file, generated once on the host. Real recordings will differ in content
(speech audio, screen content), so treat the CPU and wall figures as indicative, the memory figures
as robust (they depend on container structure and buffers, not on content).

- 3600.03 s, 509,685,688 bytes (486 MiB, 1,132,626 bit/s overall), H.264 1280x720 30 fps plus AAC
  (sine tone, 48 kHz, 64 kbit/s). `worst.mp4`: `ftyp, free, mdat, moov` (moov at the end).
  `worst-faststart.mp4`: `ftyp, moov, free, mdat`.
- Built by encoding one 60 s segment with the brief's x264 settings (`-preset ultrafast -b:v 1050k
  -maxrate 1050k -bufsize 2M -g 60`, aac 64k), then looping it 60 times with `-stream_loop 59 -c copy`.
  The brief's single 60-minute encode of `testsrc2` would have taken about 40 minutes on this
  loaded host (1.5x real time); the loop gives the same size, container layout and bit rate.
  Audio is repetitive, so audio encoder cost (the CPU figures below) is that of a pure tone.
- `docker run --rm --memory=320m --memory-swap=320m --user 1000:1000 --read-only --tmpfs /tmp`
  on image A, file mounted read-only at `/in/video.mp4`, cgroup v2. One container per
  (case, file) pair. In each, a Python baseline process imports `csa.core.settings,
  csa.storage.s3, csa.decide.groq, csa.agents.kb.embedder, csa.speech, psycopg_pool, httpx` and
  then sleeps; a small driver (about 16 MiB, included in the cgroup numbers) then runs the case
  with `nice 10` and samples `/proc/<pid>/status` and the cgroup every 20 ms.
- Input arguments match the plan's `input_args`: `-protocol_whitelist file -f mov -i <file>`,
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
loaded host). OOM kills: 0 in every container (`memory.events` `max 0 oom 0 oom_kill 0`).

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
  and the extra cost is page cache (up to 57.6 MiB for ffprobe), not anonymous memory.
- `-ss` before `-i` costs the same at 0, 1800 and 3300 s, so there is no start-offset penalty.
- Chunk extraction costs about 22 CPU seconds per 300 s of audio here, almost all libopus encoding
  of a pure tone (decode and resample alone: 4.2 s on the host; 60 s of the same tone through
  libopus alone: 2.7 s wall). A full 60-minute video is 12 chunks, so about 4.5 CPU minutes of
  extraction plus 77 s of loudness; content-dependent, so remeasure with a real recording.
- ffmpeg's `VmPeak` of 174 to 218 MiB is address space, far above its resident size: it matters
  only for `RLIMIT_AS`, below.

## Derived child limits (for Task 7)

Rules from the brief: CPU = 4 x worst measured CPU seconds; address space = 2 x worst `VmPeak`,
rounded up to 64 MiB; wall = 4 x worst wall.

| Constant | Worst measured | Rule | Value |
| --- | --- | --- | --- |
| `FFMPEG_CHILD_CPU_SECONDS` | 77.05 s | 4 x 77.05 = 308.2 | 309 |
| `FFMPEG_CHILD_MEMORY_BYTES` | 218.1 MiB | 2 x 218.1 = 436.2 MiB, up to 448 MiB (7 x 64) | 469762048 |
| `FFMPEG_CHILD_WALL_SECONDS` | 74.52 s | 4 x 74.52 = 298.08 | 300 |

These are single-process limits for the slowest command (the loudness pass). The chunk command's
own worst figures (22.2 CPU s, 33.0 s wall) would give 89 s and 132 s if Task 7 wants per-command
limits. The wall limit was measured on a busy 8-core host; csa-prod is a 2 GB Lightsail instance
whose CPU speed was not measured, so the wall limit is a starting value to check on the real host.
The address-space limit of 448 MiB is above the container's own 160 to 320 MB cap, so the cgroup
limit, not `RLIMIT_AS`, decides first; `RLIMIT_AS` only guards against runaway mappings.

## Not measured

- Production memory (not provided; production not contacted).
- Image B's memory, CPU and wall (not needed once A passed).
- Real recordings (speech, screen content, HEVC or VP9 input). Only H.264 plus AAC MP4 was run.
- Input over HTTPS from S3 (`-protocol_whitelist https,tls,tcp`); the file was local. A network
  source adds TLS buffers and removes the page-cache cost.
- Two ffmpeg commands at once, or the real worker's pool, S3 client and upload buffers.
- CPU speed of the production instance.

## Reproduce

Scratch lives in `/tmp/training-video-scratch` and is deleted afterwards. The drivers are not
committed (the task forbids committing media or Dockerfiles); to repeat, build image A
(`FROM` the base id, `COPY --from=<FFMPEG_IMAGE> /ffmpeg /ffprobe /usr/local/bin/`), create the two
files as above, and run each case with the `docker run` line under "What was measured".
