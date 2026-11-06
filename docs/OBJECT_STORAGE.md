# Object storage (S3)

Knowledge-base uploads and HR documents are stored through one interface with two backends:
the local `*_UPLOAD_ROOT` folders (the default; dev, CI and current staging) or one S3 bucket.
The database keeps only file names, so the backend is a deployment choice, not a data change.

## Settings

| Setting | Meaning |
| --- | --- |
| `STORAGE_BACKEND` | `local` (default) or `s3`. |
| `S3_BUCKET` | Bucket name. |
| `S3_REGION` | Bucket region, for example `ap-southeast-1`. |
| `S3_ACCESS_KEY_ID` | Access key id of the application key. |
| `S3_SECRET_ACCESS_KEY` | Secret access key of the application key. |

With `STORAGE_BACKEND=s3` all four S3 values are required, and `KB_UPLOAD_ROOT` and
`HR_UPLOAD_ROOT` must be unset. Anything else stops the process at startup; it never falls back
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

Objects are stored as `kb/<client_id>/<name>` and `hr/<client_id>/<name>`, all with S3-managed
encryption (SSE-S3). The local layout is `<root>/<client_id>/<name>`.

## IAM policy for the application key

The application key can list only under `kb/` and `hr/`, read and write objects there, and
write (never read or delete) under `backups/`. It has no permission on object versions, so a
leaked key cannot destroy history. Replace `<bucket>` with the bucket name.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListOwnPrefixes",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<bucket>",
      "Condition": { "StringLike": { "s3:prefix": ["kb/*", "hr/*"] } }
    },
    {
      "Sid": "ReadWriteDeleteFiles",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::<bucket>/kb/*", "arn:aws:s3:::<bucket>/hr/*"]
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

## Bucket settings

- Private, with all four "Block public access" options on.
- Default encryption: SSE-S3 (`AES256`).
- Versioning on.
- Lifecycle, two rules:
  - `backups/`: current versions expire after 14 days.
  - Whole bucket: non-current versions expire after 7 days, expired object delete markers are
    removed (`ExpiredObjectDeleteMarker: true`), and incomplete multipart uploads are aborted
    after 1 day.

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
trap 'rm -rf "$TMP"' EXIT   # the copy is removed even when a command below fails
mkdir "$TMP/kb" "$TMP/hr"

# 1. Copy each volume out with a throwaway container; the volumes are mounted read-only.
docker run --rm --entrypoint cp -v csa-staging_staging-uploads:/src:ro -v "$TMP/kb":/dst \
  python:3.12.12-slim-bookworm -a /src/. /dst/
docker run --rm --entrypoint cp -v csa-staging_staging-hr-uploads:/src:ro -v "$TMP/hr":/dst \
  python:3.12.12-slim-bookworm -a /src/. /dst/
sudo chown -R "$(id -u):$(id -g)" "$TMP"

# 2. Upload; check each plan first with --dryrun.
aws s3 sync "$TMP/kb" s3://<bucket>/kb/ --profile <admin> --dryrun
aws s3 sync "$TMP/kb" s3://<bucket>/kb/ --profile <admin>
aws s3 sync "$TMP/hr" s3://<bucket>/hr/ --profile <admin> --dryrun
aws s3 sync "$TMP/hr" s3://<bucket>/hr/ --profile <admin>

# 3. Verify: object count and total bytes in the bucket must equal the local copy.
aws s3 ls s3://<bucket>/kb/ --recursive --summarize --profile <admin> | tail -2
find "$TMP/kb" -type f | wc -l
find "$TMP/kb" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}'
aws s3 ls s3://<bucket>/hr/ --recursive --summarize --profile <admin> | tail -2
find "$TMP/hr" -type f | wc -l
find "$TMP/hr" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}'

# 4. Remove the temporary copy (it holds real files); the trap does it again harmlessly.
rm -rf "$TMP"
```

Save the block as a script file and run it with `bash`, rather than pasting it into your login
shell, because `set -e` would end that shell on the first error.

Compare "Total Objects" with the first `find` count and "Total Size" with the byte total printed
after it, for each of kb and hr; they must be equal.

The temporary directory holds real HR files and needs enough free space for both volumes. The
`trap` removes it even if the run fails; if you ran the commands another way, run
`rm -rf "$TMP"` yourself before doing anything else.

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

It writes one small object per store, under a random tenant id in the `kb` and `hr` prefixes, and
checks the startup check, put and read, listing, streaming, delete, and reading a missing name, then
removes them. It prints one `PASS` or `FAIL` line per check and never prints keys or contents.
Exit code 0 means every check passed, 1 means a check failed, 2 means the settings are unusable.

Reading a missing object may answer `StorageNotFound` (S3 404) or `StorageError` (S3 403, which
a prefix-conditioned policy can return for a missing key). Both pass; the line says which one
happened.

## Behaviour notes

- A download whose object is missing or unreadable in the bucket answers 503, not 404.
- A transient S3 error during knowledge-base ingest is retried by the ingest job's normal retry.
- Deleted files stay recoverable for 7 days as non-current versions in the bucket (versioning
  plus the lifecycle rule); ask an admin to restore one. The smoke script's own deletes leave
  the same kind of non-current versions, which expire after 7 days, and the lifecycle then
  removes the leftover delete markers.
