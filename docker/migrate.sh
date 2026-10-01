#!/bin/sh
# Apply db/migrations inside the Compose network. Idempotent.
set -eu

PGHOST="${PGHOST:-postgres}"
PGUSER="${POSTGRES_USER:-chat}"
PGDATABASE="${POSTGRES_DB:-chat_project}"
export PGPASSWORD="${POSTGRES_PASSWORD:-chat}"

echo "Waiting for Postgres at ${PGHOST}..."
i=0
while [ "$i" -lt 60 ]; do
  if pg_isready -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 1
done
pg_isready -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE"

psql -v ON_ERROR_STOP=1 -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c \
  "CREATE TABLE IF NOT EXISTS schema_migrations (
     version text PRIMARY KEY,
     applied_at timestamptz NOT NULL DEFAULT now()
   );"

applied=0
skipped=0
for file in /migrations/*.sql; do
  version=$(basename "$file")
  exists=$(psql -v ON_ERROR_STOP=1 -tA -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c \
    "SELECT 1 FROM schema_migrations WHERE version = '${version}';")
  if [ "$exists" = "1" ]; then
    echo "skip  ${version}"
    skipped=$((skipped + 1))
    continue
  fi
  echo "apply ${version}"
  psql -v ON_ERROR_STOP=1 -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -f "$file"
  psql -v ON_ERROR_STOP=1 -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c \
    "INSERT INTO schema_migrations (version) VALUES ('${version}');" >/dev/null
  applied=$((applied + 1))
done

echo "Done. applied=${applied} skipped=${skipped}"
