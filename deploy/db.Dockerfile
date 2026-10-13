# Combined PostGIS + pgvector image for CSA (LG01 §9.2).
#
# The schema baseline runs BOTH `CREATE EXTENSION postgis` and
# `CREATE EXTENSION vector`, but the stock pgvector image ships pgvector only.
# This image inherits pgvector from the base and adds PostGIS 3 from PGDG so a
# clean DB can apply db/schema_baseline.sql. Dev (compose.dev.yml), CI, and the
# testcontainers fixtures all use this one image so the extension set cannot
# diverge between "works on my machine" and "green in CI". FEAT-004 pins it by
# digest for compose and CI; this feature just needs it buildable.
FROM pgvector/pgvector:pg16

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      postgresql-16-postgis-3 \
      postgresql-16-postgis-3-scripts \
 && rm -rf /var/lib/apt/lists/*
