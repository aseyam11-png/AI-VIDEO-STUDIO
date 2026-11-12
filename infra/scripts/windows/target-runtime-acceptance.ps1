[CmdletBinding()]
param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\..\..\..\runtime-acceptance"),
    [string]$ProjectId = "",
    [string]$VideoAssetId = "",
    [string]$ProductImageAssetId = "",
    [ValidateRange(1, 360)]
    [int]$MaxJobWaitMinutes = 120,
    [switch]$RunQwenInference,
    [switch]$RunWan,
    [switch]$RunRIFE,
    [switch]$RunRealESRGAN
)

$ErrorActionPreference = "Stop"
if (-not ($RunQwenInference -or $RunWan -or $RunRIFE -or $RunRealESRGAN)) {
    throw "Select at least one real inference test: -RunQwenInference, -RunWan, -RunRIFE, or -RunRealESRGAN."
}
$ApiBaseUrl = $ApiBaseUrl.TrimEnd("/")
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$RepoPrefix = $RepoRoot.TrimEnd('\') + '\'
$ResolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
if ($ResolvedOutput.StartsWith($RepoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "GPU acceptance evidence must be stored outside the Git checkout."
}
New-Item -ItemType Directory -Force -Path $ResolvedOutput | Out-Null

$SecureToken = Read-Host "Studio API bearer token (input is hidden)" -AsSecureString
$TokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureToken)
try {
    $ApiToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($TokenPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($TokenPointer)
}
if ([string]::IsNullOrWhiteSpace($ApiToken) -or $ApiToken.Length -lt 32) {
    throw "The API token must contain at least 32 characters."
}

$Headers = @{ Authorization = "Bearer $ApiToken" }
$Evidence = [ordered]@{
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    api_base_url = $ApiBaseUrl
    project_id = $ProjectId
    qwen = $null
    wan = $null
    rife = $null
    realesrgan = $null
    model_status_snapshot = $null
    gpu_before = $null
    gpu_monitor_file = $null
    gpu_after = $null
    notes = @()
}

function Invoke-StudioApi {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateSet("GET", "POST", "PUT")][string]$Method = "GET",
        [object]$Body = $null,
        [int]$TimeoutSec = 30
    )
    $request = @{
        Uri = "$ApiBaseUrl$Path"
        Method = $Method
        Headers = $Headers
        TimeoutSec = $TimeoutSec
        ErrorAction = "Stop"
    }
    if ($null -ne $Body) {
        $request.ContentType = "application/json"
        $request.Body = $Body | ConvertTo-Json -Depth 24 -Compress
    }
    return Invoke-RestMethod @request
}

function Get-ProjectAsset {
    param([string]$AssetId)
    $assets = Invoke-StudioApi -Path "/v1/projects/$ProjectId/assets"
    $asset = @($assets) | Where-Object { $_.id -eq $AssetId } | Select-Object -First 1
    if (-not $asset) { throw "Asset $AssetId was not found in project $ProjectId." }
    return $asset
}

function Submit-And-WaitJob {
    param(
        [Parameter(Mandatory = $true)][string]$JobType,
        [Parameter(Mandatory = $true)][string]$AssetId,
        [Parameter(Mandatory = $true)][hashtable]$Payload,
        [Parameter(Mandatory = $true)][string]$KeySuffix
    )
    $key = "target-runtime-$KeySuffix-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))"
    $created = Invoke-StudioApi -Method POST -Path "/v1/jobs" -Body @{
        project_id = $ProjectId
        asset_id = $AssetId
        job_type = $JobType
        payload = $Payload
        idempotency_key = $key
    } -TimeoutSec 60
    $jobId = [string]$created.id
    if (-not $jobId) { throw "Studio did not return a job id for $JobType." }

    $deadline = (Get-Date).AddMinutes($MaxJobWaitMinutes)
    while ((Get-Date) -lt $deadline) {
        $job = Invoke-StudioApi -Path "/v1/jobs/$jobId" -TimeoutSec 30
        if ($job.status -eq "succeeded") {
            if (-not $job.result) { throw "Job $JobType succeeded without a result payload." }
            return $job
        }
        if ($job.status -in @("failed", "cancelled")) {
            throw "Job $JobType ended as $($job.status): $($job.error_message)"
        }
        Start-Sleep -Seconds 3
    }
    throw "Job $JobType exceeded the configured $MaxJobWaitMinutes-minute wait limit. Job id: $jobId"
}

