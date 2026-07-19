# L2 G1 Golden Sample — PowerShell runner
# Usage: .\run_g1.example.ps1 -Video "D:\movies\film.mp4" -Bgm "D:\bgm\track.mp3" -Movie "电影名"

param(
    [Parameter(Mandatory=$true)]
    [string]$Video,
    [Parameter(Mandatory=$true)]
    [string]$Bgm,
    [Parameter(Mandatory=$true)]
    [string]$Movie,
    [string]$Style = "热血搞笑",
    [int]$Duration = 60,
    [string]$Preset = "douyin-fast",
    [string]$Format = "16:9"
)

Write-Host "=== L2 G1 Golden Sample ===" -ForegroundColor Cyan
Write-Host "Movie:    $Movie"
Write-Host "Video:    $Video"
Write-Host "BGM:      $Bgm"
Write-Host "Preset:   $Preset"
Write-Host "Duration: ${Duration}s"
Write-Host "Format:   $Format"
Write-Host ""

mn create `
    --movie $Movie `
    --style $Style `
    --duration $Duration `
    --format $Format `
    --video $Video `
    --bgm $Bgm `
    -p $Preset `
    --config examples/l2/job.l2.douyin.yaml `
    --keep-cache

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=== Pipeline completed ===" -ForegroundColor Green
    Write-Host "Check output/$Movie/ for deliverables"
    Write-Host "Fill in docs/checklists/L2_HANDTEST.md"
} else {
    Write-Host ""
    Write-Host "=== Pipeline FAILED (exit $LASTEXITCODE) ===" -ForegroundColor Red
}
