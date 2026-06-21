# run.ps1
# Runs the main python script inside the virtual environment

$scriptPath = Join-Path $PSScriptRoot "tagpup.py"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "Virtual environment not found. Please run setup.bat / setup.ps1 first." -ForegroundColor Red
    Exit 1
}

# Run the python script forwarding all arguments
& $venvPython $scriptPath $args
