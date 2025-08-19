#!/usr/bin/env bash
set -euo pipefail

# Batch fan-out smoke test. Requires API + Celery + Redis running.
BASE_URL=${BASE_URL:-http://localhost:8000}

echo "[1/3] Generating dummy PDFs..."
python scripts/generate_dummy_pdfs.py

echo "[2/3] Submitting batch job (jokbo-centric) to ${BASE_URL} ..."
RESP_JSON=$(curl -sS -f \
  -F "jokbo_files=@jokbo/sample.pdf" \
  -F "lesson_files=@lesson/sample.pdf" \
  "${BASE_URL}/analyze/batch?mode=jokbo-centric&model=flash")

echo "Response: ${RESP_JSON}"

JOB_ID=$(python - <<'PY'
import json,sys
data=json.loads(sys.stdin.read())
print(data.get('job_id',''))
PY
<<<"${RESP_JSON}")

TASK_ID=$(python - <<'PY'
import json,sys
data=json.loads(sys.stdin.read())
print(data.get('task_id',''))
PY
<<<"${RESP_JSON}")

if [[ -z "$JOB_ID" || -z "$TASK_ID" ]]; then
  echo "Failed to parse job_id/task_id from response" >&2
  exit 1
fi

echo "[3/3] Polling task status and fetching results..."
python scripts/poll_status.py --base "$BASE_URL" --task "$TASK_ID" --job "$JOB_ID" --timeout 300 --download

echo "Batch smoke test completed."

