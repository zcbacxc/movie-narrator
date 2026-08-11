# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

<#
.SYNOPSIS
  Detect the concrete version and origin of ffmpeg on the current PATH,
  and whether a crippled (partial) build is hit.

.DESCRIPTION
  movie-narrator's render.py uses shutil.which("ffmpeg") directly for the
  cover-frame extraction and the final audio mux (it takes the FIRST ffmpeg
  on PATH), while the main render path goes through the imageio-ffmpeg
  full build. If the first PATH entry is a crippled build (e.g. Trae's
  bundled minimal ffmpeg), the cover is silently dropped or the final mux
  hard-fails.

  This script:
    1. Lists every ffmpeg on PATH in resolution order
    2. Deep-checks the first one (what shutil.which would hit)
    3. Probes key capabilities (WAV input, sidechaincompress filter,
       aac encoder) to flag a crippled build
    4. Compares with the imageio-ffmpeg build (what MoviePy renders with)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/check_ffmpeg.ps1
#>

$ErrorActionPreference = 'Continue'

function Write-Step($title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

function Test-FfmpegCapability($exe, $Label) {
    # 1) WAV input support (crippled builds often lack it)
    $wavOk = $false
    try {
        & $exe -y -loglevel error -f lavfi -i "sine=frequency=440:duration=0.1" -f wav - 2>$null | Out-Null
        $wavOk = ($LASTEXITCODE -eq 0)
    } catch { $wavOk = $false }

    # 2) sidechaincompress filter (audio_mix._ffmpeg_bin depends on it)
    $hasSidechain = $false
    try {
        $filters = & $exe -hide_banner -filters 2>&1
        $hasSidechain = (@($filters -match 'sidechaincompress').Count -gt 0)
    } catch { $hasSidechain = $false }

    # 3) aac encoder (default audio codec for mux)
    $hasAac = $false
    try {
        $encoders = & $exe -hide_banner -encoders 2>&1
        $hasAac = (@($encoders -match '\baac\b').Count -gt 0)
    } catch { $hasAac = $false }

    $wavTxt = if ($wavOk) { 'OK' } else { 'FAIL/missing' }
    $scTxt = if ($hasSidechain) { 'yes' } else { 'no' }
    $aacTxt = if ($hasAac) { 'yes' } else { 'no' }

    Write-Host "  [$Label]"
    Write-Host "    WAV input          : $wavTxt"
    Write-Host "    sidechaincompress  : $scTxt"
    Write-Host "    aac encoder        : $aacTxt"

    $missing = @()
    if (-not $wavOk) { $missing += 'WAV-input' }
    if (-not $hasSidechain) { $missing += 'sidechaincompress' }
    if (-not $hasAac) { $missing += 'aac' }
    if ($missing.Count -gt 0) {
        Write-Host "    >>> SUSPECT crippled build; missing: $($missing -join ', ')" -ForegroundColor Yellow
    } else {
        Write-Host "    >>> capabilities complete" -ForegroundColor Green
    }
}

# ---------- 1. every ffmpeg on PATH ----------
Write-Step "1. All ffmpeg on PATH (resolution order)"
$all = @(where.exe ffmpeg 2>$null)
if ($all.Count -eq 0) {
    Write-Host "  No ffmpeg found on PATH." -ForegroundColor Yellow
} else {
    $idx = 0
    foreach ($p in $all) {
        $idx++
        $tag = if ($idx -eq 1) { "  <<< first one shutil.which would hit" } else { "" }
        Write-Host "  [$idx] $p$tag"
    }
}

# ---------- 2. deep-check the first one ----------
Write-Step "2. First ffmpeg (what shutil.which hits) version & origin"
$first = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $first) {
    Write-Host "  No ffmpeg found." -ForegroundColor Red
} else {
    $exe = $first.Source
    Write-Host "  path: $exe"
    try {
        $verLine = @(& $exe -hide_banner -version 2>&1) | Select-Object -First 3
        $verLine | ForEach-Object { Write-Host "  $_" }
    } catch {
        Write-Host "  cannot execute $exe" -ForegroundColor Red
    }
    try {
        $prefix = @(& $exe -hide_banner -buildconf 2>&1) | Select-String '--prefix='
        if ($prefix) { Write-Host "  build prefix : $($prefix.Line.Trim())" }
    } catch { }
    Test-FfmpegCapability $exe "PATH#1"

    # 3. check the remaining candidates too
    if ($all.Count -gt 1) {
        Write-Step "3. Remaining ffmpeg candidates"
        for ($i = 1; $i -lt $all.Count; $i++) {
            Test-FfmpegCapability $all[$i] "PATH#$($i+1)"
        }
    }
}

# ---------- 4. imageio-ffmpeg (what MoviePy renders with) ----------
Write-Step "4. imageio-ffmpeg (MoviePy main render path)"
try {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        $result = @(python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe()); print(imageio_ffmpeg.get_ffmpeg_version())" 2>&1)
        $result | ForEach-Object { Write-Host "  $_" }
        $imgExe = $result[0]
        if ($imgExe -and (Test-Path $imgExe)) {
            Test-FfmpegCapability $imgExe "imageio"
        }
    } else {
        Write-Host "  python not available; skipping imageio check" -ForegroundColor Yellow
    }
} catch {
    Write-Host "  imageio-ffmpeg check failed (maybe [full]/[ml] deps not installed)" -ForegroundColor Yellow
}

# ---------- 5. conclusion ----------
Write-Step "5. Conclusion"
if (-not $all) {
    Write-Host "  No ffmpeg on PATH -> render mux will raise RuntimeError (fix: wire in ffmpeg_bin() fallback)" -ForegroundColor Yellow
} else {
    $firstExe = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
    Write-Host "  shutil.which hits: $firstExe"
    Write-Host "  -> If that path is flagged 'SUSPECT crippled build' above, render cover/mux are affected."
    Write-Host "     Wrapping with ffmpeg_bin() alone will NOT help (it is also which-first);"
    Write-Host "     you need imageio-first resolution or fix the PATH order."
}
