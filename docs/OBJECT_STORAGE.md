# Object storage (S3)

Knowledge-base uploads, HR documents and training library files (PDFs and videos) are stored through one interface
with two backends: the local `*_UPLOAD_ROOT` folders (the default; dev, CI and current staging) or one S3 bucket.
The database keeps only file names, so the backend is a deployment choice, not a data change.

## Settings

| Setting | Meaning |
| --- | --- |
| `STORAGE_BACKEND` | `local` (default) or `s3`. |
| `KB_UPLOAD_ROOT`, `HR_UPLOAD_ROOT`, `TRAINING_UPLOAD_ROOT` | Local backend only: the folder for each store. Unset means that store is not configured. None may be, contain or sit inside another. |
| `S3_BUCKET` | Bucket name. |
| `S3_REGION` | Bucket region, for example `ap-southeast-1`. |
| `S3_ACCESS_KEY_ID` | Access key id of the application key. |
| `S3_SECRET_ACCESS_KEY` | Secret access key of the application key. |

With `STORAGE_BACKEND=s3` all four S3 values are required, and `KB_UPLOAD_ROOT`,
`HR_UPLOAD_ROOT` and `TRAINING_UPLOAD_ROOT` must be unset. Anything else stops the process at startup; it never falls back
to local disk. The gateway and the ingest worker also run the store's startup check and stop if
the bucket is unreachable or the key is refused.

Keep the two key values in secret files, not in `.env` files or compose files. Each setting is
read from the Docker secret file `/run/secrets/<SETTING>` or from an environment variable of the
same name. If both exist, the environment variable wins, so production must not set
`S3_ACCESS_KEY_ID` or `S3_SECRET_ACCESS_KEY` in the environment, only as secret files:

- `/run/secrets/S3_ACCESS_KEY_ID`
- `/run/secrets/S3_SECRET_ACCESS_KEY`

Create them on the server with mode `0400`, owned by the container user (uid 1000). Never put
the keys in git, logs or chat, and rotate the key if one is ever pasted anywhere.

## Layout

Objects are stored as `kb/<client_id>/<name>`, `hr/<client_id>/<name>` and
`training/<client_id>/<name>`, all with S3-managed encryption (SSE-S3). The local layout is
`<root>/<client_id>/<name>`. No existing files need copying for `training/`: no training files
existed before the store was added.

## IAM policy for the application key

The application key can list only under `kb/`, `hr/` and `training/`, read and write objects there, and
write (never read or delete) under `backups/`. It has no permission on object versions, so a
leaked key cannot destroy history. Replace `<bucket>` with the bucket name.

