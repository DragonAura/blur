$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$stageRoot = Join-Path $repoRoot 'build/package-windows'
$portableDir = Join-Path $stageRoot 'blur-gpu-r82'
$installerDir = Join-Path $stageRoot 'installer'
$bundleDir = Join-Path $repoRoot 'bundle'
$versionMatch = [regex]::Match((Get-Content -LiteralPath (Join-Path $repoRoot 'src/common/blur.h') -Raw), 'BLUR_VERSION\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) { throw 'BLUR_VERSION was not found' }
$version = $versionMatch.Groups[1].Value

foreach ($path in @($stageRoot, $bundleDir)) {
    $resolved = [IO.Path]::GetFullPath($path)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath($repoRoot) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a directory outside the repository: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        if ((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Redirected build path: $resolved" }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $resolved | Out-Null
}

New-Item -ItemType Directory -Force -Path (Join-Path $portableDir 'lib') | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot 'bin/Release/blur-gui.exe'), (Join-Path $repoRoot 'bin/Release/blur-cli.exe') -Destination $portableDir
Copy-Item -Path (Join-Path $repoRoot 'src/vapoursynth/*') -Destination (Join-Path $portableDir 'lib') -Recurse
Copy-Item -Path (Join-Path $PSScriptRoot 'out/*') -Destination (Join-Path $portableDir 'lib') -Recurse
Copy-Item -LiteralPath (Join-Path $repoRoot 'LICENSE'), (Join-Path $repoRoot 'README.md') -Destination $portableDir

# Ship a selectable configuration; do not overwrite an existing user's config.
@'
blur: true
blur amount: 1
blur output fps: 60
blur weighting: vegas
blur gamma: 1
interpolate: false
pre-interpolate: false
deduplicate: false
gpu blending: false
gpu decoding: true
fast decoding: true
gpu interpolation: false
gpu encoding: true
nvenc split encoding: true
encode preset: av1
quality: 22
preview: true
'@ | Set-Content -LiteralPath (Join-Path $portableDir '4k120-to-60-av1.cfg') -Encoding utf8

@"
Blur GPU R82 $version (Windows x64)

Run blur-gui.exe, or use:
  blur-cli.exe -i input.mp4 -o output.mp4 -c 4k120-to-60-av1.cfg -v

The supplied profile targets native 4K 120fps footage -> 60fps AV1 output.
It uses Vegas weights, blur amount 1 and gamma 1, without interpolation or deduplication.
Frame blending runs on the CPU; GPU decoding and encoding are enabled.
On NVIDIA, the built-in AV1 preset uses NVENC p5 with CQ 22 in this profile.
This profile also enables fast decoding and NVENC split encoding; both are optional.
Fast decoding requires constant resolution and pixel format. Turn it off if either changes.
NVENC split encoding uses multiple encoding engines on supported NVIDIA GPUs for HEVC/AV1.
It keeps blending and p5/CQ settings but can change compression efficiency and image quality.
These options default to off outside this supplied profile.
Import 4k120-to-60-av1.cfg in the GUI and select NVIDIA if multiple GPU devices appear.
GPU blending remains optional and is off by default; current tests do not show a speed benefit.
GUI previews refresh twice per second at a maximum width of 960 pixels; exported video stays full resolution.
Performance depends on the source and hardware; see README.md for the measured workload.
The optional GPU blending path requires a Vulkan 1.4 driver.
The runtime is private to this folder; no system Python/VS registration is needed.
For other weighting/gamma settings, use the same values as your original config.
The R82 package does not include the incompatible legacy SVP API3 plugins.
See README.md for capabilities and validation details.

Sources and licenses:
Blur: https://github.com/DragonAura/blur (GPL-3.0; LICENSE included)
VapourSynth R82: https://github.com/vapoursynth/vapoursynth (LGPL-2.1-or-later)
Python 3.12.10: https://www.python.org/downloads/release/python-31210/
FFmpeg build: https://github.com/GyanD/codexffmpeg/releases/tag/2025-08-14-git-cdbb5f1b93
Akarin: https://pypi.org/project/vapoursynth-akarin/1.5.0/
MVTools: https://pypi.org/project/vapoursynth-mvtools/29/
L-SMASH Works r1310 official x64 release: https://github.com/HomeOfAviSynthPlusEvolution/L-SMASH-Works/releases/tag/1310.0.0.0
MSVC runtime: app-local x64 redistributable from the build toolchain; see lib/licenses/MSVC-runtime/BUILD-PROVENANCE.txt
BestSource: https://github.com/vapoursynth/bestsource/releases/tag/R11
RIFE: https://github.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan
Adjust: https://github.com/f0e/Vapoursynth-adjust
"@ | Set-Content -LiteralPath (Join-Path $portableDir 'START-HERE.txt') -Encoding utf8

$required = @('blur-gui.exe', 'blur-cli.exe', 'lib/blur.py', 'lib/blur/blending.py', 'lib/blur/source.py',
    'lib/vapoursynth/python.exe', 'lib/vapoursynth/Lib/site-packages/vapoursynth/vspipe.exe',
    'lib/vapoursynth/Lib/site-packages/vapoursynth/plugins/LSMASHSource.dll',
    'lib/licenses/LSMASHSource-r1310/LICENSE', 'lib/licenses/MSVC-runtime/BUILD-PROVENANCE.txt',
    'lib/ffmpeg/ffmpeg.exe', 'lib/ffmpeg/ffprobe.exe')
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $portableDir $relative) -PathType Leaf)) { throw "Missing package file: $relative" }
}

