# Полный запуск GraphRAG в minikube: кластер -> helm upgrade --install -> port-forward.
# Секреты обязательны: infra/helm/graphrag/secrets.yaml (шаблон secrets.yaml.example).
# Использование:
#   powershell scripts/k8s_up.ps1                          # профиль graphrag, helm + port-forward
#   powershell scripts/k8s_up.ps1 -SkipInstall             # только кластер + port-forward
#   powershell scripts/k8s_up.ps1 -Tunnel                  # + minikube tunnel (доменный режим)
#   powershell scripts/k8s_up.ps1 -Profile myprofile       # другой профиль
param(
    [string]$Profile = "graphrag",
    [switch]$SkipInstall,
    [switch]$Tunnel
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$chart = Join-Path $root "infra\helm\graphrag"
$secretsPath = Join-Path $chart "secrets.yaml"

if (-not (Test-Path $secretsPath)) {
    Write-Host "НЕТ $secretsPath" -ForegroundColor Red
    Write-Host "Скопируй и заполни шаблон: Copy-Item secrets.yaml.example secrets.yaml"
    exit 1
}

# 1) Кластер: start идемпотентен - поднимает остановленный, подтверждает работающий
"==> minikube start (профиль $Profile)"
minikube -p $Profile start | Out-Null
if ($LASTEXITCODE -ne 0) { throw "minikube start failed" }
kubectl get nodes --no-headers

# Ждём готовности ingress-nginx (webhook нужен для helm upgrade)
"==> ожидание ingress-nginx controller"
kubectl -n ingress-nginx wait --for=condition=Available deploy/ingress-nginx-controller --timeout=120s 2>$null
# Webhook endpoint может быть не готов сразу после Available - ждём явно
$webhookReady = $false
for ($i = 1; $i -le 12; $i++) {
    $ep = kubectl -n ingress-nginx get endpoints ingress-nginx-controller-admission -o jsonpath='{.subsets[0].addresses[0].ip}' 2>$null
    if ($ep) { $webhookReady = $true; break }
    "webhook endpoint ещё не готов ($i/12), ждём 5с..."
    Start-Sleep 5
}
if (-not $webhookReady) { Write-Host "WARN: webhook endpoint не найден, helm upgrade может упасть" -ForegroundColor Yellow }

# 2) Релиз
if (-not $SkipInstall) {
    "==> helm upgrade --install graphrag"
    $helmOk = $false
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        helm upgrade graphrag $chart -n graphrag --create-namespace `
            -f (Join-Path $chart "values.yaml") -f $secretsPath 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $helmOk = $true; break }
        "helm upgrade попытка $attempt/5 не удалась (webhook ещё не готов?), ждём 15с..."
        Start-Sleep 15
    }
    if (-not $helmOk) { throw "helm upgrade failed после 5 попыток" }

    "Ожидание деплоев (neo4j стартует до ~90с)..."
    kubectl -n graphrag wait --for=condition=Available deploy --all --timeout=300s

    # Vault dev-mode хранит секреты в памяти - при рестарте ноды они теряются.
    # Читаем секреты из secrets.yaml и записываем в Vault напрямую через kubectl exec.
    "==> запись секретов в Vault (dev-mode in-memory)"
    $sec = Get-Content $secretsPath -Raw
    # Парсим YAML-значения (простой парсинг ключей secrets:)
    $jwtSecret   = if ($sec -match 'jwtSecret:\s*(.+)')   { $Matches[1].Trim() } else { "dev_jwt_secret_change_me_0123456789" }
    $lfPublic    = if ($sec -match 'langfusePublicKey:\s*(.+)') { $Matches[1].Trim() } else { "pk-lf-graphrag-dev" }
    $lfSecret    = if ($sec -match 'langfuseSecretKey:\s*(.+)') { $Matches[1].Trim() } else { "sk-lf-graphrag-dev-local" }
    $neo4jPass   = if ($sec -match 'neo4jPassword:\s*(.+)') { $Matches[1].Trim() } else { "dev_neo4j_password" }
    $pgPass      = if ($sec -match 'postgresPassword:\s*(.+)') { $Matches[1].Trim() } else { "dev_pg_password" }
    $vaultToken  = if ($sec -match 'vaultRootToken:\s*(.+)') { $Matches[1].Trim() } else { "dev_root_token" }

    # Ждём пока Vault станет готов
    $vaultReady = $false
    for ($i = 1; $i -le 12; $i++) {
        $code = kubectl -n graphrag exec deploy/vault -- sh -c "VAULT_ADDR=http://127.0.0.1:8200 vault status -format=json" 2>$null
        if ($LASTEXITCODE -eq 0) { $vaultReady = $true; break }
        "Vault ещё не готов ($i/12), ждём 5с..."
        Start-Sleep 5
    }
    if (-not $vaultReady) { throw "Vault не стартовал за 60с" }

    # Записываем секреты в Vault KV v2
    kubectl -n graphrag exec deploy/vault -- sh -c @"
VAULT_ADDR=http://127.0.0.1:8200
export VAULT_ADDR
vault kv put secret/graphrag/app \
  APP_JWT_SECRET='$jwtSecret' \
  APP_LANGFUSE_PUBLIC_KEY='$lfPublic' \
  APP_LANGFUSE_SECRET_KEY='$lfSecret' \
  APP_NEO4J_PASSWORD='$neo4jPass' \
  APP_DATABASE_URL='postgresql+asyncpg://graphrag:$pgPass@postgres:5432/graphrag' \
  APP_QDRANT_URL='http://qdrant:6333' \
  APP_NEO4J_URI='bolt://neo4j:7687' \
  APP_NEO4J_USER='neo4j' \
  APP_LLM_MODEL='qwen3.5-2b' \
  APP_LLM_BASE_URL='http://host.minikube.internal:1234/v1' \
  APP_GUARDRAIL_LLM_MODEL='qwen3.5-2b' \
  APP_GUARDRAIL_LLM_BASE_URL='http://host.minikube.internal:1234/v1' \
  APP_LANGFUSE_HOST='http://host.minikube.internal:3300' \
  APP_OPENAI_API_KEY='sk-no-key'
"@ 2>&1 | Out-Null
    "секреты записаны в Vault"

    # Перезапускаем backend, чтобы initContainer подхватил свежие секреты
    "==> rollout restart backend (свежие секреты из Vault)"
    kubectl -n graphrag rollout restart deploy/backend
    Start-Sleep 10
    kubectl -n graphrag wait --for=condition=Available deploy/backend --timeout=120s
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

# 4) Tunnel (доменный режим, опционально)
if ($Tunnel) {
    $existingTunnel = Get-CimInstance Win32_Process -Filter "Name='minikube.exe'" |
        Where-Object { $_.CommandLine -match "tunnel" }
    if (-not $existingTunnel) {
        "==> minikube tunnel (требует UAC один раз)"
        Start-Process minikube -ArgumentList '-p',$Profile,'tunnel' -WindowStyle Hidden
        Start-Sleep 5
    } else {
        "==> tunnel уже работает (PID $($existingTunnel.ProcessId))"
    }
}

# 5) Итог
& (Join-Path $PSScriptRoot "k8s_status.ps1") -Profile $Profile
""
"UI:            http://localhost:8080/   (Swagger: /docs)"
"Доменный режим: scripts/k8s_up.ps1 -Tunnel + hosts entries"
"                (см. docs/minikube-deployment.md, раздел 5.1)"
"Newman gate:   powershell scripts/run_postman.ps1 -BaseUrl http://127.0.0.1:8080"
