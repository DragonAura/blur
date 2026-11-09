# Fixed portable runtime: no machine-wide Python or VapourSynth installation.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$outDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "out"))

function Remove-BuildPath {
    param([Parameter(Mandatory)][string]$Path)
    $resolved = [IO.Path]::GetFullPath($Path)
    if ($resolved -ne $outDir -and -not $resolved.StartsWith($outDir + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside ci/out: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        if ((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to remove a redirected build path: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}

Remove-BuildPath $outDir

$vapoursynthDir = Join-Path $outDir "vapoursynth"
$ffmpegDir = Join-Path $outDir "ffmpeg"
$sitePackagesDir = Join-Path $vapoursynthDir "Lib/site-packages"
$pluginsDir = Join-Path $sitePackagesDir "vapoursynth/plugins"
$modelsBaseDir = Join-Path $outDir "models"

# Create directories if they don't exist
New-Item -ItemType Directory -Force -Path $vapoursynthDir | Out-Null
New-Item -ItemType Directory -Force -Path $ffmpegDir | Out-Null
New-Item -ItemType Directory -Force -Path $pluginsDir | Out-Null
New-Item -ItemType Directory -Force -Path $modelsBaseDir | Out-Null

# Function to download file
function Download-File {
    param (
        [string]$Url,
        [string]$OutFile
    )

    Write-Host "Downloading $Url to $OutFile"
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -MaximumRetryCount 3 -RetryIntervalSec 5
    Write-Host "Download completed"
}

function Assert-FileSha256 {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$ExpectedHash
    )
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($actual -ne $ExpectedHash) {
        throw "SHA256 mismatch for ${Path}: expected $ExpectedHash, got $actual"
    }
}

# Function to extract files from archive and copy to destination
function Extract-Files {
    param (
        [string]$ArchivePath,
        [string[]]$FilePatterns,
        [string]$DestinationPath
    )

    if (-not (Get-Command "7z" -ErrorAction SilentlyContinue)) {
        throw "7z not found. Install 7-Zip and add it to PATH."
    }

    $tempDir = [System.IO.Path]::Combine([System.IO.Path]::GetDirectoryName($ArchivePath), "temp-extract-" + [System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

    try {
        # Extract archive
        & 7z x $ArchivePath -o"$tempDir" -y
        if ($LASTEXITCODE -ne 0) { throw "Failed to extract $ArchivePath" }

        foreach ($pattern in $FilePatterns) {
            $filePath = Join-Path $tempDir $pattern
            if (Test-Path $filePath) {
                Copy-Item -Path $filePath -Destination $DestinationPath
                Write-Host "Copied $pattern to $DestinationPath"
            } else {
                throw "Could not find $pattern in $ArchivePath"
            }
        }
    }
    finally {
        # Clean up
        if (Test-Path $tempDir) {
            Remove-BuildPath $tempDir
        }
        if (Test-Path $ArchivePath) {
            Remove-BuildPath $ArchivePath
        }
    }
}

# Function to download model files from GitHub
function Download-ModelFiles {
    param (
        [string]$BaseUrl,
        [string]$ModelName,
        [string[]]$FileList
    )

    Write-Host "Downloading model: $ModelName"
    $modelDir = Join-Path $modelsBaseDir $ModelName

    # Create model directory if it doesn't exist
    if (-not (Test-Path $modelDir)) {
        New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
        Write-Host "Created directory: $modelDir"
    }

    foreach ($file in $FileList) {
        $fileUrl = "$BaseUrl/$file"
        $outputPath = Join-Path $modelDir $file
        Download-File -Url $fileUrl -OutFile $outputPath
    }

    Write-Host "Model $ModelName download completed"
}

# R82 ships its binaries in a cp312-abi3 wheel. Install the wheel in the
# official portable layout so vsscript can locate Python without registration.
$pythonArchive = Join-Path $outDir "python-3.12.10-embed-amd64.zip"
$vsArchive = Join-Path $outDir "VapourSynth64-Portable-R82.zip"
Download-File -Url "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip" -OutFile $pythonArchive
Download-File -Url "https://github.com/vapoursynth/vapoursynth/releases/download/R82/VapourSynth64-Portable-R82.zip" -OutFile $vsArchive
Expand-Archive -LiteralPath $pythonArchive -DestinationPath $vapoursynthDir -Force
Expand-Archive -LiteralPath $vsArchive -DestinationPath $vapoursynthDir -Force
Add-Content -LiteralPath (Join-Path $vapoursynthDir "python312._pth") -Value "Lib/site-packages" -Encoding utf8
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory(
    (Join-Path $vapoursynthDir "wheel/vapoursynth-82-cp312-abi3-win_amd64.whl"),
    $sitePackagesDir,
    $true
)
Remove-BuildPath $pythonArchive
Remove-BuildPath $vsArchive
Remove-BuildPath (Join-Path $vapoursynthDir "doc")
Remove-BuildPath (Join-Path $vapoursynthDir "wheel")
Get-ChildItem -LiteralPath (Join-Path $sitePackagesDir "vapoursynth") -Filter *.pdb |
    ForEach-Object { Remove-BuildPath $_.FullName }

# Plugin installations
# Newer API4 plugins use the standard wheel layout. Keep the complete wheel,
# including Akarin's adjacent libzstd.dll and license metadata.
$pluginWheels = @(
    @{
        Name = "vapoursynth_akarin-1.5.0-py3-none-win_amd64.whl";
        Url = "https://files.pythonhosted.org/packages/0d/31/95658c029a7ee3bbfc1359f9fa623a13f0a3ff0d940ba9e204421dd7a0ca/vapoursynth_akarin-1.5.0-py3-none-win_amd64.whl";
    },
    @{
        Name = "vapoursynth_mvtools-29-py3-none-win_amd64.whl";
        Url = "https://files.pythonhosted.org/packages/28/f5/1697a4c7238f442a24982a3e5b7b4cbc54d411b40bf3f83b955ab0ed3cc2/vapoursynth_mvtools-29-py3-none-win_amd64.whl";
    }
)
foreach ($wheel in $pluginWheels) {
    $wheelPath = Join-Path $outDir $wheel.Name
    Download-File -Url $wheel.Url -OutFile $wheelPath
    [IO.Compression.ZipFile]::ExtractToDirectory($wheelPath, $sitePackagesDir, $true)
    Remove-BuildPath $wheelPath
}

# The official x64 release and the identically versioned PyPI wheel are not
# equivalent builds. Use the verified release DLL, which retains the dedicated
# P010/SSE2 conversion path. Pin both the archive and the extracted binary.
$lsmashArchive = Join-Path $outDir "L-SMASH-Works-r1310.0.0.0.7z"
$lsmashExtractDir = Join-Path $outDir "lsmash-release-extract"
$lsmashLicenseDir = Join-Path $outDir "licenses/LSMASHSource-r1310"
Download-File -Url "https://github.com/HomeOfAviSynthPlusEvolution/L-SMASH-Works/releases/download/1310.0.0.0/L-SMASH-Works-r1310.0.0.0.7z" -OutFile $lsmashArchive
Assert-FileSha256 -Path $lsmashArchive -ExpectedHash "b4fd48e3cb97c9583e08e69a3dd49a6500dede3bbc054f0fb9cddd0f5448e6e1"
if (-not (Get-Command "7z" -ErrorAction SilentlyContinue)) { throw "7z not found. Install 7-Zip and add it to PATH." }
try {
    & 7z x $lsmashArchive -o"$lsmashExtractDir" -y
    if ($LASTEXITCODE -ne 0) { throw "Failed to extract $lsmashArchive" }
    $lsmashDll = Join-Path $lsmashExtractDir "x64/LSMASHSource.dll"
    Assert-FileSha256 -Path $lsmashDll -ExpectedHash "0abac09524e56413eeb8b52119d8575fc14753492ce53e2133943b91e08452d6"
    Copy-Item -LiteralPath $lsmashDll -Destination $pluginsDir
    New-Item -ItemType Directory -Force -Path $lsmashLicenseDir | Out-Null
    Copy-Item -LiteralPath (Join-Path $lsmashExtractDir "VapourSynth/LICENSE"), (Join-Path $lsmashExtractDir "VapourSynth/README.md") -Destination $lsmashLicenseDir
} finally {
    Remove-BuildPath $lsmashExtractDir
    Remove-BuildPath $lsmashArchive
}

# App-local CRT DLLs come only from the installed MSVC toolchain's documented
# redistributable directory, populated by the CI Developer Command Prompt.
# Never harvest DLLs from System32 or an unrelated application's installation.
if (-not $env:VCToolsRedistDir -or -not (Test-Path -LiteralPath $env:VCToolsRedistDir -PathType Container)) {
    throw "VCToolsRedistDir is unavailable. Run from the configured MSVC x64 Developer Command Prompt."
}
$crtDirectory = Get-ChildItem -LiteralPath (Join-Path $env:VCToolsRedistDir "x64") -Directory -Filter "Microsoft.VC*.CRT" |
    Sort-Object Name -Descending | Select-Object -First 1
if (-not $crtDirectory) { throw "The MSVC x64 redistributable CRT directory was not found." }
foreach ($name in @("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")) {
    if (-not (Test-Path -LiteralPath (Join-Path $crtDirectory.FullName $name) -PathType Leaf)) {
        throw "MSVC redistributable is missing $name"
    }
}
$crtFiles = @(Get-ChildItem -LiteralPath $crtDirectory.FullName -Filter *.dll -File)
# Python.exe and vspipe.exe live in different directories. Supply the same CRT
# set beside both entry points, so neither needs a machine-wide VC installation.
foreach ($destination in @($vapoursynthDir, (Join-Path $sitePackagesDir "vapoursynth"))) {
    foreach ($file in $crtFiles) {
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
}
$crtLicenseDir = Join-Path $outDir "licenses/MSVC-runtime"
New-Item -ItemType Directory -Force -Path $crtLicenseDir | Out-Null
@(
    "MSVC x64 app-local runtime copied from the build toolchain's VCToolsRedistDir.",
    "CRT folder: $($crtDirectory.Name)",
    "Redistribution documentation: https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files",
    "Files (SHA256):"
) + @($crtFiles | ForEach-Object {
    "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name
}) | Set-Content -LiteralPath (Join-Path $crtLicenseDir "BUILD-PROVENANCE.txt") -Encoding utf8

$plugins = @(
    @{
        Name = "BestSource";
        Url = "https://github.com/vapoursynth/bestsource/releases/download/R11/BestSource-R11.7z";
        FilePatterns = @("BestSource.dll");
    },
    @{
        Name = "VapourSynth-RIFE-ncnn-Vulkan";
        Url = "https://github.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan/releases/download/r9_mod_v33/librife_windows_x86-64.dll";
        IsDirectDll = $true;
    },
    @{
        Name = "Adjust";
        Url = "https://github.com/f0e/Vapoursynth-adjust/releases/download/v1/adjust.dll";
        IsDirectDll = $true;
    }
)

# Legacy GPL SVPFlow uses the removed VS API3 and cannot be bundled with R82.
# This release targets frame blending without interpolation; RIFE remains
# available for users who explicitly select it.

foreach ($plugin in $plugins) {
    Write-Host "Processing $($plugin.Name) plugin..."

    # Determine if it's a direct DLL download by checking if IsDirectDll is true
    $isDirectDll = $plugin.ContainsKey('IsDirectDll') -and $plugin.IsDirectDll

    if ($isDirectDll) {
        # Direct DLL download (no extraction needed)
        $dllPath = Join-Path $pluginsDir "$($plugin.Name.ToLower()).dll"
        Download-File -Url $plugin.Url -OutFile $dllPath
    } else {
        # Archive download that needs extraction
        $archiveExt = if ($plugin.Url.EndsWith('.zip')) {'.zip'} else {'.7z'}
        $archivePath = Join-Path $vapoursynthDir "$($plugin.Name.ToLower())$archiveExt"
        Download-File -Url $plugin.Url -OutFile $archivePath
        Extract-Files -ArchivePath $archivePath -FilePatterns $plugin.FilePatterns -DestinationPath $pluginsDir
    }
}

# Download and process FFmpeg
$ffmpegUrl = "https://github.com/GyanD/codexffmpeg/releases/download/2025-08-14-git-cdbb5f1b93/ffmpeg-2025-08-14-git-cdbb5f1b93-full_build.7z"
$ffmpegArchive = Join-Path $ffmpegDir "ffmpeg-git-essentials.7z"
Download-File -Url $ffmpegUrl -OutFile $ffmpegArchive
Extract-Files -ArchivePath $ffmpegArchive -FilePatterns @(
    "ffmpeg-2025-08-14-git-cdbb5f1b93-full_build\\bin\ffmpeg.exe",
    "ffmpeg-2025-08-14-git-cdbb5f1b93-full_build\\bin\ffprobe.exe"
) -DestinationPath $ffmpegDir

# Define model downloads
$modelDownloads = @(
    @{
        BaseUrl = "https://raw.githubusercontent.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan/c3ec6aabc07c8fa37a4f58d7fed9e2ad1fc1b13f/models/rife-v4.26_ensembleFalse";
        ModelName = "rife-v4.26_ensembleFalse";
        FileList = @("flownet.bin", "flownet.param");
    }
)

# Download all models
foreach ($model in $modelDownloads) {
    Download-ModelFiles -BaseUrl $model.BaseUrl -ModelName $model.ModelName -FileList $model.FileList
}

Write-Host "Done"

# Catch incomplete archives and plugin/runtime incompatibility before packaging.
& (Join-Path $vapoursynthDir "python.exe") -c "import vapoursynth as vs; c = vs.core; assert vs.__version__.release_major == 82; assert all(hasattr(c, p) for p in ('akarin', 'lsmas', 'bs', 'mv', 'rife', 'adjust')); print(c)"
if ($LASTEXITCODE -ne 0) { throw "Portable VapourSynth R82/plugin smoke check failed" }
