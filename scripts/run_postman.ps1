# Прогон E2E Postman-коллекции через newman (этап 9).
# Exit-code newman'а = gate: 0 - зелёный, иначе падение.
#
# Использование:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_postman.ps1
#   powershell ... -BaseUrl "http://127.0.0.1:8000"     # мимо Traefik
#   powershell ... -IngestRun                            # + опциональная папка ingest-run
#                                                        #   (полный ingestion, CPU 10-30 мин!)
param(
    [string]$BaseUrl = "",
    [switch]$IngestRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$collection = Join-Path $root "tests\postman\graphrag.postman_collection.json"
$environment = Join-Path $root "tests\postman\env.local.json"

$resultsDir = Join-Path $root "scripts\load_test\results"
New-Item -ItemType Directory -Force -Path $resultsDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$jsonReport = Join-Path $resultsDir "newman-$stamp.json"

$args = @(
    "--yes", "newman", "run", $collection,
    "-e", $environment,
    "--timeout-request", "180000",
    "--delay-request", "100",
    "--reporters", "cli,json",
    "--reporter-json-export", $jsonReport
)
if ($BaseUrl -ne "") { $args += @("--env-var", "baseURL=$BaseUrl") }
if ($IngestRun) { $args += @("--folder", "ingest-run (ОПЦИЯ: полный прогон, в gate не входит)") }

Write-Host "== newman run: $collection"
& npx @args
$code = $LASTEXITCODE

if ($code -eq 0) {
    Write-Host "== GATE GREEN (newman exit 0); отчёт: $jsonReport"
} else {
    Write-Host "== GATE RED (newman exit $code)"
}
exit $code