$GpuMonitor = $null
$GpuLog = Join-Path $ResolvedOutput "gpu-samples.csv"
try {
    Write-Host "Checking Studio readiness and model workflow registry..." -ForegroundColor Cyan
    $ready = Invoke-RestMethod -Uri "$ApiBaseUrl/ready" -TimeoutSec 15
    if ($ready.status -ne "ready") { throw "Studio API/database readiness is not ready." }
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $Evidence.gpu_before = (& nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu --format=csv,noheader) -join "; "
        $Evidence.gpu_monitor_file = $GpuLog
        $GpuMonitor = Start-Job -ArgumentList $GpuLog -ScriptBlock {
            param($LogPath)
            while ($true) {
                try {
                    $sample = & nvidia-smi --query-gpu=timestamp,name,memory.used,utilization.gpu,temperature.gpu --format=csv,noheader 2>$null
                    if ($sample) { $sample | Add-Content -LiteralPath $LogPath }
                } catch {
                    # GPU metrics are supplementary; a failed sample does not alter inference status.
                }
                Start-Sleep -Seconds 2
            }
        }
    } else {
        $Evidence.notes += "nvidia-smi was unavailable; GPU VRAM/offload could not be observed by this script."
    }

    $models = Invoke-StudioApi -Path "/v1/models/status" -TimeoutSec 360
    $Evidence.model_status_snapshot = @{
        ollama = $models.runtimes.ollama
        comfyui = $models.runtimes.comfyui
    }

    $requiredWorkflows = @{
        Wan = "wan22_ti2v_5b_v1"
        RIFE = "rife_interpolate_v1"
        RealESRGAN = "realesrgan_upscale_v1"
    }
    foreach ($name in $requiredWorkflows.Keys) {
        $workflowId = $requiredWorkflows[$name]
        $workflow = $models.runtimes.comfyui.workflows.$workflowId
        if ($name -eq "Wan" -and -not $RunWan) { continue }
        if ($name -eq "RIFE" -and -not $RunRIFE) { continue }
        if ($name -eq "RealESRGAN" -and -not $RunRealESRGAN) { continue }
        if (-not $workflow -or -not $workflow.registered -or -not $workflow.workflow_file_ready -or -not $workflow.bindings_valid) {
            throw "$name workflow $workflowId is not registered and validated against the live ComfyUI node catalog."
        }
    }

    if ($RunQwenInference) {
        Write-Host "Running a real Arabic Qwen plan through the Studio API..." -ForegroundColor Cyan
        $plan = Invoke-StudioApi -Method POST -Path "/v1/director/plan" -TimeoutSec 240 -Body @{
            brief = "أنشئ خطة عربية قصيرة لاختبار إعلان منتج راقٍ. اجعل المشاهد واضحة ومترابطة، وحدد وصفًا بصريًا وحركة كاميرا وصوتًا مناسبًا."
            target_duration_seconds = 15
            language = "ar"
        }
        if (-not $plan.plan -or $plan.plan.scenes.Count -lt 1) {
            throw "Qwen did not return a validated CreativePlan."
        }
        $durationTotal = ($plan.plan.scenes | Measure-Object -Property duration_seconds -Sum).Sum
        if ([int]$durationTotal -ne 15) {
            throw "Qwen CreativePlan scene durations do not sum to 15 seconds."
        }
        $Evidence.qwen = @{
            status = "passed"
            model = $plan.model
            title = $plan.plan.title
            scene_count = $plan.plan.scenes.Count
            duration_seconds = [int]$durationTotal
        }
    }

    $needsProject = $RunWan -or $RunRIFE -or $RunRealESRGAN
    if ($needsProject -and [string]::IsNullOrWhiteSpace($ProjectId)) {
        throw "ProjectId is required when running Wan, RIFE or Real-ESRGAN."
    }
    if ($RunWan -and [string]::IsNullOrWhiteSpace($ProductImageAssetId)) {
        throw "ProductImageAssetId is required for the Wan image-to-video test."
    }
    if (($RunRIFE -or $RunRealESRGAN) -and [string]::IsNullOrWhiteSpace($VideoAssetId) -and -not $RunWan) {
        throw "VideoAssetId is required for RIFE/Real-ESRGAN unless the same run generates a Wan video."
    }

    $wanOutputAssetId = ""
    if ($RunWan) {
        $imageAsset = Get-ProjectAsset -AssetId $ProductImageAssetId
        if ($imageAsset.asset_type -ne "image") { throw "ProductImageAssetId must reference a project-owned image asset." }
        if ([string]$imageAsset.sha256 -notmatch "^[0-9a-f]{64}$") {
            throw "The selected image asset has no valid SHA-256 fingerprint."
        }

        Write-Host "Running a real two-shot Wan 2.2 generation with product identity lock..." -ForegroundColor Cyan
        $shots = @(
            @{
                prompt = "A premium product advertisement. Reveal the exact product from the supplied reference image in a clean, controlled studio composition. Preserve all visible physical details. First shot: a clear hero view, gentle cinematic camera push."
                negative_prompt = "different product, altered silhouette, changed color, wrong material, distorted logo, misspelled label, missing components, extra components, flicker, deformation"
                duration_seconds = 2
            },
            @{
                prompt = "Continue the same premium product advertisement. Keep the exact same referenced product, geometry, colors, materials, logo and labels. Second shot: a restrained close detail view with smooth side camera movement."
                negative_prompt = "different product, altered silhouette, changed color, wrong material, distorted logo, misspelled label, missing components, extra components, flicker, deformation"
                duration_seconds = 2
            }
        )
        $job = Submit-And-WaitJob -JobType "video.generate_sequence" -AssetId $ProductImageAssetId -KeySuffix "wan" -Payload @{
            shots = $shots
            generation_width = 832
            generation_height = 480
            output_width = 640
            output_height = 360
            fps = 24
            crf = 24
            interpolate_factor = 1
            approved = $true
            product_identity_lock = $true
            identity_reference_sha256 = [string]$imageAsset.sha256
        }
        $wanOutputAssetId = [string]$job.result.output_asset_id
        if (-not $wanOutputAssetId) { throw "Wan completed without registering a generated video asset." }
        $Evidence.wan = @{
            status = "passed"
            job_id = $job.id
            output_asset_id = $wanOutputAssetId
            shot_count = 2
            duration_seconds = $job.result.duration_seconds
            identity_reference_sha256 = $imageAsset.sha256
        }
    }

    $testVideoAssetId = if ($wanOutputAssetId) { $wanOutputAssetId } else { $VideoAssetId }
    if ($RunRIFE) {
        $videoAsset = Get-ProjectAsset -AssetId $testVideoAssetId
        if ($videoAsset.asset_type -ne "video") { throw "RIFE input must be a project-owned video asset." }
        Write-Host "Running a real RIFE interpolation job..." -ForegroundColor Cyan
        $job = Submit-And-WaitJob -JobType "video.interpolate" -AssetId $testVideoAssetId -KeySuffix "rife" -Payload @{
            factor = 2
        }
        if (-not $job.result.output_asset_id) { throw "RIFE completed without registering an output video asset." }
        $Evidence.rife = @{
            status = "passed"
            job_id = $job.id
            source_asset_id = $testVideoAssetId
            output_asset_id = $job.result.output_asset_id
            outputs = $job.result.outputs
        }
    }

    if ($RunRealESRGAN) {
        $videoAsset = Get-ProjectAsset -AssetId $testVideoAssetId
        if ($videoAsset.asset_type -ne "video") { throw "Real-ESRGAN input must be a project-owned video asset." }
        $sourceDuration = [double]$videoAsset.probe.format.duration
        if ($sourceDuration -le 0 -or $sourceDuration -gt 10) {
            throw "Real-ESRGAN acceptance input must be between 0 and 10 seconds; detected $sourceDuration seconds."
        }
        Write-Host "Running real frame-by-frame Real-ESRGAN enhancement..." -ForegroundColor Cyan
        $job = Submit-And-WaitJob -JobType "video.enhance" -AssetId $testVideoAssetId -KeySuffix "realesrgan" -Payload @{
            scale = 2
            fps = 24
            max_duration_seconds = 10
            crf = 24
            approved = $true
        }
        if (-not $job.result.output_asset_id) { throw "Real-ESRGAN completed without registering an output video asset." }
        $Evidence.realesrgan = @{
            status = "passed"
            job_id = $job.id
            source_asset_id = $testVideoAssetId
            output_asset_id = $job.result.output_asset_id
            source_width = $job.result.source_width
            source_height = $job.result.source_height
            output_width = $job.result.output_width
            output_height = $job.result.output_height
            frame_count = $job.result.frame_count
            audio_preserved = $job.result.audio_preserved
        }
    }

    $Evidence.status = "passed"
} catch {
    $Evidence.status = "failed"
    $Evidence.failure = $_.Exception.Message
    throw
} finally {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $Evidence.gpu_after = (& nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu --format=csv,noheader 2>$null) -join "; "
    }
    if ($GpuMonitor) {
        Stop-Job -Job $GpuMonitor -ErrorAction SilentlyContinue | Out-Null
        Receive-Job -Job $GpuMonitor -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $GpuMonitor -Force -ErrorAction SilentlyContinue
    }
    if ($SecureToken) { $SecureToken.Dispose() }
    $ApiToken = $null
    $Headers = $null
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmssZ")
    $evidencePath = Join-Path $ResolvedOutput "runtime-acceptance-$stamp.json"
    $Evidence | ConvertTo-Json -Depth 24 | Set-Content -Encoding utf8 -LiteralPath $evidencePath
    Write-Host "Runtime acceptance evidence written to: $evidencePath" -ForegroundColor Green
}

Write-Host "A model is accepted only if its own inference job succeeded and its generated asset was registered."
