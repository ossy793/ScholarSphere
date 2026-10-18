# Starts the Pritis Class Dues website (API + local pages) on http://localhost:8100.
# Run from anywhere:  & "C:\Users\hp\Desktop\OssyQuiz\pritis-payments\start-server.ps1"
$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "backend"
Set-Location $backend

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'pritis-payments.*uvicorn' }
if ($running) {
    Write-Host "The server is already running (process $($running.ProcessId -join ', ')). Close that window first, or stop it with:" -ForegroundColor Yellow
    Write-Host "  Stop-Process -Id $($running.ProcessId -join ',') -Force"
    exit 1
}

& (Join-Path $backend ".venv\Scripts\python.exe") -m uvicorn app.main:app --reload --port 8100
