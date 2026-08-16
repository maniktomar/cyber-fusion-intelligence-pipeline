$hostAddress = "127.0.0.1"
$port = 8050

while ($port -le 8060) {
    $connection = Get-NetTCPConnection -LocalAddress $hostAddress -LocalPort $port -ErrorAction SilentlyContinue
    if (-not $connection) {
        break
    }
    $port += 1
}

if ($port -gt 8060) {
    Write-Error "No free port found between 8050 and 8060."
    exit 1
}

Write-Host "Starting AI Agent Workflow Assistant at http://$hostAddress`:$port" -ForegroundColor Green
.\.venv\Scripts\python.exe -m uvicorn api.agent_workflow_api:app --host $hostAddress --port $port
