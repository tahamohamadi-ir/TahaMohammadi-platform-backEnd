# dev-local-stack.ps1 — laptop helper for the taha-local Compose project.
# Usage:
#   pwsh infra/deploy/dev-local-stack.ps1 up-db      # start Postgres (:15432)
#   pwsh infra/deploy/dev-local-stack.ps1 up-a2      # full-docker cms (:18001) — optional
#   pwsh infra/deploy/dev-local-stack.ps1 down       # stop (keeps data volume)
#   pwsh infra/deploy/dev-local-stack.ps1 reset-db   # stop AND delete data volume
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('up-db', 'up-a2', 'down', 'reset-db')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # infra/deploy -> repo root
$composeFile = Join-Path $repoRoot 'infra/cms/docker-compose.local.yml'

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    Write-Host "> docker compose -f $composeFile $($ComposeArgs -join ' ')"
    & docker compose -f $composeFile @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

switch ($Action) {
    'up-db' {
        Invoke-Compose @('up', '-d', 'db')
        Write-Host 'Waiting for db healthy...'
        $deadline = (Get-Date).AddSeconds(60)
        do {
            Start-Sleep -Seconds 2
            $state = docker inspect --format '{{.State.Health.Status}}' taha-local-db 2>$null
        } while ($state -ne 'healthy' -and (Get-Date) -lt $deadline)
        if ($state -eq 'healthy') {
            Write-Host 'OK: taha-local-db is healthy on localhost:15432 (user=taha db=taha).'
            Write-Host 'Next: $env:DJANGO_SETTINGS_MODULE="config.settings.local"; cd apps/cms; uv run python manage.py migrate'
        }
        else { throw 'db did not become healthy within 60s' }
    }
    'up-a2' {
        Invoke-Compose @('--profile', 'a2', 'up', '-d')
        Write-Host 'OK: full-docker cms on http://127.0.0.1:18001 (A2 rehearsal mode).'
    }
    'down' {
        Invoke-Compose @('--profile', 'a2', 'down')
        Write-Host 'OK: stack stopped (data volume preserved).'
    }
    'reset-db' {
        Invoke-Compose @('--profile', 'a2', 'down', '-v')
        Write-Host 'OK: stack stopped and data volume DELETED. Run up-db + migrate + createsuperuser again.'
    }
}
