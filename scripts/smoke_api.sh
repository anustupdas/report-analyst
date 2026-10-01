#!/usr/bin/env bash
# Minimal API smoke test (requires make run).
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
EMAIL="smoke-$(date +%s)@example.com"

echo "== health =="
curl -sf "${API_URL}/api/v1/health" | tee /tmp/chat_api_health.json
echo

echo "== create user =="
USER_JSON=$(curl -sf "${API_URL}/api/v1/users" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"displayName\":\"Smoke Tester\"}")
echo "${USER_JSON}" | tee /tmp/chat_api_user.json
TOKEN=$(python3 -c "import json,sys; print(json.load(sys.stdin)['data']['apiToken'])" <<<"${USER_JSON}")
AUTH="Authorization: Bearer ${TOKEN}"

echo "== create project =="
PROJ_JSON=$(curl -sf "${API_URL}/api/v1/projects" -H "${AUTH}" \
  -H 'Content-Type: application/json' -d '{"name":"Smoke Project"}')
echo "${PROJ_JSON}"
PROJECT=$(python3 -c "import json,sys; print(json.load(sys.stdin)['data']['id'])" <<<"${PROJ_JSON}")

echo "== prepare document =="
DOC_JSON=$(curl -sf "${API_URL}/api/v1/projects/${PROJECT}/documents/prepare" \
  -H "${AUTH}" -H 'Content-Type: application/json' \
  -d '{"filename":"note.txt","title":"Smoke note"}')
echo "${DOC_JSON}"
DOC=$(python3 -c "import json,sys; print(json.load(sys.stdin)['data']['id'])" <<<"${DOC_JSON}")

TMP_FILE=$(mktemp /tmp/chat_api_smoke.XXXXXX.txt)
echo "Shell spent illustrative amounts on climate adaptation. FTE was 1000." > "${TMP_FILE}"

echo "== upload content =="
curl -sf -X PUT "${API_URL}/api/v1/projects/${PROJECT}/documents/${DOC}/content" \
  -H "${AUTH}" -F "file=@${TMP_FILE};filename=note.txt"
echo

echo "== process =="
curl -sf -X POST "${API_URL}/api/v1/projects/${PROJECT}/documents/${DOC}/process" \
  -H "${AUTH}"
echo

echo "== poll status =="
for i in $(seq 1 30); do
  STATUS_JSON=$(curl -sf "${API_URL}/api/v1/projects/${PROJECT}/documents/${DOC}" -H "${AUTH}")
  STATUS=$(python3 -c "import json,sys; print(json.load(sys.stdin)['data']['processStatus'])" <<<"${STATUS_JSON}")
  echo "  attempt ${i}: ${STATUS}"
  if [[ "${STATUS}" == "completed" || "${STATUS}" == "ready" || "${STATUS}" == "failed" ]]; then
    echo "${STATUS_JSON}"
    break
  fi
  sleep 1
done

rm -f "${TMP_FILE}"
echo "Smoke finished."
