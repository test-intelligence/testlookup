[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$Lite,
    [switch]$SkipModelPull,
    [switch]$SkipVerify,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host $Message -ForegroundColor Cyan
}

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command
    )

    if ($DryRun) {
        Write-Host "[dry-run] $Command"
        return
    }

    Write-Host "+ $Command"
    Invoke-Expression $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

function Get-ComposePrefix {
    if ($Lite) {
        return "docker compose -f docker-compose.dev-lite.yml"
    }
    return "docker compose"
}

function Test-PlaceholderSecrets {
    $envFile = Join-Path $script:RepoRoot ".env"
    if (-not (Test-Path $envFile)) {
        return
    }

    $content = Get-Content -Path $envFile -Raw
    if ($content -match '(<set-|<your-|change-me-)') {
        Write-Warning @"
.env still contains placeholder secrets.
Before sharing your environment or debugging auth/storage issues, update:
  - POSTGRES_PASSWORD
  - MINIO_SECRET_KEY
  - JWT_SECRET_KEY
  - WEBHOOK_SECRET
"@
    }
}

function Wait-ForHttp {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [Parameter(Mandatory = $true)]
        [string]$Label,
        [int]$MaxAttempts = 60,
        [int]$DelaySeconds = 5
    )

    if ($DryRun) {
        Write-Host "[dry-run] wait for $Label at $Url"
        return
    }

    Write-Host "Waiting for $Label at $Url..."
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            Invoke-RestMethod -Uri $Url | Out-Null
            Write-Host "$Label is ready."
            return
        } catch {
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    throw "Timed out waiting for $Label at $Url"
}

function Test-DevLogin {
    if ($DryRun) {
        Write-Host "[dry-run] verify POST http://localhost:8000/api/v1/auth/dev-login?role=admin"
        return
    }

    $response = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/api/v1/auth/dev-login?role=admin"
    if (-not $response.access_token) {
        throw "Development quick login did not return an access token."
    }
    Write-Host "Development quick login is working."
}

Push-Location $script:RepoRoot
try {
    Write-Step "Validating prerequisites"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is not available in PATH. Install Docker Desktop, Rancher Desktop, or another Docker-compatible runtime first."
    }

    if (-not $DryRun) {
        docker compose version | Out-Null
    } else {
        Write-Host "[dry-run] docker compose version"
    }

    $envPath = Join-Path $script:RepoRoot ".env"
    if (-not (Test-Path $envPath)) {
        if ($DryRun) {
            Write-Host "[dry-run] Copy-Item .env.example .env"
        } else {
            Copy-Item ".env.example" ".env"
            Write-Host "Created .env from .env.example"
        }
    }

    Test-PlaceholderSecrets

    $compose = Get-ComposePrefix

    if ($Clean) {
        Write-Step "Removing existing containers and volumes"
        Invoke-Step "$compose down -v --remove-orphans"
    }

    Write-Step "Starting the local developer stack"
    Invoke-Step "$compose up -d --build"

    if (-not $SkipVerify) {
        Write-Step "Verifying backend, frontend, and development login"
        Wait-ForHttp -Url "http://localhost:8000/health/live" -Label "backend"
        Wait-ForHttp -Url "http://localhost:3000" -Label "frontend"
        Test-DevLogin
    }

    if (-not $Lite -and -not $SkipModelPull) {
        Write-Step "Pulling local Ollama models"
        Invoke-Step "$compose exec ollama ollama pull qwen2.5:7b"
        Invoke-Step "$compose exec ollama ollama pull nomic-embed-text"
    }

    $stackLabel = if ($Lite) { "lite" } else { "full" }
    Write-Host ""
    Write-Host "TestLookup local $stackLabel developer stack is ready." -ForegroundColor Green
    Write-Host ""
    Write-Host "Useful URLs:"
    Write-Host "  - Dashboard:      http://localhost:3000"
    Write-Host "  - API Docs:       http://localhost:8000/api-docs"
    Write-Host "  - Health:         http://localhost:8000/health/details"
    Write-Host "  - Flower:         http://localhost:5555"
    Write-Host "  - MinIO Console:  http://localhost:9001"
    Write-Host "  - MCP SSE:        http://localhost:8002/sse"
    Write-Host ""
    Write-Host "Helpful follow-up commands:"
    Write-Host "  - docker compose ps"
    Write-Host "  - docker compose logs seed-init --tail 200"
    Write-Host "  - docker compose logs -f backend worker"
    Write-Host "  - make list-llm"
    Write-Host ""
    Write-Host "Login:"
    Write-Host "  - Open the dashboard and use the Quick Login buttons in development mode."
}
finally {
    Pop-Location
}
