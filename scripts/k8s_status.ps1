# Статус GraphRAG-стека в minikube (этап 10).
# Использование:
#   powershell scripts/k8s_status.ps1
#   powershell scripts/k8s_status.ps1 -Profile myprofile
param(
    [string]$Profile = "graphrag"
)
$ErrorActionPreference = "Continue"

"=== Кластер (профиль $Profile) ==="
minikube -p $Profile status 2>$null | Select-String "host:|kubelet:|apiserver:" | ForEach-Object { $_.Line.Trim() }
kubectl get nodes --no-headers

"`n=== Поды/Jobs (namespace graphrag) ==="
kubectl -n graphrag get pods,jobs --no-headers

"`n=== Ресурсы подов ==="
kubectl top pods -n graphrag --sort-by=memory 2>$null
kubectl top nodes 2>$null

"`n=== Входная точка (port-forward 8080 -> ingress-nginx) ==="
$pf = Get-CimInstance Win32_Process -Filter "Name='kubectl.exe'" |
    Where-Object { $_.CommandLine -match "port-forward.*8080" }
if ($pf) {
    "process: PID $($pf.ProcessId)"
} else {
    "НЕ запущен. Старт: scripts/k8s_up.ps1 или вручную:"
    "  Start-Process kubectl -ArgumentList '-n','ingress-nginx','port-forward','svc/ingress-nginx-controller','8080:80' -WindowStyle Hidden"
}

try {
    $code = (curl.exe -s --noproxy "*" -o NUL -w "%{http_code}" http://127.0.0.1:8080/health)
    "health http://127.0.0.1:8080/health -> HTTP $code"
    if ($code -ne "200") { "  (если PF только что стартовал - повторите через пару секунд)" }
} catch {
    "health: недоступен"
}
