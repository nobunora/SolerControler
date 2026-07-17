Set-StrictMode -Version Latest

function Import-ProductionEnv {
    param([string]$Path = (Join-Path (Split-Path $PSScriptRoot -Parent) '.env'))

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Production environment file not found: $Path"
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $raw = $line.Trim()
        if (-not $raw -or $raw.StartsWith('#') -or -not $raw.Contains('=')) {
            continue
        }
        $key, $value = $raw.Split('=', 2)
        $key = $key.Trim()
        $value = $value.Trim()
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($key -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw "Invalid environment key in ${Path}: $key"
        }
        [Environment]::SetEnvironmentVariable($key, $value, 'Process')
    }
}

function Get-RequiredProductionEnv {
    param([Parameter(Mandatory = $true)][string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Required production setting is missing or empty in .env: $Name"
    }
    return $value.Trim()
}

function Get-ProductionEnv {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$Default = ''
    )

    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim()
}

function Assert-ProductionEnv {
    param([Parameter(Mandatory = $true)][string[]]$Names)

    foreach ($name in $Names) {
        [void](Get-RequiredProductionEnv -Name $name)
    }
}
