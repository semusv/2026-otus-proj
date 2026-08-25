# Нагрузочный прогон locust (этап 9). Воспроизводим: фиксированный run-time,
# headless, CSV/HTML в scripts/load_test/results/.
#
# Использование:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_load_test.ps1 `
#       -Profile both -BaseUrl "http://api.localhost"
#   Профили: health | chat | both (по умолчанию both, последовательно).
param(
    [ValidateSet("health", "chat", "both")]
    [string]$Profile = "both",
    [string]$BaseUrl = "http://api.localhost",
    [int]$HealthUsers = 25,
    [int]$ChatUsers = 4
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$locustfile = Join-Path $PSScriptRoot "locustfile.py"
$LOCUST_VERSION = "2.32.4"

$resultsRoot = Join-Path $PSScriptRoot "results"
New-Item -ItemType Directory -Force -Path $resultsRoot | Out-Null

function Invoke-DockerStats([string]$Path) {
    docker stats --no-stream --format "{{.Name}}`tCPU={{.CPUPerc}}`tMEM={{.MemUsage}}" |
        Select-String graphrag | ForEach-Object { $_.Line } | Set-Content -Path $Path -Encoding UTF8
}

function Invoke-Profile([string]$Name, [int]$Users, [double]$SpawnRate, [int]$RunSeconds) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $outDir = Join-Path $resultsRoot "load-$stamp-$Name"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $csvPrefix = Join-Path $outDir "stats"
    $htmlReport = Join-Path $outDir "report.html"

    Write-Host "== [$Name] users=$Users spawn=$SpawnRate/s runtime=${RunSeconds}s host=$BaseUrl"
    Invoke-DockerStats (Join-Path $outDir "docker-stats-before.txt")

    $env:LOCUST_PROFILE = $Name
    $proc = Start-Process -FilePath "uv" -ArgumentList @(
        "run", "--no-project", "--with", "locust==$LOCUST_VERSION",
        "python", "-m", "locust",
        "-f", $locustfile,
        "--headless",
        "--host", $BaseUrl,
        "-u", "$Users", "-r", "$SpawnRate",
        "--run-time", "${RunSeconds}s",
        "--only-summary", "--csv", $csvPrefix, "--html", $htmlReport
    ) -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $outDir "locust-out.log") -RedirectStandardError (Join-Path $outDir "locust-err.log")

    # снапшот ресурсов посреди прогона (пиковая нагрузка)
    Start-Sleep -Seconds ([int]($RunSeconds / 2))
    Invoke-DockerStats (Join-Path $outDir "docker-stats-mid.txt")

    Wait-Process -Id $proc.Id -Timeout ($RunSeconds + 180) -ErrorAction SilentlyContinue
    if (-not $proc.HasExited) {
        Write-Host "== locust не завершился сам - убиваю (защита от зависания)"
        Stop-Process -Id $proc.Id -Force
    }
    Invoke-DockerStats (Join-Path $outDir "docker-stats-after.txt")
    Write-Host "== [$Name] готово: $outDir (exit=$($proc.ExitCode))"
}

if ($Profile -in @("health", "both")) {
    Invoke-Profile "health" $HealthUsers 5 60
}
if ($Profile -in @("chat", "both")) {
    Invoke-Profile "chat" $ChatUsers 1 180
}
Write-Host "== нагрузочные прогоны завершены"