# Fail packaging instead of shipping a plugin that only happens to load on the
# CI machine because Visual Studio installed its runtime globally.
foreach ($name in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
    $pythonCopy = Join-Path $portableDir "lib/vapoursynth/$name"
    $vspipeCopy = Join-Path $portableDir "lib/vapoursynth/Lib/site-packages/vapoursynth/$name"
    foreach ($path in @($pythonCopy, $vspipeCopy)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing app-local MSVC runtime: $path" }
    }
    if ((Get-FileHash -LiteralPath $pythonCopy -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $vspipeCopy -Algorithm SHA256).Hash) {
        throw "Mismatched app-local MSVC runtime copies: $name"
    }
}
$lsmashDll = Join-Path $portableDir 'lib/vapoursynth/Lib/site-packages/vapoursynth/plugins/LSMASHSource.dll'
if ((Get-FileHash -LiteralPath $lsmashDll -Algorithm SHA256).Hash -ne '0abac09524e56413eeb8b52119d8575fc14753492ce53e2133943b91e08452d6') {
    throw 'The package does not contain the verified official L-SMASH r1310 x64 DLL.'
}

$zipPath = Join-Path $bundleDir 'blur-Windows-GPU-R82-Portable-x64.zip'
Push-Location $stageRoot
try {
    & 7z a -tzip -mx=7 $zipPath 'blur-gpu-r82'
    if ($LASTEXITCODE -ne 0) { throw 'Portable zip creation failed' }
} finally { Pop-Location }

New-Item -ItemType Directory -Force -Path $installerDir, (Join-Path $installerDir 'resources'), (Join-Path $installerDir 'dependencies'), (Join-Path $installerDir 'redist') | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot 'installer/setup.iss'), (Join-Path $repoRoot 'installer/environment.iss') -Destination $installerDir
Copy-Item -Path (Join-Path $repoRoot 'installer/resources/*') -Destination (Join-Path $installerDir 'resources')
Copy-Item -LiteralPath (Join-Path $portableDir 'blur-gui.exe'), (Join-Path $portableDir 'blur-cli.exe') -Destination (Join-Path $installerDir 'resources')
Copy-Item -LiteralPath (Join-Path $portableDir 'lib') -Destination (Join-Path $installerDir 'dependencies') -Recurse
Copy-Item -LiteralPath (Join-Path $portableDir 'START-HERE.txt'), (Join-Path $portableDir '4k120-to-60-av1.cfg'), (Join-Path $portableDir 'LICENSE'), (Join-Path $portableDir 'README.md') -Destination (Join-Path $installerDir 'dependencies')
Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile (Join-Path $installerDir 'redist/VC_redist.x64.exe') -MaximumRetryCount 3
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = 'C:/Program Files (x86)/Inno Setup 6/ISCC.exe' }
if (-not (Test-Path -LiteralPath $iscc)) { throw 'Inno Setup 6 is required to package the installer' }
& $iscc "/O$bundleDir" '/Fblur-Windows-Installer-x64' '/DMyAppName=blur GPU R82' "/DMyAppVersion=$version" '/DMyAppPublisher=DragonAura' '/DMyAppURL=https://github.com/DragonAura/blur' '/DMyAppId={{8639D658-757A-4647-81D9-7AA168F026E5}' (Join-Path $installerDir 'setup.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }

Get-ChildItem -LiteralPath $bundleDir -File | Sort-Object Name | ForEach-Object {
    '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $_.Name
} | Set-Content -LiteralPath (Join-Path $bundleDir 'SHA256SUMS.txt') -Encoding ascii
