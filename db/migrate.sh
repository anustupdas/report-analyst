#!/usr/bin/env bash
# Apply pending SQL migrations under db/migrations/ in lexical order.
# Prefer running inside the Postgres container when Docker is available.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIGRATIONS_DIR="${ROOT_DIR}/db/migrations"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "${ROOT_DIR}/.env"
  set +a
fi

POSTGRES_USER="${POSTGRES_USER:-chat}"
POSTGRES_DB="${POSTGRES_DB:-chat_project}"
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-chat}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-postgres}"

export PGPASSWORD="${POSTGRES_PASSWORD}"

have_docker_compose() {
  if command -v docker >/dev/null 2>&1; then
    if docker compose version >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

container_running() {
  have_docker_compose || return 1
  docker compose -f "${ROOT_DIR}/docker-compose.yml" ps --status running --services 2>/dev/null \
    | grep -qx "${COMPOSE_SERVICE}"
}

run_sql() {
  local sql="$1"
  if container_running; then
    docker compose -f "${ROOT_DIR}/docker-compose.yml" exec -T "${COMPOSE_SERVICE}" \
      psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "${sql}"
  else
    psql -v ON_ERROR_STOP=1 \
      -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" \
      -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
      -c "${sql}"
  fi
}

run_sql_quiet() {
  local sql="$1"
  if container_running; then
    docker compose -f "${ROOT_DIR}/docker-compose.yml" exec -T "${COMPOSE_SERVICE}" \
      psql -v ON_ERROR_STOP=1 -tA -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "${sql}"
  else
    psql -v ON_ERROR_STOP=1 -tA \
      -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" \
      -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
      -c "${sql}"
  fi
}

run_sql_file() {
  local file="$1"
  if container_running; then
    docker compose -f "${ROOT_DIR}/docker-compose.yml" exec -T "${COMPOSE_SERVICE}" \
      psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" < "${file}"
  else
    psql -v ON_ERROR_STOP=1 \
      -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" \
      -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
      -f "${file}"
  fi
}

wait_for_db() {
  local attempts=40
  local i
  for ((i = 1; i <= attempts; i++)); do
    if run_sql "SELECT 1;" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "ERROR: Postgres is not ready after ${attempts}s" >&2
  echo "  Start it with: make db-up" >&2
  exit 1
}

echo "Waiting for Postgres..."
wait_for_db

# Ensure tracking table exists even before 001 is recorded.
run_sql "CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);" >/dev/null

shopt -s nullglob
files=("${MIGRATIONS_DIR}"/*.sql)
if ((${#files[@]} == 0)); then
  echo "No migrations found in ${MIGRATIONS_DIR}"
  exit 1
fi

applied=0
skipped=0

for file in "${files[@]}"; do
  version="$(basename "${file}")"
  exists="$(run_sql_quiet "SELECT 1 FROM schema_migrations WHERE version = '${version}';" || true)"

  if [[ "${exists}" == "1" ]]; then
    echo "skip  ${version}"
    skipped=$((skipped + 1))
    continue
  fi

  echo "apply ${version}"
  run_sql_file "${file}"
  run_sql_quiet "INSERT INTO schema_migrations (version) VALUES ('${version}');" >/dev/null
  applied=$((applied + 1))
done

echo "Done. applied=${applied} skipped=${skipped}"

echo
echo "Tables:"
run_sql "\dt"
