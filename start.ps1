# Starts the Markets Learning Workbench and opens it in your browser.
# Right-click this file -> "Run with PowerShell" (or double-click start.bat).
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host ""
    Write-Host "The virtual environment (.venv) is missing." -ForegroundColor Yellow
    Write-Host "See README.md, section 'Setup', step 2."
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 1
}

& $python run.py --open
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Read-Host "The app stopped with an error (see above). Press Enter to close"
}
