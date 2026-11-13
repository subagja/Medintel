
# MEDINTEL - Worker Launcher
$backend = $PSScriptRoot
$python = Join-Path $backend "..\venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Python virtual environment tidak ditemukan."
}

# Worker crawling
Start-Process powershell.exe `
    -WorkingDirectory $backend `
    -ArgumentList "-NoExit", "-Command", "& '$python' manage.py run_collection_worker"

# Worker klasifikasi dan ekstraksi terpadu
Start-Process powershell.exe `
    -WorkingDirectory $backend `
    -ArgumentList "-NoExit", "-Command", "& '$python' manage.py run_integrated_processing_worker --interval 5"

Write-Host "Worker MEDINTEL berhasil dijalankan."
