[CmdletBinding()]
param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\..\..\..\studio-monitoring"),
    [ValidateRange(5, 3600)]
    [int]$IntervalSeconds = 30,
    [switch]$Once,
    [switch]$RequireModelWorker,
    [switch]$RequireSpeechWorker
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$RepoPrefix = $RepoRoot.TrimEnd('\') + '\'
$ComposeFile = Join-Path $RepoRoot "compose.yaml"
$EnvFile = Join-Path $RepoRoot ".env"
$ApiBaseUrl = $ApiBaseUrl.TrimEnd("/")
$ResolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)

if ($ResolvedOutput.StartsWith($RepoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Monitoring output must be outside the Git checkout."
}
New-Item -ItemType Directory -Force -Path $ResolvedOutput | Out-Null
$LogFile = Join-Path $ResolvedOutput "studio-health.jsonl"

function Get-EndpointProbe {
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        $response = Invoke-RestMethod -Uri "$ApiBaseUrl$Path" -Method Get -TimeoutSec 8
        return @{
            available = $true
            status = 200
            application_status = [string]$response.status
        }
    } catch {
        $statusCode = $null
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        return @{
            available = $false
            status = $statusCode
            application_status = $null
        }
    }
}

function Get-MediaDiskSnapshot {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        return @{ configured = $false; free_bytes = $null; total_bytes = $null; free_percent = $null }
    }
    $line = Get-Content -LiteralPath $EnvFile |
        Where-Object { $_ -match '^\s*MEDIA_HOST_PATH\s*=' } |
        Select-Object -First 1
    if (-not $line) {
        return @{ configured = $false; free_bytes = $null; total_bytes = $null; free_percent = $null }
    }
    $value = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
    if (-not [System.IO.Path]::IsPathRooted($value)) {
        $value = Join-Path $RepoRoot $value
    }
    if (-not (Test-Path -LiteralPath $value -PathType Container)) {
        return @{ configured = $true; free_bytes = $null; total_bytes = $null; free_percent = $null }
    }
    $root = [System.IO.Path]::GetPathRoot((Resolve-Path -LiteralPath $value).Path)
    $drive = Get-PSDrive -PSProvider FileSystem |
        Where-Object { $_.Root -eq $root } |
        Select-Object -First 1
    if (-not $drive) {
        return @{ configured = $true; free_bytes = $null; total_bytes = $null; free_percent = $null }
    }
    $free = [int64]$drive.Free
    $total = [int64]($drive.Free + $drive.Used)
    $percent = if ($total -gt 0) { [math]::Round(($free / $total) * 100, 2) } else { $null }
    return @{
        configured = $true
        free_bytes = $free
        total_bytes = $total
        free_percent = $percent
    }
}

function Get-GpuSnapshot {
    $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $nvidiaSmi) {
        return @{ available = $false; devices = @() }
    }
    try {
        $gpuArguments = @(
            "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits"
        )
        $lines = @(& $nvidiaSmi.Source @gpuArguments 2>$null)
        if ($LASTEXITCODE -ne 0) {
            return @{ available = $false; devices = @() }
        }
        $devices = @()
        foreach ($line in $lines) {
            $parts = @($line -split ",\s*")
            if ($parts.Count -ge 5) {
                $devices += @{
                    name = $parts[0]
                    memory_total_mb = $parts[1]
                    memory_used_mb = $parts[2]
                    utilization_percent = $parts[3]
                    temperature_c = $parts[4]
                }
            }
        }
        return @{ available = ($devices.Count -gt 0); devices = $devices }
    } catch {
        return @{ available = $false; devices = @() }
    }
}

function Get-RunningComposeServices {
    Push-Location $RepoRoot
    try {
        $lines = @(& docker compose -f $ComposeFile ps --services --filter status=running 2>$null)
        if ($LASTEXITCODE -ne 0) {
            return @{ available = $false; services = @() }
        }
        return @{
            available = $true
            services = @($lines | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
        }
    } catch {
        return @{ available = $false; services = @() }
    } finally {
        Pop-Location
    }
}

function Write-StudioSnapshot {
    $health = Get-EndpointProbe -Path "/health"
    $ready = Get-EndpointProbe -Path "/ready"
    $compose = Get-RunningComposeServices
    $requiredServices = @("postgres", "valkey", "api", "worker-cpu", "worker-control", "scheduler")
    if ($RequireModelWorker) { $requiredServices += "worker-models" }
    if ($RequireSpeechWorker) { $requiredServices += "worker-speech" }

    $missing = @()
    if ($compose.available) {
        $missing = @($requiredServices | Where-Object { $_ -notin $compose.services })
    } else {
        $missing = $requiredServices
    }

    $disk = Get-MediaDiskSnapshot
    $gpu = Get-GpuSnapshot
    $healthy = $health.available -and $ready.available -and $compose.available -and ($missing.Count -eq 0)
    if ($null -ne $disk.free_percent -and $disk.free_percent -lt 10) { $healthy = $false }

    $snapshot = [ordered]@{
        timestamp_utc = (Get-Date).ToUniversalTime().ToString("o")
        status = if ($healthy) { "healthy" } else { "degraded" }
        endpoints = @{
            health = $health
            ready = $ready
        }
        compose = @{
            available = $compose.available
            running_services = $compose.services
            missing_required_services = $missing
        }
        media_disk = $disk
        gpu = $gpu
    }

    $json = $snapshot | ConvertTo-Json -Depth 10 -Compress
    Add-Content -LiteralPath $LogFile -Value $json -Encoding utf8
    if ($healthy) {
        Write-Host "[$($snapshot.timestamp_utc)] Studio healthy." -ForegroundColor Green
    } else {
        Write-Warning "[$($snapshot.timestamp_utc)] Studio degraded. Review $LogFile"
    }
}

do {
    Write-StudioSnapshot
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSeconds
} while ($true)
