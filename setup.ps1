# setup.ps1
# Setup script for TagPup

Write-Host "Setting up Python virtual environment..." -ForegroundColor Cyan

# Create venv if not exists
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Host "Virtual environment created." -ForegroundColor Green
} else {
    Write-Host "Virtual environment already exists." -ForegroundColor Yellow
}

# Active and upgrade pip
Write-Host "Upgrading pip and installing dependencies..." -ForegroundColor Cyan
& ".\.venv\Scripts\pip.exe" install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt

# Create necessary directories
$dataDir = "data"

if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
    Write-Host "Created directory: $dataDir" -ForegroundColor Green
}

# There is no settings file to make: each library holds its own settings, and a new
# library is made with the defaults (tagpup.services.settings).

Write-Host "Setup complete!" -ForegroundColor Green
