# Interactive production CMS update + migrate. Opens SSH with sudo password prompt.
# SUPERSEDED for routine use — prefer the GitHub Actions workflow_dispatch
# CMS-migrate job (cd-cms-migrate.sh). Kept for incident-only SSH use.
#
# Usage: .\run-prod-cms-migrate.ps1 -Image ghcr.io/tahamohamadi-ir/taha-cms:<sha>
# Get <sha> from the latest 'CMS image' workflow run on main. No default pin:
# a stale default (b369885) previously skipped migrations.
param(
    [Parameter(Mandatory = $true)]
    [string]$Image
)
$ErrorActionPreference = "Stop"
$LogFile = Join-Path $PSScriptRoot "..\..\cms-deploy-output.log"
$Key = Join-Path $env:USERPROFILE ".ssh\taha-platform-ops"
$Remote = "deploy@85.192.29.196"
$Port = 2222

Write-Host "Production CMS update + migrate"
Write-Host "Enter VPS sudo password when prompted."
Write-Host "Log: $LogFile"
Write-Host ""

$cmd = @"
export CMS_IMAGE=$Image CMS_BUILD=0
sudo CMS_IMAGE=$Image CMS_BUILD=0 bash /home/deploy/cms-repo/infra/deploy/prod-cms-update-migrate.sh
echo FINAL_EXIT=`$?
"@

ssh -tt -p $Port -i $Key -o IdentitiesOnly=yes $Remote $cmd 2>&1 | Tee-Object -FilePath $LogFile

Write-Host ""
Write-Host "Done. See $LogFile"
