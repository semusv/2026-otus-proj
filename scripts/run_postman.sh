#!/usr/bin/env sh
# Прогон E2E Postman-коллекции через newman (этап 9).
# Exit-code newman'а = gate: 0 - зелёный, иначе падение.
#
# Использование:
#   sh scripts/run_postman.sh
#   sh scripts/run_postman.sh http://127.0.0.1:8000    # мимо Traefik
#   INGEST_RUN=1 sh scripts/run_postman.sh             # + опциональная папка ingest-run
#                                                      #   (полный ingestion, CPU 10-30 мин!)
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COLLECTION="$ROOT/tests/postman/graphrag.postman_collection.json"
ENVIRONMENT="$ROOT/tests/postman/env.local.json"

RESULTS_DIR="$ROOT/scripts/load_test/results"
mkdir -p "$RESULTS_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
JSON_REPORT="$RESULTS_DIR/newman-$STAMP.json"

set -- \
    --timeout-request 180000 \
    --delay-request 100 \
    --reporters cli,json \
    --reporter-json-export "$JSON_REPORT"

if [ -n "${1:-}" ]; then
    set -- "$@" --env-var "baseURL=$1"
fi
if [ "${INGEST_RUN:-0}" = "1" ]; then
    set -- "$@" --folder "ingest-run (ОПЦИЯ: полный прогон, в gate не входит)"
fi

echo "== newman run: $COLLECTION"
npx --yes newman run "$COLLECTION" -e "$ENVIRONMENT" "$@"
