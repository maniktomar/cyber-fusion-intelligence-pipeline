<#
.SYNOPSIS
    One entry point for every portfolio project.

.DESCRIPTION
    Each project has its own virtual environment, dependencies, and start command. This
    script creates the venv and installs requirements on first run, then starts the project,
    so none of that has to be remembered per project.

.EXAMPLE
    .\start.ps1 -List
    .\start.ps1 docushield
    .\start.ps1 ireland -Demo
    .\start.ps1 agentops -Install
#>

param(
    [Parameter(Position = 0)]
    [string]$Project,

    # Create the venv and install requirements without starting anything.
    [switch]$Install,

    # Run the project's test suite instead of starting it.
    [switch]$Test,

    # Ireland only: run the full data pipeline before serving the dashboard.
    [switch]$Demo,

    [switch]$List
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

# name -> configuration. `Venv` is relative to the project directory; `Port` is informational.
$Projects = [ordered]@{
    "cyber-fusion" = @{
        Dir     = "."
        Venv    = ".venv"
        Req     = "requirements.txt"
        Port    = 8000
        Start   = { param($py) & $py -m uvicorn api.metrics_api:app --host 0.0.0.0 --port 8000 }
        Test    = { param($py) & $py -m pytest tests -q }
        Notes   = "Needs Kafka for the streaming job: .\scripts\start_kafka.ps1"
    }
    "agentops" = @{
        Dir   = "agentops-command-center"
        Venv  = ".venv"
        Req   = "requirements.txt"
        Port  = 8000
        Start = { param($py) & $py -m uvicorn app.main:app --host 0.0.0.0 --port 8000 }
        Test  = { param($py) & $py -m pytest -q }
        Notes = "Runs in deterministic demo mode with no API keys set."
    }
    "iceberg" = @{
        Dir   = "iceberg-lakehouse-platform"
        Venv  = ".venv"
        Req   = "requirements.txt"
        Port  = 8501
        Start = { param($py) & $py -m streamlit run streamlit_app/app.py }
        Test  = { param($py) & $py -m pytest tests -q }
        Notes = "Start the lakehouse stack first: .\scripts\start.ps1 (Docker required)"
    }
    "ireland" = @{
        Dir   = "ireland-energy-ai-pipeline"
        Venv  = ".venv"
        Req   = "requirements.txt"
        Port  = 8501
        Start = { param($py) Push-Location frontend; try { & $py -m http.server 8501 } finally { Pop-Location } }
        Test  = { param($py) & $py -m pytest -q }
        Notes = "Use -Demo to refresh from the live EirGrid feed before serving."
    }
    "docushield" = @{
        Dir   = "docushield"
        Venv  = ".venv"
        Req   = "requirements.txt"
        Port  = 8000
        Start = { param($py) & $py -m uvicorn app.main:app --host 0.0.0.0 --port 8000 }
        Test  = { param($py) & $py -m pytest -q }
        Notes = "Train first if models/ is empty: python -m app.synthetic.generate; python -m app.ml.train"
    }
    "emars" = @{
        Dir   = "emars/backend"
        Venv  = ".venv"
        Req   = "requirements.txt"
        Port  = 8080
        Start = { param($py) & $py -m uvicorn app.main:app --host 0.0.0.0 --port 8080 }
        Test  = { param($py) & $py -m pytest -q }
        Notes = "Frontend is separate: cd emars\frontend; npm install; npm run dev (port 3000)"
    }
    "raglens" = @{
        Dir   = "raglens/backend"
        Venv  = ".venv"
        Req   = "requirements.txt"
        Port  = 8000
        Start = { param($py) & $py -m uvicorn app.main:app --host 0.0.0.0 --port 8000 }
        Test  = { param($py) & $py -m pytest -q }
        Notes = "Full stack with Docker: docker compose -f raglens\docker-compose.yml up"
    }
    "copilot" = @{
        Dir   = "cloud-data-warehouse-ai-copilot"
        Venv  = ".venv"
        Req   = "requirements.txt"
        Port  = 8000
        Start = { param($py) & $py -m uvicorn api.main:app --host 0.0.0.0 --port 8000 }
        Test  = { param($py) & $py -m pytest -q }
        Notes = "Makefile has the data tasks: make generate, make ingest, make dbt"
    }
    "workflow-assistant" = @{
        Dir   = "."
        Venv  = ".venv"
        Req   = "requirements.txt"
        Port  = 8050
        Start = { param($py) & $py -m uvicorn api.agent_workflow_api:app --host 0.0.0.0 --port 8050 }
        Test  = { param($py) & $py -m pytest tests/test_agent_workflow_api.py tests/test_agent_workflow_assistant.py -q }
        Notes = "Shares the repo-root venv with cyber-fusion."
    }
}

function Show-Projects {
    Write-Host ""
    Write-Host "Portfolio projects" -ForegroundColor Cyan
    Write-Host ""
    foreach ($name in $Projects.Keys) {
        $config = $Projects[$name]
        $venvPath = Join-Path $Root (Join-Path $config.Dir $config.Venv)
        $ready = if (Test-Path (Join-Path $venvPath "Scripts\python.exe")) { "ready " } else { "no venv" }
        "{0,-20} {1}  port {2,-5} {3}" -f $name, $ready, $config.Port, $config.Dir | Write-Host
    }
    Write-Host ""
    Write-Host "Usage: .\start.ps1 <name> [-Install] [-Test] [-Demo]" -ForegroundColor DarkGray
    Write-Host ""
}

if ($List -or -not $Project) {
    Show-Projects
    return
}

if (-not $Projects.Contains($Project)) {
    Write-Host "Unknown project '$Project'." -ForegroundColor Red
    Show-Projects
    exit 1
}

$config = $Projects[$Project]
$projectDir = Join-Path $Root $config.Dir
if (-not (Test-Path $projectDir)) {
    throw "Project directory not found: $projectDir"
}

Push-Location $projectDir
try {
    $venvDir = $config.Venv
    $python = Join-Path $venvDir "Scripts\python.exe"

    if (-not (Test-Path $python)) {
        Write-Host "Creating virtual environment for '$Project'..." -ForegroundColor Yellow
        python -m venv $venvDir
        & $python -m pip install --upgrade pip --quiet
        $freshInstall = $true
    } else {
        $freshInstall = $false
    }

    if ($freshInstall -or $Install) {
        if ($Project -eq "docushield") {
            # CPU wheels keep the download to a fraction of the default CUDA build.
            Write-Host "Installing CPU-only torch..." -ForegroundColor Yellow
            & $python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu --quiet
        }
        Write-Host "Installing requirements for '$Project'..." -ForegroundColor Yellow
        & $python -m pip install -r $config.Req --quiet
        Write-Host "Dependencies ready." -ForegroundColor Green
    }

    $env:PYTHONPATH = (Get-Location).Path

    if ($Install) {
        Write-Host "'$Project' is installed. Start it with: .\start.ps1 $Project" -ForegroundColor Green
        return
    }

    if ($Test) {
        Write-Host "Running tests for '$Project'..." -ForegroundColor Cyan
        & $config.Test $python
        return
    }

    if ($Demo -and $Project -eq "ireland") {
        Write-Host "Refreshing from the live EirGrid feed..." -ForegroundColor Cyan
        $end = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
        $start = (Get-Date).AddDays(-7).ToString("yyyy-MM-dd")
        & $python -m src.ingest.build_real_events --start $start --end $end
        & $python -m src.pipelines.local_lakehouse_pipeline
        & $python -m src.ml.train_forecast_model
        & $python -m src.mlops.monitor
        & $python -m src.ai.generate_insights
        & $python -m src.app.export_dashboard_data
    }

    if ($config.Notes) {
        Write-Host "Note: $($config.Notes)" -ForegroundColor DarkGray
    }
    Write-Host "Starting '$Project' on port $($config.Port)..." -ForegroundColor Green
    & $config.Start $python
}
finally {
    Pop-Location
}
