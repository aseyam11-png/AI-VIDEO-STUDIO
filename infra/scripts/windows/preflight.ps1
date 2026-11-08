$ErrorActionPreference = "Continue"

Write-Host "== Windows =="
Get-ComputerInfo | Select-Object WindowsProductName, WindowsVersion, OsArchitecture

Write-Host "== RAM =="
Get-CimInstance Win32_ComputerSystem |
    Select-Object @{Name="RAM_GB";Expression={[math]::Round($_.TotalPhysicalMemory / 1GB, 1)}}

Write-Host "== GPU =="
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    nvidia-smi
} else {
    Get-CimInstance Win32_VideoController |
        Select-Object Name, VideoProcessor, DriverVersion
}

Write-Host "== Developer tools =="
foreach ($cmd in @("git", "gh", "docker", "python", "node", "npm")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) { Write-Host "$cmd : $($found.Source)" } else { Write-Host "$cmd : not found" }
}

Write-Host "Preflight only. No software, model weights, GPU pods, or paid services were installed or started."
