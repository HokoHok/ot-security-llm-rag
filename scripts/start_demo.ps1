[CmdletBinding()]
param(
    [switch]$OpenBrowser,
    [switch]$RunQuickValidation
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$AdapterUrl = "http://127.0.0.1:8001/healthz"
$WebUiUrl = "http://127.0.0.1:3000"

Set-Location $ProjectRoot

function Test-HttpEndpoint {
    param([Parameter(Mandatory = $true)][string]$Url)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-DockerEngine {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & docker info 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python virtual environment was not found: $Python"
}

Write-Host "[1/5] Checking Ollama..."
try {
    $ollama = Invoke-RestMethod "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    $modelNames = @($ollama.models | ForEach-Object { $_.name })
    if (-not ($modelNames -contains "llama3.1:8b-instruct-q5_0")) {
        throw "The configured chat model is not available in Ollama."
    }
    if (-not ($modelNames -contains "embeddinggemma:300m-qat-q8_0")) {
        throw "The configured embedding model is not available in Ollama."
    }
    Write-Host "  [OK] Ollama and required models are available."
}
catch {
    throw "Ollama is unavailable or required models are missing: $($_.Exception.Message)"
}

Write-Host "[2/5] Starting Open WebUI container..."
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($null -eq $docker) {
    throw "Docker command was not found. Start Docker Desktop, then run this script again."
}
if (-not (Test-DockerEngine)) {
    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path -LiteralPath $dockerDesktop)) {
        throw "Docker Engine is unavailable and Docker Desktop was not found at: $dockerDesktop"
    }
    Write-Host "  Docker Engine is stopped; starting Docker Desktop..."
    Start-Process -FilePath $dockerDesktop
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        if (Test-DockerEngine) { break }
        Start-Sleep -Seconds 2
    }
    if (-not (Test-DockerEngine)) {
        throw "Docker Desktop did not make its Linux Engine available within 60 seconds."
    }
}
$container = & docker ps -a --filter "name=^/ot-tutor-webui$" --format "{{.Names}}"
if ($container -ne "ot-tutor-webui") {
    throw "Open WebUI container 'ot-tutor-webui' was not found. Follow docs/RAG_RUNBOOK.md to create it once."
}
& docker start ot-tutor-webui | Out-Null
for ($attempt = 1; $attempt -le 12; $attempt++) {
    if (Test-HttpEndpoint -Url $WebUiUrl) { break }
    Start-Sleep -Seconds 2
}
if (-not (Test-HttpEndpoint -Url $WebUiUrl)) {
    throw "Open WebUI did not become ready at $WebUiUrl"
}
Write-Host "  [OK] Open WebUI is ready."

Write-Host "[3/5] Starting the local RAG adapter when needed..."
if (-not (Test-HttpEndpoint -Url $AdapterUrl)) {
    Start-Process -FilePath $Python `
        -ArgumentList "-m", "app.openai_adapter", "--host", "127.0.0.1", "--port", "8001" `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden
    for ($attempt = 1; $attempt -le 8; $attempt++) {
        if (Test-HttpEndpoint -Url $AdapterUrl) { break }
        Start-Sleep -Seconds 1
    }
}
if (-not (Test-HttpEndpoint -Url $AdapterUrl)) {
    throw "The RAG adapter did not become ready at http://127.0.0.1:8001"
}
Write-Host "  [OK] Adapter is ready with scenario models."

Write-Host "[4/5] Checking indexed scenario collections..."
& $Python -m app.final_validation --output ".\reports\final_validation_quick"
if ($LASTEXITCODE -ne 0) {
    throw "Quick final validation failed. Read reports\final_validation_quick.md"
}
Write-Host "  [OK] Scenario assets, indexes, and local services passed validation."

if ($RunQuickValidation) {
    Write-Host "[5/5] Running pytest..."
    & $Python -m app.final_validation --with-pytest --output ".\reports\final_validation_demo"
    if ($LASTEXITCODE -ne 0) {
        throw "Pytest validation failed. Read reports\final_validation_demo.md"
    }
    Write-Host "  [OK] pytest passed."
}
else {
    Write-Host "[5/5] Ready. Use -RunQuickValidation to include pytest."
}

Write-Host ""
Write-Host "Open WebUI: $WebUiUrl"
Write-Host "Select an ot-tutor-* model, not the raw llama3.1 model."
if ($OpenBrowser) {
    Start-Process $WebUiUrl
}
