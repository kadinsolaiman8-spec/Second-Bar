$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "Trading stack web UI — http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host "Leave this window open. Press Ctrl+C to stop." -ForegroundColor Gray
Write-Host ""

python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
