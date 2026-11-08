param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$OllamaBaseUrl = "http://127.0.0.1:11434",
    [string]$ComfyUIBaseUrl = "http://127.0.0.1:8188",
    [switch]$RunArabicInferenceSmokeTest
)

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$Failures = [System.Collections.Generic.List[string]]::new()

function Write-Section([string]$Name) {
    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
}

Write-Section "GPU driver"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    nvidia-smi
    if ($LASTEXITCODE -ne 0) { $Failures.Add("nvidia-smi failed.") }
} else {
    Write-Warning "nvidia-smi is unavailable. This is normal on AMD/non-NVIDIA hosts; ComfyUI device inspection remains required."
}

Write-Section "Docker Engine"
docker version
if ($LASTEXITCODE -ne 0) { $Failures.Add("Docker Engine is not reachable.") }
docker compose version
if ($LASTEXITCODE -ne 0) { $Failures.Add("Docker Compose plugin is unavailable.") }

Push-Location $RepoRoot
try {
    Write-Section "Compose configuration"
    docker compose config --quiet
    if ($LASTEXITCODE -ne 0) { $Failures.Add("compose.yaml validation failed.") }

    Write-Section "Studio API readiness"
    try {
        $ready = Invoke-RestMethod -Uri "$ApiBaseUrl/ready" -TimeoutSec 10
        $ready | ConvertTo-Json -Depth 6
    } catch {
        Write-Warning "Studio API readiness failed: $($_.Exception.Message)"
        $Failures.Add("Studio API is not ready.")
    }

    Write-Section "Model runtime status"
    try {
        $models = Invoke-RestMethod -Uri "$ApiBaseUrl/v1/models/status" -TimeoutSec 30
        $models | ConvertTo-Json -Depth 10
    } catch {
        Write-Warning "Model status endpoint failed: $($_.Exception.Message)"
        $Failures.Add("Model status could not be read.")
    }

    Write-Section "Ollama"
    try {
        $tags = Invoke-RestMethod -Uri "$OllamaBaseUrl/api/tags" -TimeoutSec 10
        $names = @($tags.models | ForEach-Object { $_.name })
        $names
        if ($names -notcontains "qwen3.5:9b-q4_K_M") {
            Write-Warning "Required model tag is not installed. Install it explicitly; this script never downloads weights."
            $Failures.Add("Qwen3.5-9B Q4_K_M is not installed.")
        }
    } catch {
        Write-Warning "Ollama is not reachable at $OllamaBaseUrl."
        $Failures.Add("Ollama is not reachable.")
    }

    Write-Section "ComfyUI"
    try {
        $stats = Invoke-RestMethod -Uri "$ComfyUIBaseUrl/system_stats" -TimeoutSec 10
        $stats | ConvertTo-Json -Depth 8
        $gpuDevices = @($stats.devices | Where-Object {
            ([string]$_.type -match "(?i)cuda|hip|rocm|directml|vulkan") -or
            ([string]$_.name -match "(?i)nvidia|amd|radeon|geforce|rtx")
        })
        if ($gpuDevices.Count -eq 0) {
            Write-Warning "ComfyUI reports no recognizable GPU device. Check the installed PyTorch/backend."
            $Failures.Add("ComfyUI reports no recognizable GPU device.")
        } else {
            $gpuDevices | ConvertTo-Json -Depth 6
        }
    } catch {
        Write-Warning "ComfyUI is not reachable at $ComfyUIBaseUrl."
        $Failures.Add("ComfyUI is not reachable.")
    }

    if ($RunArabicInferenceSmokeTest) {
        Write-Section "Qwen Arabic inference smoke test"
        try {
            $body = @{
                model = "qwen3.5:9b-q4_K_M"
                stream = $false
                messages = @(
                    @{ role = "user"; content = "اكتب كلمة تم فقط باللغة العربية." }
                )
                options = @{ num_ctx = 1024; temperature = 0 }
            } | ConvertTo-Json -Depth 6
            $answer = Invoke-RestMethod -Method Post -Uri "$OllamaBaseUrl/api/chat" -ContentType "application/json" -Body $body -TimeoutSec 180
            $answer.message.content
            if (-not $answer.message.content) { throw "Empty model response." }
        } catch {
            Write-Warning "Arabic inference failed: $($_.Exception.Message)"
            $Failures.Add("Qwen Arabic inference smoke test failed.")
        }
    }
} finally {
    Pop-Location
}

Write-Section "Result"
if ($Failures.Count -gt 0) {
    $Failures | ForEach-Object { Write-Host "- $_" -ForegroundColor Red }
    Write-Host "Runtime acceptance is NOT complete." -ForegroundColor Red
    exit 1
}
Write-Host "Runtime preflight passed. Approved ComfyUI workflow inference must still be tested." -ForegroundColor Green
