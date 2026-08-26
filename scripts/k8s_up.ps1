# Полный запуск GraphRAG в minikube: кластер -> helm upgrade --install -> port-forward.
# Секреты обязательны: infra/helm/graphrag/secrets.yaml (шаблон secrets.yaml.example).
# Использование: powershell scripts/k8s_up.ps1 [-SkipInstall]
param(
    [switch]$SkipInstall   # только кластер+port-forward, без helm
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$chart = Join-Path $root "infra\helm\graphrag"
$secrets = Join-Path $chart "secrets.yaml"

if (-not (Test-Path $secrets)) {
    Write-Host "НЕТ $secrets" -ForegroundColor Red
    Write-Host "Скопируй и заполни шаблон: Copy-Item secrets.yaml.example secrets.yaml"
    exit 1
}

# 1) Кластер: start идемпотентен - поднимает остановленный, подтверждает работающий
"==> minikube start (существующий профиль)"
minikube -p minikube start | Out-Null
if ($LASTEXITCODE -ne 0) { throw "minikube start failed" }
kubectl get nodes --no-headers

# 2) Релиз
if (-not $SkipInstall) {
    "==> helm upgrade --install graphrag"
    helm upgrade graphrag $chart -n graphrag --create-namespace `
        -f (Join-Path $chart "values.yaml") -f $secrets | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "helm upgrade failed" }
    "Ожидание деплоев (neo4j стартует до ~90с)..."
    kubectl -n graphrag wait --for=condition=Available deploy --all --timeout=300s
}

# 3) Port-forward ingress -> localhost:8080 (фоновый процесс)
$existing = Get-CimInstance Win32_Process -Filter "Name='kubectl.exe'" |
    Where-Object { $_.CommandLine -match "port-forward.*8080" }
if (-not $existing) {
    $p = Start-Process kubectl -ArgumentList '-n','ingress-nginx','port-forward',
        'svc/ingress-nginx-controller','8080:80' -WindowStyle Hidden -PassThru
    Start-Sleep 3
    "==> port-forward запущен (PID $($p.Id))"
} else {
    "==> port-forward уже работает (PID $($existing.ProcessId))"
}

# 4) Итог
& (Join-Path $PSScriptRoot "k8s_status.ps1")
""
"UI:            http://localhost:8080/"
"Swagger:       http://localhost:8080/docs"
"Newman gate:   powershell scripts/run_postman.ps1 -BaseUrl http://127.0.0.1:8080"
