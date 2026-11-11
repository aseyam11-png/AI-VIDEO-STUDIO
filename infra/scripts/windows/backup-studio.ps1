[CmdletBinding()]
param([string]$OutputDirectory = (Join-Path $PSScriptRoot "..\..\..\..\studio-backups"))
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$RepoPrefix = $RepoRoot.TrimEnd('\') + '\'
$ComposeFile = Join-Path $RepoRoot "compose.yaml"
$EnvFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path -LiteralPath $EnvFile)) { throw "Missing .env. Configure database and media settings first." }
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
if ($resolvedOutput.StartsWith($RepoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Backup output must be outside the Git checkout." }
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
$mediaLine = Get-Content -LiteralPath $EnvFile | Where-Object { $_ -match '^\s*MEDIA_HOST_PATH\s*=' } | Select-Object -First 1
if (-not $mediaLine) { throw "MEDIA_HOST_PATH is not configured in .env." }
$mediaValue = ($mediaLine -split "=", 2)[1].Trim().Trim('"').Trim("'")
if (-not [System.IO.Path]::IsPathRooted($mediaValue)) { $mediaValue = Join-Path $RepoRoot $mediaValue }
if (-not (Test-Path -LiteralPath $mediaValue -PathType Container)) { throw "The configured private media directory was not found." }
$MediaPath = (Resolve-Path -LiteralPath $mediaValue).Path
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmssZ")
$dbDump = Join-Path $resolvedOutput "studio-$stamp.dump"
$mediaArchive = Join-Path $resolvedOutput "studio-media-$stamp.tar.gz"
$manifest = Join-Path $resolvedOutput "studio-$stamp.sha256.txt"
$activeServices = @(& docker compose -f $ComposeFile ps --services --filter status=running)
if ($LASTEXITCODE -ne 0) { throw "Could not inspect Compose services." }
$writerServices = @($activeServices | Where-Object { $_ -in @("api", "worker-cpu", "worker-speech", "worker-models", "worker-control", "scheduler") })
Push-Location $RepoRoot
try {
    if ($writerServices.Count -gt 0) {
        & docker compose stop @writerServices
        if ($LASTEXITCODE -ne 0) { throw "Could not stop all active API/worker writers." }
    }
    & docker compose exec -T postgres sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/studio-backup.dump'
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL custom-format backup failed." }
    & docker compose cp postgres:/tmp/studio-backup.dump $dbDump
    if ($LASTEXITCODE -ne 0) { throw "Could not copy the database dump." }
    & docker compose exec -T postgres rm -f /tmp/studio-backup.dump
    if ($LASTEXITCODE -ne 0) { Write-Warning "Temporary database dump could not be removed from the container." }
    & tar.exe -czf $mediaArchive -C $MediaPath .
    if ($LASTEXITCODE -ne 0) { throw "Private media archive failed." }
    $databaseHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $dbDump).Hash.ToLowerInvariant()
    $mediaHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $mediaArchive).Hash.ToLowerInvariant()
    @(
        "created_at_utc=$((Get-Date).ToUniversalTime().ToString('o'))"
        "database_file=$([System.IO.Path]::GetFileName($dbDump))"
        "database_sha256=$databaseHash"
        "media_file=$([System.IO.Path]::GetFileName($mediaArchive))"
        "media_sha256=$mediaHash"
    ) | Set-Content -Encoding utf8 -LiteralPath $manifest
    Write-Host "Consistent backup created:"
    Write-Host "  Database: $dbDump"
    Write-Host "  Media:    $mediaArchive"
    Write-Host "  Manifest: $manifest"
    Write-Host "Move files to encrypted storage outside Git. Backup success is not a restore test."
}
finally {
    if ($writerServices.Count -gt 0) {
        & docker compose start @writerServices
        if ($LASTEXITCODE -ne 0) { Write-Warning "One or more Studio services did not restart. Inspect docker compose ps." }
    }
    Pop-Location
}
