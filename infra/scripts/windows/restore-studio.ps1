[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$DatabaseDump,
    [Parameter(Mandatory = $true)]
    [string]$MediaArchive,
    [Parameter(Mandatory = $true)]
    [string]$Sha256Manifest,
    [Parameter(Mandatory = $true)]
    [string]$RestoreMediaDirectory,
    [string]$RestoreDatabaseName = "video_studio_restore",
    [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmRestore) {
    throw "Restore creates a separate database and media directory. Review the backup and rerun with -ConfirmRestore."
}
if ($RestoreDatabaseName -notmatch '^[a-z][a-z0-9_]{0,62}$') {
    throw "RestoreDatabaseName must be a safe PostgreSQL identifier (lowercase letters, digits, underscores)."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$RepoPrefix = $RepoRoot.TrimEnd('\') + '\'
$ComposeFile = Join-Path $RepoRoot "compose.yaml"
$EnvFile = Join-Path $RepoRoot ".env"

if (-not (Test-Path -LiteralPath $EnvFile)) { throw "Missing .env." }
$databaseLine = Get-Content -LiteralPath $EnvFile |
    Where-Object { $_ -match '^\s*POSTGRES_DB\s*=' } |
    Select-Object -First 1
if (-not $databaseLine) { throw "POSTGRES_DB is not configured in .env." }
$currentDatabaseName = ($databaseLine -split "=", 2)[1].Trim().Trim('"').Trim("'")
if ($RestoreDatabaseName -eq $currentDatabaseName) {
    throw "RestoreDatabaseName must differ from the configured application database. The live database will not be overwritten."
}

$DatabaseDump = (Resolve-Path -LiteralPath $DatabaseDump).Path
$MediaArchive = (Resolve-Path -LiteralPath $MediaArchive).Path
$Sha256Manifest = (Resolve-Path -LiteralPath $Sha256Manifest).Path
$RestoreMediaDirectory = [System.IO.Path]::GetFullPath($RestoreMediaDirectory)

if ($RestoreMediaDirectory.StartsWith($RepoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Choose a private restore directory outside the Git checkout."
}
if (Test-Path -LiteralPath $RestoreMediaDirectory) {
    if (@(Get-ChildItem -Force -LiteralPath $RestoreMediaDirectory).Count -gt 0) {
        throw "RestoreMediaDirectory must be new or empty. Existing media is never overwritten."
    }
} else {
    New-Item -ItemType Directory -Force -Path $RestoreMediaDirectory | Out-Null
}

$manifestValues = @{}
foreach ($line in Get-Content -LiteralPath $Sha256Manifest) {
    if ($line -match '^([a-z_]+)=(.*)$') { $manifestValues[$Matches[1]] = $Matches[2] }
}
if ($manifestValues.database_file -ne [System.IO.Path]::GetFileName($DatabaseDump)) {
    throw "Database filename does not match the supplied SHA-256 manifest."
}
if ($manifestValues.media_file -ne [System.IO.Path]::GetFileName($MediaArchive)) {
    throw "Media archive filename does not match the supplied SHA-256 manifest."
}
$actualDbHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $DatabaseDump).Hash.ToLowerInvariant()
$actualMediaHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $MediaArchive).Hash.ToLowerInvariant()
if ($actualDbHash -ne $manifestValues.database_sha256) {
    throw "Database dump SHA-256 does not match the manifest."
}
if ($actualMediaHash -ne $manifestValues.media_sha256) {
    throw "Media archive SHA-256 does not match the manifest."
}

$archiveEntries = @(& tar.exe -tzf $MediaArchive)
if ($LASTEXITCODE -ne 0 -or $archiveEntries.Count -eq 0) {
    throw "The media archive is unreadable or empty."
}
foreach ($entry in $archiveEntries) {
    $normalized = $entry.Replace('\', '/')
    if (
        $normalized.StartsWith('/') -or
        $normalized -match '^[A-Za-z]:' -or
        $normalized -match '(^|/)\.\.(/|$)'
    ) {
        throw "The media archive contains an unsafe path entry."
    }
}

Push-Location $RepoRoot
try {
    & docker compose cp $DatabaseDump postgres:/tmp/studio-restore.dump
    if ($LASTEXITCODE -ne 0) { throw "Could not copy the database dump into PostgreSQL." }

    & docker compose exec -T postgres sh -lc 'pg_restore --list /tmp/studio-restore.dump >/dev/null'
    if ($LASTEXITCODE -ne 0) { throw "The database dump failed PostgreSQL archive validation." }

    & docker compose exec -T -e "RESTORE_DATABASE=$RestoreDatabaseName" postgres sh -lc 'createdb -U "$POSTGRES_USER" "$RESTORE_DATABASE"'
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the separate restore database. It may already exist; no existing database was overwritten."
    }

    & docker compose exec -T -e "RESTORE_DATABASE=$RestoreDatabaseName" postgres sh -lc 'pg_restore --no-owner --no-privileges --exit-on-error -U "$POSTGRES_USER" -d "$RESTORE_DATABASE" /tmp/studio-restore.dump'
    if ($LASTEXITCODE -ne 0) {
        throw "Database restore into the separate validation database failed. The configured application database was not modified."
    }

    & docker compose exec -T postgres rm -f /tmp/studio-restore.dump
    if ($LASTEXITCODE -ne 0) { Write-Warning "Temporary restore archive remains inside the PostgreSQL container." }

    & tar.exe -xzf $MediaArchive -C $RestoreMediaDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Media extraction failed. Do not point MEDIA_HOST_PATH at a partial restore."
    }

    Write-Host "Database restore into a separate validation database and media extraction completed."
    Write-Host "Restore database: $RestoreDatabaseName`nRestored media directory: $RestoreMediaDirectory"
    Write-Host "The configured application database and running Studio services were not changed."
    Write-Host "Verify table counts and media file integrity against the restore database and extracted directory. The application is not switched to the restored database or media."
    Write-Host "Only plan a separate cutover after the restore drill, database ownership/privilege review, and media consistency review are accepted."
}
finally {
    Pop-Location
}
