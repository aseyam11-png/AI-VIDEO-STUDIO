[CmdletBinding()]
param(
    [string]$MediaPath = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Continue"

function Get-CommandText {
    param(
        [string]$CommandName,
        [string[]]$Arguments
    )

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        return "not_available"
    }

    $output = & $CommandName @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        return (@($output) -join [Environment]::NewLine)
    }
    return (@($output) -join [Environment]::NewLine).Trim()
}

$os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction SilentlyContinue
$computer = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction SilentlyContinue
$processors = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction SilentlyContinue)
$videoControllers = @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction SilentlyContinue)
$logicalDisks = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction SilentlyContinue)

$cpuSummary = @(
    $processors | ForEach-Object {
        [pscustomobject]@{
            name = $_.Name
            cores = $_.NumberOfCores
            logical_processors = $_.NumberOfLogicalProcessors
        }
    }
)

$gpuSummary = @(
    $videoControllers | ForEach-Object {
        [pscustomobject]@{
            name = $_.Name
            driver_version = $_.DriverVersion
            driver_date = $_.DriverDate
        }
    }
)

$diskSummary = @(
    $logicalDisks | ForEach-Object {
        [pscustomobject]@{
            drive = $_.DeviceID
            total_gib = if ($_.Size) { [math]::Round(([double]$_.Size / 1GB), 2) } else { $null }
            free_gib = if ($_.FreeSpace) { [math]::Round(([double]$_.FreeSpace / 1GB), 2) } else { $null }
        }
    }
)

$mediaDrive = $null
$mediaDriveDisk = $null
if ($MediaPath -match "^(?<drive>[A-Za-z]:)") {
    $mediaDrive = $Matches.drive.ToUpperInvariant()
    $mediaDriveDisk = $diskSummary | Where-Object { $_.drive -eq $mediaDrive } | Select-Object -First 1
}

$nvidiaSmi = "not_available"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $nvidiaOutput = & nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1
    if ($LASTEXITCODE -eq 0) {
        $nvidiaSmi = @($nvidiaOutput)
    } else {
        $nvidiaSmi = "command_failed: $(@($nvidiaOutput) -join ' ')"
    }
}

$dockerClientServer = Get-CommandText -CommandName "docker" -Arguments @("version", "--format", "{{.Client.Version}}|{{.Server.Version}}")
$dockerCompose = Get-CommandText -CommandName "docker" -Arguments @("compose", "version", "--short")
$wslStatus = Get-CommandText -CommandName "wsl.exe" -Arguments @("--status")

$report = [ordered]@{
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    powershell_version = $PSVersionTable.PSVersion.ToString()
    operating_system = [ordered]@{
        caption = if ($os) { $os.Caption } else { $null }
        version = if ($os) { $os.Version } else { $null }
        build_number = if ($os) { $os.BuildNumber } else { $null }
        architecture = if ($os) { $os.OSArchitecture } else { $null }
    }
    processor = $cpuSummary
    memory = [ordered]@{
        total_gib = if ($computer -and $computer.TotalPhysicalMemory) {
            [math]::Round(([double]$computer.TotalPhysicalMemory / 1GB), 2)
        } else { $null }
        free_gib_at_capture = if ($os -and $os.FreePhysicalMemory) {
            [math]::Round(([double]$os.FreePhysicalMemory / 1MB), 2)
        } else { $null }
    }
    windows_gpu_devices = $gpuSummary
    nvidia_smi_query = $nvidiaSmi
    fixed_drives = $diskSummary
    media_drive = [ordered]@{
        drive = $mediaDrive
        free_gib = if ($mediaDriveDisk) { $mediaDriveDisk.free_gib } else { $null }
        note = if ($MediaPath) { "Only the drive letter is recorded; the supplied path is not included." } else { "Pass -MediaPath to identify the drive used for private media storage." }
    }
    docker = [ordered]@{
        client_server_version = $dockerClientServer
        compose_version = $dockerCompose
    }
    wsl_status = $wslStatus
    notes = @(
        "This report performs read-only inventory checks. It does not install drivers, change settings, start services or download model weights.",
        "Use nvidia_smi_query as the authoritative NVIDIA model/VRAM/driver reading; Windows CIM GPU data does not reliably expose VRAM above 4 GiB.",
        "No serial numbers, IP addresses, usernames, environment variables or secrets are collected."
    )
}

$json = $report | ConvertTo-Json -Depth 8
if ($OutputPath) {
    try {
        $parent = Split-Path -Parent $OutputPath
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Set-Content -LiteralPath $OutputPath -Value $json -Encoding UTF8
        Write-Host "Inventory saved to: $OutputPath" -ForegroundColor Green
    } catch {
        Write-Warning "Could not save inventory output: $($_.Exception.Message)"
    }
}
Write-Output $json