Training videos arrive piece by piece as an S3 multipart upload, which the gateway starts, fills,
completes and, when a video is removed, aborts. The calls it makes and the permission each needs:
`CreateMultipartUpload`, `UploadPart` and `CompleteMultipartUpload` need `s3:PutObject` (already
granted); `AbortMultipartUpload` needs `s3:AbortMultipartUpload`; `ListParts` (only when S3 refuses
a completion, to find which pieces to send again) needs `s3:ListMultipartUploadParts`; `HeadObject`
(only when a completion's answer was lost) and the presigned playback link need `s3:GetObject`
(already granted). The statement `TrainingMultipart` below adds the two new actions, on `training/`
only. The gateway never lists open uploads; the training worker does.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListOwnPrefixes",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<bucket>",
      "Condition": { "StringLike": { "s3:prefix": ["kb/*", "hr/*", "training/*"] } }
    },
    {
      "Sid": "ReadWriteDeleteFiles",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": [
        "arn:aws:s3:::<bucket>/kb/*",
        "arn:aws:s3:::<bucket>/hr/*",
        "arn:aws:s3:::<bucket>/training/*"
      ]
    },
    {
      "Sid": "TrainingMultipart",
      "Effect": "Allow",
      "Action": ["s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
      "Resource": "arn:aws:s3:::<bucket>/training/*"
    },
    {
      "Sid": "BackupsPutOnly",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::<bucket>/backups/*"
    }
  ]
}
```

## IAM policy for the worker key

The ingest worker only reads knowledge-base files, so production gives it a second key that can
list under `kb/` (its startup check) and read objects there, and nothing else. It cannot read HR
documents, write, or delete. Replace `<bucket>` with the bucket name:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListKnowledgeBaseOnly",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<bucket>",
      "Condition": { "StringLike": { "s3:prefix": ["kb/*"] } }
    },
    {
      "Sid": "ReadKnowledgeBaseFiles",
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::<bucket>/kb/*"
    }
  ]
}
```

A missing object answers 404 for the application key (checked on the real bucket). The worker key
has the same list right, so the same answer is expected, but it has not been checked: the ingest
retry treats 404 as "this file is gone" and anything else as an error. The key goes in the secret files
`storage/s3_worker_access_key_id` and `storage/s3_worker_secret_access_key` (`deploy/PRODUCTION.md`).

## IAM policy for the training worker key

The training worker (`python -m apps.training_worker.main`) transcribes and summarises videos and
sweeps abandoned uploads. Production gives it a third key, limited to `training/`, so it never
holds the application key or the knowledge-base worker key. Its calls and their permissions:

| Call | Why | Permission |
| --- | --- | --- |
| `ListObjectsV2` under `training/` | startup check; the hourly sweep of stored names | `s3:ListBucket` with prefix `training/*` |
| `ListMultipartUploads` under `training/<company>/` | the hourly sweep of open uploads no row keeps | `s3:ListBucketMultipartUploads` on the bucket |
| `AbortMultipartUpload` | abort those uploads | `s3:AbortMultipartUpload` on `training/*` |
| `HeadObject` | the video's size, when ffmpeg cannot read it | `s3:GetObject` on `training/*` |
| presigned `GetObject` (15 minutes) | ffmpeg and ffprobe read the video through the link | `s3:GetObject` on `training/*` |
| `DeleteObject` | a refused video, and stored names no row keeps | `s3:DeleteObject` on `training/*` |

It never writes: no `s3:PutObject`. `s3:ListBucketMultipartUploads` is granted on the bucket with no
prefix condition, so that one action sees the names of every open multipart upload in the bucket
(names only: it cannot read, finish or abort one outside `training/`). Replace `<bucket>`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListTrainingOnly",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<bucket>",
      "Condition": { "StringLike": { "s3:prefix": ["training/*"] } }
    },
    {
      "Sid": "ListOpenUploads",
      "Effect": "Allow",
      "Action": "s3:ListBucketMultipartUploads",
      "Resource": "arn:aws:s3:::<bucket>"
    },
    {
      "Sid": "ReadDeleteAbortTrainingFiles",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
      "Resource": "arn:aws:s3:::<bucket>/training/*"
    }
  ]
}
```

The key goes in the secret files `storage/s3_training_access_key_id` and
`storage/s3_training_secret_access_key`, and `deploy/production-rollout.env` names it with
`CSA_PROD_S3_TRAINING_KEY=prod_s3_training` (`deploy/PRODUCTION.md`, "Training video"). Until then
the training worker reads the disabled placeholder and stays idle while video is off; with video on
and the placeholder it refuses to start.

## Bucket settings

- Private, with all four "Block public access" options on.
- Default encryption: SSE-S3 (`AES256`).
- Versioning on.
- Bucket policy that refuses any request not made over TLS (replace `<bucket>`):

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "DenyInsecureTransport",
        "Effect": "Deny",
        "Principal": "*",
        "Action": "s3:*",
        "Resource": ["arn:aws:s3:::<bucket>", "arn:aws:s3:::<bucket>/*"],
        "Condition": { "Bool": { "aws:SecureTransport": "false" } }
      }
    ]
  }
  ```

- Lifecycle, two rules:
  - `backups/`: current versions expire after 14 days.
  - Whole bucket: non-current versions expire after 7 days, expired object delete markers are
    removed (`ExpiredObjectDeleteMarker: true`), and incomplete multipart uploads are aborted
    after 2 days (`AbortIncompleteMultipartUpload`, `DaysAfterInitiation: 2`).

  The 2 days are for `training/`: a video upload is kept open while pieces keep arriving and is
  abandoned only after 24 hours with no new piece, so a rule that counts from the upload's start
  must give it longer than that. Before training video, this rule said 1 day; change it to 2
  before video is switched on. A separate `training/` rule at 2 days is not enough while the
  whole-bucket rule says 1: both apply to `training/`, and the shorter one aborts first. The rule
  is a backstop; the training worker aborts abandoned uploads itself every hour.

## Copy existing local files into S3

Do this once, before switching a deployment from `local` to `s3`, with an ADMIN profile (not
the application key, which cannot list the whole bucket). Names do not change; only the prefix
is added. The staging volumes are `csa-staging_staging-uploads` (knowledge base) and
`csa-staging_staging-hr-uploads` (HR). Run these on the host, replacing `<bucket>` and `<admin>`:

```bash
set -euo pipefail

# 0. Fail on a mistyped volume name: Docker would otherwise create an empty volume silently.
docker volume inspect csa-staging_staging-uploads > /dev/null
docker volume inspect csa-staging_staging-hr-uploads > /dev/null

TMP=$(mktemp -d)
[ -n "$TMP" ] && [ -d "$TMP" ] || { echo "mktemp failed" >&2; exit 1; }
# The copy runs as root inside Docker, so files can be root-owned: remove with sudo, even when
# a command below fails.
trap 'sudo rm -rf "$TMP"' EXIT
mkdir "$TMP/kb" "$TMP/hr"

# 1. Copy each volume out with a throwaway container; the volumes are mounted read-only.
docker run --rm --entrypoint cp -v csa-staging_staging-uploads:/src:ro -v "$TMP/kb":/dst \
  python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c \
  -a /src/. /dst/
docker run --rm --entrypoint cp -v csa-staging_staging-hr-uploads:/src:ro -v "$TMP/hr":/dst \
  python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c \
  -a /src/. /dst/
sudo chown -R "$(id -u):$(id -g)" "$TMP"

# 2. Upload: show both plans with --dryrun, read them, then confirm.
aws s3 sync "$TMP/kb" s3://<bucket>/kb/ --profile <admin> --dryrun
aws s3 sync "$TMP/hr" s3://<bucket>/hr/ --profile <admin> --dryrun
read -r -p "Proceed with the real upload? [y/N] " ok
[ "$ok" = y ] || exit 1
aws s3 sync "$TMP/kb" s3://<bucket>/kb/ --profile <admin>
aws s3 sync "$TMP/hr" s3://<bucket>/hr/ --profile <admin>

# 3. Verify: object count and total bytes in the bucket must equal the local copy.
# `aws s3 ls` exits 1 for an empty prefix, which pipefail would turn into an abort, so the
# summary lines tolerate it. A real error still prints and no Total lines follow it.
aws s3 ls s3://<bucket>/kb/ --recursive --summarize --profile <admin> | tail -2 || true
find "$TMP/kb" -type f | wc -l
find "$TMP/kb" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}'
aws s3 ls s3://<bucket>/hr/ --recursive --summarize --profile <admin> | tail -2 || true
find "$TMP/hr" -type f | wc -l
find "$TMP/hr" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}'

# 4. Remove the temporary copy (it holds real files); the trap does it again harmlessly.
sudo rm -rf "$TMP"
```

Save the block as a script file and run it with `bash` in a terminal (it asks for a
confirmation before the real upload), rather than pasting it into your login shell, because
`set -e` would end that shell on the first error.

Compare "Total Objects" with the first `find` count and "Total Size" with the byte total printed
after it, for each of kb and hr; they must be equal.

The temporary directory holds real HR files and needs enough free space for both volumes. The
container copies the files as root, so they can be root-owned and the removal uses `sudo`. The
`trap` removes the directory even if the run fails; if you ran the commands another way, run
`sudo rm -rf "$TMP"` yourself before doing anything else.

Copy while uploads are quiet, or run the sync again after the switch is planned so files added in
between are included.

## Never mix the backends

A deployment uses either `local` or `s3`, never both. Switching `STORAGE_BACKEND` needs the copy
above first; after that nothing else changes, because the database holds only file names. Files
written to one backend are invisible to the other, so switching back without copying again loses
sight of everything written in between.

## How to verify

Run the smoke script inside the gateway container, so it uses the real application key:

```bash
docker exec csa-prod-gateway-1 python -m scripts.storage_smoke
```

It writes one small object per store, under a random tenant id in the `kb`, `hr` and `training` prefixes, and
checks the startup check, put and read, listing, streaming, delete, and reading a missing name, then
removes them. The training store also gets a multipart upload of three pieces (about 10 MiB),
completed and read back whole, a second upload aborted after one piece, and a GET of the first 16
bytes through a presigned playback link, which must answer 206 as it does for a browser's video
element. The multipart lines expect the application key's `TrainingMultipart` statement (above):
without it the abort line fails with `AccessDenied` and so does the cleanup line, which still
removes the completed object and names what it could not remove (the denied upload, left for the
lifecycle rule). `s3:ListMultipartUploadParts` is used only when S3 refuses a completion, so the
smoke cannot show it is missing. It prints one `PASS` or `FAIL` line per check and never prints
keys, links, object names or contents.
Exit code 0 means every check passed, 1 means a check failed, 2 means the settings are unusable.
Run it in the gateway only: the training worker's key cannot write.

Reading a missing object may answer `StorageNotFound` (S3 404) or `StorageError` (S3 403, which
a prefix-conditioned policy can return for a missing key). Both pass; the line says which one
happened.

## Checks only the real bucket can answer

The test double (moto) accepts a piece whose checksum is wrong and leaves `ChecksumSHA256` out of
`ListParts`, so two behaviours the upload code relies on are proven only on the real bucket. Run
this once in CloudShell with the admin login before video is switched on (replace `<bucket>`):

```bash
B=<bucket>; K="training/smoke-check-$(date +%s).bin"
head -c 1024 /dev/urandom > part.bin
GOOD=$(openssl dgst -sha256 -binary part.bin | base64)
BAD=$(printf other | openssl dgst -sha256 -binary | base64)
U=$(aws s3api create-multipart-upload --bucket "$B" --key "$K" --checksum-algorithm SHA256 --query UploadId --output text)
# 1. Must fail with BadDigest: the gateway reads that as "send this piece again".
aws s3api upload-part --bucket "$B" --key "$K" --upload-id "$U" --part-number 1 --body part.bin --checksum-sha256 "$BAD"
# 2. Must succeed, and list-parts must print the same value as: echo "$GOOD"
aws s3api upload-part --bucket "$B" --key "$K" --upload-id "$U" --part-number 1 --body part.bin --checksum-sha256 "$GOOD"
aws s3api list-parts --bucket "$B" --key "$K" --upload-id "$U" --query 'Parts[].ChecksumSHA256'
echo "$GOOD"
aws s3api abort-multipart-upload --bucket "$B" --key "$K" --upload-id "$U"
rm part.bin
```

If step 1 succeeds, or step 2 prints no checksum, stop: a corrupted piece would not be caught by
S3 and a refused completion could not name the pieces to resend. Report it before video is on.

## Behaviour notes

- A download whose object is missing or unreadable in the bucket answers 503, not 404.
- A transient S3 error during knowledge-base ingest is retried by the ingest job's normal retry.
- Deleted files stay recoverable for 7 days as non-current versions in the bucket (versioning
  plus the lifecycle rule); ask an admin to restore one. The smoke script's own deletes leave
  the same kind of non-current versions, which expire after 7 days, and the lifecycle then
  removes the leftover delete markers.
