# Остановка GraphRAG в minikube: глушит port-forward, опционально minikube stop.
# Использование:
#   powershell scripts/k8s_down.ps1              # только port-forward (кластер работает)
#   powershell scripts/k8s_down.ps1 -StopCluster # + minikube stop (освободить RAM хоста)
param(
    [switch]$StopCluster
)
$ErrorActionPreference = "Continue"

$pfs = Get-CimInstance Win32_Process -Filter "Name='kubectl.exe'" |
    Where-Object { $_.CommandLine -match "port-forward" }
foreach ($p in $pfs) {
    Stop-Process -Id $p.ProcessId -Force
    "port-forward остановлен (PID $($p.ProcessId))"
}
if (-not $pfs) { "port-forward не был запущен" }

if ($StopCluster) {
    "==> minikube stop (данные PVC сохраняются)"
    minikube -p minikube stop | Select-Object -Last 2
} else {
    "Кластер оставлен работать. Полная остановка: scripts/k8s_down.ps1 -StopCluster"
}
