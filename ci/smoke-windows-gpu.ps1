$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$portableDir = Join-Path $repoRoot 'build/package-windows/blur-gpu-r82'
$smokeDir = Join-Path $repoRoot 'build/smoke-windows'
New-Item -ItemType Directory -Force -Path $smokeDir | Out-Null
$inputPath = Join-Path $smokeDir 'input.mp4'
$outputPath = Join-Path $smokeDir 'output.mp4'
$configPath = Join-Path $smokeDir 'test.cfg'
@'
blur: true
blur amount: 1
blur output fps: 60
blur weighting: equal
blur gamma: 1
interpolate: false
pre-interpolate: false
deduplicate: false
gpu blending: true
gpu decoding: false
fast decoding: true
gpu interpolation: false
gpu encoding: false
preview: false
'@ | Set-Content -LiteralPath $configPath -Encoding utf8
$ffmpeg = Join-Path $portableDir 'lib/ffmpeg/ffmpeg.exe'
$ffprobe = Join-Path $portableDir 'lib/ffmpeg/ffprobe.exe'
& $ffmpeg -hide_banner -loglevel error -y -f lavfi -i 'testsrc2=size=320x180:rate=240' -frames:v 48 -c:v libx264 -preset ultrafast $inputPath
if ($LASTEXITCODE -ne 0) { throw 'Synthetic input generation failed' }
& (Join-Path $portableDir 'blur-cli.exe') -i $inputPath -o $outputPath -c $configPath -v
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $outputPath)) { throw 'Packaged CLI render failed' }
$result = & $ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,nb_frames -of json $outputPath | ConvertFrom-Json
$stream = $result.streams[0]
if ($stream.width -ne 320 -or $stream.height -ne 180 -or $stream.r_frame_rate -ne '60/1' -or $stream.nb_frames -ne '12') {
    throw "Unexpected output stream: $($stream | ConvertTo-Json -Compress)"
}
Write-Host 'Packaged CLI smoke test passed: 48 frames at 240fps -> 12 frames at 60fps.'
