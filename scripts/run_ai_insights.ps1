Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Create it with: py -3.11 -m venv .venv"
}

& ".\.venv\Scripts\python.exe" -m ai_insights.threat_insights --limit 100

