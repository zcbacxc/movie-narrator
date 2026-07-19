#!/usr/bin/env bash
# L2 G1 Golden Sample — Bash runner
# Usage: ./run_g1.example.sh --video /path/to/film.mp4 --bgm /path/to/track.mp3 --movie "电影名"

set -euo pipefail

VIDEO=""
BGM=""
MOVIE=""
STYLE="热血搞笑"
DURATION=60
PRESET="douyin-fast"
FORMAT="16:9"

while [[ $# -gt 0 ]]; do
    case $1 in
        --video) VIDEO="$2"; shift 2 ;;
        --bgm) BGM="$2"; shift 2 ;;
        --movie) MOVIE="$2"; shift 2 ;;
        --style) STYLE="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --preset) PRESET="$2"; shift 2 ;;
        --format) FORMAT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$VIDEO" || -z "$BGM" || -z "$MOVIE" ]]; then
    echo "Usage: $0 --video <path> --bgm <path> --movie <name>"
    exit 1
fi

echo "=== L2 G1 Golden Sample ==="
echo "Movie:    $MOVIE"
echo "Video:    $VIDEO"
echo "BGM:      $BGM"
echo "Preset:   $PRESET"
echo "Duration: ${DURATION}s"
echo "Format:   $FORMAT"
echo ""

mn create \
    --movie "$MOVIE" \
    --style "$STYLE" \
    --duration "$DURATION" \
    --format "$FORMAT" \
    --video "$VIDEO" \
    --bgm "$BGM" \
    -p "$PRESET" \
    --config examples/l2/job.l2.douyin.yaml \
    --keep-cache

echo ""
echo "=== Pipeline completed ==="
echo "Check output/$MOVIE/ for deliverables"
echo "Fill in docs/checklists/L2_HANDTEST.md"
