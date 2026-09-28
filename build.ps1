<#
.SYNOPSIS
    Builds the packaged Windows .exe for the Batch H.264 Encoder.

.DESCRIPTION
    Runs PyInstaller against video_batch.spec, then copies a separately
    obtained ffmpeg build (with libx264 + libvmaf enabled) into the output
    folder so the packaged app finds it next to the .exe at runtime
    (see video_batch/core/ffmpeg_locate.py).

    This script does NOT download ffmpeg. Point -FfmpegDir at a folder
    containing ffmpeg.exe and ffprobe.exe (e.g. a gyan.dev or BtbN
    "full"/"shared" Windows build that includes libvmaf).

.PARAMETER FfmpegDir
    Path to a folder containing ffmpeg.exe and ffprobe.exe. If omitted, the
    build proceeds without bundling ffmpeg -- the packaged app will still
    look for ffmpeg on PATH at runtime, but won't be self-contained.

.EXAMPLE
    ./build.ps1 -FfmpegDir "D:\ffmpeg-full-build\bin"
#>
param(
    [string]$FfmpegDir = ""
)

$ErrorActionPreference = "Stop"

Write-Host "Running PyInstaller..."
pyinstaller video_batch.spec --noconfirm
if (-not $?) { throw "PyInstaller build failed." }

$distDir = Join-Path (Get-Location) "dist\BatchH264Encoder"

if ($FfmpegDir -ne "") {
    $ffmpegExe = Join-Path $FfmpegDir "ffmpeg.exe"
    $ffprobeExe = Join-Path $FfmpegDir "ffprobe.exe"
    if (-not (Test-Path $ffmpegExe) -or -not (Test-Path $ffprobeExe)) {
        throw "ffmpeg.exe / ffprobe.exe not found in $FfmpegDir"
    }

    $bundledFfmpegDir = Join-Path $distDir "ffmpeg"
    New-Item -ItemType Directory -Force -Path $bundledFfmpegDir | Out-Null
    Copy-Item $ffmpegExe -Destination $bundledFfmpegDir -Force
    Copy-Item $ffprobeExe -Destination $bundledFfmpegDir -Force
    Write-Host "Bundled ffmpeg from $FfmpegDir into $bundledFfmpegDir"
} else {
    Write-Host "No -FfmpegDir given: ffmpeg.exe/ffprobe.exe were NOT bundled." -ForegroundColor Yellow
    Write-Host "The packaged app will only find ffmpeg if it's on the target machine's PATH." -ForegroundColor Yellow
}

Write-Host "Build complete: $distDir\BatchH264Encoder.exe"
