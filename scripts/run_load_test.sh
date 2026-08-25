#!/usr/bin/env sh
# Нагрузочный прогон locust (этап 9). Воспроизводим: фиксированный run-time,
# headless, CSV/HTML в scripts/load_test/results/.
#
# Использование:
#   sh scripts/run_load_test.sh [health|chat|both] [baseUrl]
#   Переменные: HEALTH_USERS=25 CHAT_USERS=4 LOCUST_VERSION=2.32.4
set -eu
PROFILE="${1:-both}"
BASE_URL="${2:-http://api.localhost}"
HEALTH_USERS="${HEALTH_USERS:-25}"
CHAT_USERS="${CHAT_USERS:-4}"
LOCUST_VERSION="${LOCUST_VERSION:-2.32.4}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCUSTFILE="$ROOT/scripts/load_test/locustfile.py"
RESULTS_ROOT="$ROOT/scripts/load_test/results"
mkdir -p "$RESULTS_ROOT"

docker_stats() {
    docker stats --no-stream --format '{{.Name}}\tCPU={{.CPUPerc}}\tMEM={{.MemUsage}}' \
        | grep graphrag > "$1" || true
}

run_profile() {
    name="$1"; users="$2"; spawn="$3"; seconds="$4"
    stamp="$(date +%Y%m%d-%H%M%S)"
    out_dir="$RESULTS_ROOT/load-$stamp-$name"
    mkdir -p "$out_dir"

    echo "== [$name] users=$users spawn=$spawn/s runtime=${seconds}s host=$BASE_URL"
    docker_stats "$out_dir/docker-stats-before.txt"

    LOCUST_PROFILE="$name" uv run --no-project --with "locust==$LOCUST_VERSION" \
        python -m locust -f "$LOCUSTFILE" --headless \
        --host "$BASE_URL" -u "$users" -r "$spawn" \
        --run-time "${seconds}s" --only-summary \
        --csv "$out_dir/stats" --html "$out_dir/report.html" \
        > "$out_dir/locust-out.log" 2> "$out_dir/locust-err.log" &
    pid=$!

    sleep $((seconds / 2))
    docker_stats "$out_dir/docker-stats-mid.txt"

    wait $pid || true
    docker_stats "$out_dir/docker-stats-after.txt"
    echo "== [$name] готово: $out_dir"
}

case "$PROFILE" in
    health) run_profile health "$HEALTH_USERS" 5 60 ;;
    chat)   run_profile chat "$CHAT_USERS" 1 180 ;;
    both)   run_profile health "$HEALTH_USERS" 5 60; run_profile chat "$CHAT_USERS" 1 180 ;;
    *) echo "unknown profile: $PROFILE (health|chat|both)" >&2; exit 2 ;;
esac
echo "== нагрузочные прогоны завершены"
