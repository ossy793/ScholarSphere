# Starts the Pritis Class Dues background worker (emails, Paystack checks, nightly check).
# Run from anywhere:  & "C:\Users\hp\Desktop\OssyQuiz\pritis-payments\start-worker.ps1"
$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "backend"
Set-Location $backend

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'app\.worker' }
if ($running) {
    Write-Host "The worker is already running (process $($running.ProcessId -join ', ')). Close that window first, or stop it with:" -ForegroundColor Yellow
    Write-Host "  Stop-Process -Id $($running.ProcessId -join ',') -Force"
    exit 1
}

& (Join-Path $backend ".venv\Scripts\python.exe") -m app.worker
