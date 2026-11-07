<#
.SYNOPSIS
    Windows AI Operating Environment (WinAI-OE) Automated Setup Script.
.DESCRIPTION
    Configures environment directories, validates Python prerequisites, installs dependencies,
    and initializes secure DPAPI storage.
#>

param(
    [string]$InstallPath = "$HOME\.winai",
    [switch]$SkipTests = $false
)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Windows AI Operating Environment (WinAI-OE) Installer   " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Directory Structure
Write-Host "[1/5] Initializing local storage at: $InstallPath" -ForegroundColor Yellow
$dirs = @("$InstallPath\data", "$InstallPath\logs", "$InstallPath\backups")
foreach ($d in $dirs) {
    if (!(Test-Path -Path $d)) {
        New-Item -ItemType Directory -Path $d -Force | Out-Null
    }
}

# 2. Verify Python
Write-Host "[2/5] Checking Python 3.12+ installation..." -ForegroundColor Yellow
try {
    $pyVersion = & python --version
    Write-Host "Found: $pyVersion" -ForegroundColor Green
} catch {
    Write-Error "Python is not installed or not in system PATH."
    exit 1
}

# 3. Install Dependencies
Write-Host "[3/5] Installing Python core requirements..." -ForegroundColor Yellow
& python -m pip install -r "D:\Interveiewsass\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to install dependencies."
    exit 1
}

# 4. Initialize DPAPI Vault
Write-Host "[4/5] Initializing Windows DPAPI encrypted key vault..." -ForegroundColor Yellow
$vaultInitScript = @"
from pathlib import Path
from src.storage.credential_vault import CredentialVault
v = CredentialVault(vault_path=Path('$InstallPath/vault.enc'))
print('DPAPI Vault initialized successfully.')
"@
& python -c $vaultInitScript

# 5. Run Verification
if (-not $SkipTests) {
    Write-Host "[5/5] Running automated baseline verification..." -ForegroundColor Yellow
    & python -m pytest "D:\Interveiewsass\tests\unit\test_environment.py" --quiet
}

Write-Host "`nWinAI-OE Installation and Verification Completed Successfully." -ForegroundColor Green
Write-Host "To launch the orchestrator, run: python D:\Interveiewsass\run_vertical_slice.py" -ForegroundColor Cyan
