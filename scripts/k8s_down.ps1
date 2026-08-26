# Остановка GraphRAG в minikube: глушит port-forward/tunnel, опционально minikube stop.
# Использование:
#   powershell scripts/k8s_down.ps1              # только port-forward (кластер работает)
#   powershell scripts/k8s_down.ps1 -StopCluster # + minikube stop (освободить RAM хоста)
#   powershell scripts/k8s_down.ps1 -Profile myprofile
param(
    [string]$Profile = "graphrag",
    [switch]$StopCluster
)
$ErrorActionPreference = "Continue"

# 1) Остановить port-forward
$pfs = Get-CimInstance Win32_Process -Filter "Name='kubectl.exe'" |
    Where-Object { $_.CommandLine -match "port-forward" }
foreach ($p in $pfs) {
    Stop-Process -Id $p.ProcessId -Force
    "port-forward остановлен (PID $($p.ProcessId))"
}
if (-not $pfs) { "port-forward не был запущен" }

# 2) Остановить tunnel (если запущен)
$tunnels = Get-CimInstance Win32_Process -Filter "Name='minikube.exe'" |
    Where-Object { $_.CommandLine -match "tunnel" }
foreach ($t in $tunnels) {
    Stop-Process -Id $t.ProcessId -Force
    "tunnel остановлен (PID $($t.ProcessId))"
}
if (-not $tunnels) { "tunnel не был запущен" }

# 3) Остановить кластер (опционально)
if ($StopCluster) {
    "==> minikube stop (профиль $Profile, данные PVC сохраняются)"
    minikube -p $Profile stop | Select-Object -Last 2
} else {
    "Кластер оставлен работать. Полная остановка: scripts/k8s_down.ps1 -StopCluster"
}
