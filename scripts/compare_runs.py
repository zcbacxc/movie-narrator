#!/usr/bin/env python3
"""Compare two pipeline run metadata.json files for L2+ hand-test verification.

Usage:
    python scripts/compare_runs.py \
        --baseline output/l2-runs/baseline/metadata.json \
        --new output/l2-runs/v0426/metadata.json \
        --output comparison_report.md

Focus areas:
    --focus beat_anchor   Compare EP2 beat anchor fields + src_mid distribution
    --focus duck_curve    Compare EP6 duck curve / loudness fields
    --focus all           Compare everything (default)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_metadata(path: str) -> dict[str, Any]:
    """Load metadata.json from path."""
    p = Path(path)
    if not p.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def safe_get(d: dict, *keys, default=None) -> Any:
    """Safely traverse nested dict keys."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    return cur


def fmt_val(v: Any) -> str:
    """Format value for display."""
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4f}"
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, indent=2)
    if isinstance(v, list):
        return f"[{len(v)} items]"
    return str(v)


def compare_field(
    baseline: dict, new: dict, path: list[str], label: str
) -> str:
    """Compare a single field between baseline and new metadata."""
    b = safe_get(baseline, *path)
    n = safe_get(new, *path)

    diff = ""
    if isinstance(b, (int, float)) and isinstance(n, (int, float)):
        delta = n - b
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
        diff = f" ({arrow} {delta:+.4f})"

    return (
        f"| {label} | `{fmt_val(b)}` | `{fmt_val(n)}` |{diff} |"
    )


def compare_beat_anchor(baseline: dict, new: dict) -> str:
    """Compare EP2 beat anchor fields."""
    lines = [
        "## EP2 — Beat Time Anchor\n",
        "| Field | Baseline | New | Delta |",
        "|-------|----------|-----|-------|",
    ]

    ms_b = safe_get(baseline, "match_summary", default={})
    ms_n = safe_get(new, "match_summary", default={})

    lines.append(compare_field(
        baseline, new, ["match_summary", "beat_anchor"], "beat_anchor"
    ))
    lines.append(compare_field(
        baseline, new, ["match_summary", "beat_anchored_count"],
        "beat_anchored_count"
    ))
    lines.append(compare_field(
        baseline, new, ["match_summary", "segments"], "segments"
    ))

    # beats_meta comparison
    bm_b = baseline.get("beats_meta", [])
    bm_n = new.get("beats_meta", [])
    lines.append(f"| beats_meta length | {len(bm_b)} | {len(bm_n)} | |")

    # approx_ratio coverage
    ratio_b = sum(1 for b in bm_b if b.get("approx_ratio") is not None)
    ratio_n = sum(1 for b in bm_n if b.get("approx_ratio") is not None)
    lines.append(
        f"| approx_ratio coverage | {ratio_b}/{len(bm_b)} | "
        f"{ratio_n}/{len(bm_n)} | |"
    )

    # src_mid distribution from matches.json
    matches_b = baseline.get("_matches", [])
    matches_n = new.get("_matches", [])
    if matches_b and matches_n:
        lines.append("\n### src_mid distribution (from matches)\n")
        lines.append("| Percentile | Baseline | New | Delta |")
        lines.append("|------------|----------|-----|-------|")

        mids_b = sorted(
            [(safe_get(m, "src_start", default=0) +
             safe_get(m, "src_end", default=0)) / 2
             for m in matches_b if safe_get(m, "src_start") is not None]
        )
        mids_n = sorted(
            [(safe_get(m, "src_start", default=0) + 
             safe_get(m, "src_end", default=0)) / 2
             for m in matches_n if safe_get(m, "src_start") is not None]
        )

        if mids_b and mids_n:
            max_b = mids_b[-1] if mids_b[-1] > 0 else 1
            max_n = mids_n[-1] if mids_n[-1] > 0 else 1
            for pct in [0, 25, 50, 75, 100]:
                idx_b = min(int(len(mids_b) * pct / 100), len(mids_b) - 1)
                idx_n = min(int(len(mids_n) * pct / 100), len(mids_n) - 1)
                val_b = mids_b[idx_b] / max_b if max_b > 0 else 0
                val_n = mids_n[idx_n] / max_n if max_n > 0 else 0
                delta = val_n - val_b
                lines.append(
                    f"| {pct}% | {val_b:.3f} | {val_n:.3f} | {delta:+.3f} |"
                )

            # Uniformity check: if baseline is roughly linear (R² > 0.95)
            # and new deviates, EP2 is working
            lines.append("\n**判读**: 若基线 src_mid 分位接近线性（0→1 均匀），"
                        "而新版偏离线性（集中在高光区），则 EP2 beat anchor 生效。")

    return "\n".join(lines)


def compare_duck_curve(baseline: dict, new: dict) -> str:
    """Compare EP6 duck curve / loudness fields."""
    lines = [
        "## EP6 — Duck Curve + Loudnorm\n",
        "| Field | Baseline | New | Delta |",
        "|-------|----------|-----|-------|",
    ]

    # bgm_loudnorm
    lines.append(compare_field(
        baseline, new, ["bgm_loudnorm"], "bgm_loudnorm"
    ))

    # bgm_duck_db
    lines.append(compare_field(
        baseline, new, ["bgm_duck_db"], "bgm_duck_db"
    ))

    # mean_volume
    lines.append(compare_field(
        baseline, new, ["mean_volume"], "mean_volume (dB)"
    ))

    # audio_target_dbfs
    lines.append(compare_field(
        baseline, new, ["audio_target_dbfs"], "audio_target_dbfs"
    ))

    # status.bgm
    lines.append(compare_field(
        baseline, new, ["status", "bgm"], "status.bgm"
    ))

    # Check if loudnorm target is met
    mean_vol_n = safe_get(new, "mean_volume")
    if mean_vol_n is not None:
        if -16.0 <= mean_vol_n <= -12.0:
            lines.append(
                f"\n✅ 新版 mean_volume={mean_vol_n}dB 在 loudnorm "
                "目标范围 [-16, -12] dB 内。"
            )
        else:
            lines.append(
                f"\n⚠️ 新版 mean_volume={mean_vol_n}dB 超出 loudnorm "
                "目标范围 [-16, -12] dB。"
            )

    lines.append("\n**判读**: duck 曲线为比例闪避（代码内实现），无法从 metadata "
                "直接读取包络。需人工听感对比：基线 duck 固定 -10dB，"
                "新版在人声峰值时 duck 更深、句间抬起更自然。")

    return "\n".join(lines)


def compare_match_summary(baseline: dict, new: dict) -> str:
    """Compare match_summary fields."""
    lines = [
        "## Match Summary 对比\n",
        "| Field | Baseline | New | Delta |",
        "|-------|----------|-----|-------|",
    ]

    fields = [
        (["match_summary", "segments"], "segments"),
        (["match_summary", "embedding_ratio"], "embedding_ratio"),
        (["match_summary", "heuristic_ratio"], "heuristic_ratio"),
        (["match_summary", "score", "avg"], "score.avg"),
        (["match_summary", "score", "min"], "score.min"),
        (["match_summary", "score", "max"], "score.max"),
        (["match_summary", "speed_factor", "avg"], "speed_factor.avg"),
        (["match_summary", "speed_factor", "min"], "speed_factor.min"),
        (["match_summary", "speed_factor", "max"], "speed_factor.max"),
        (["match_summary", "low_score_fallback_count"],
         "low_score_fallback_count"),
        (["match_summary", "degraded_reason"], "degraded_reason"),
        (["match_summary", "timeline_mode"], "timeline_mode"),
        (["match_summary", "beat_anchor"], "beat_anchor"),
        (["match_summary", "beat_anchored_count"], "beat_anchored_count"),
    ]

    for path, label in fields:
        lines.append(compare_field(baseline, new, path, label))

    # source_counts
    sc_b = safe_get(baseline, "match_summary", "source_counts", default={})
    sc_n = safe_get(new, "match_summary", "source_counts", default={})
    if sc_b or sc_n:
        lines.append("\n### source_counts\n")
        lines.append("| Source | Baseline | New | Delta |")
        lines.append("|--------|----------|-----|-------|")
        for src in ["embedding", "heuristic", "fallback", "scene"]:
            b = sc_b.get(src, 0)
            n = sc_n.get(src, 0)
            delta = n - b
            lines.append(f"| {src} | {b} | {n} | {delta:+d} |")

    # diversity
    div_b = safe_get(baseline, "match_summary", "diversity", default={})
    div_n = safe_get(new, "match_summary", "diversity", default={})
    if div_b or div_n:
        lines.append("\n### diversity\n")
        lines.append("| Field | Baseline | New | Delta |")
        lines.append("|-------|----------|-----|-------|")
        for field in ["swaps", "window", "max_reuse"]:
            b = div_b.get(field, "—")
            n = div_n.get(field, "—")
            lines.append(f"| {field} | {b} | {n} | |")

    return "\n".join(lines)


def compare_basic(baseline: dict, new: dict) -> str:
    """Compare basic pipeline fields."""
    lines = [
        "## 基础信息\n",
        "| Field | Baseline | New | Delta |",
        "|-------|----------|-----|-------|",
    ]

    fields = [
        (["version"], "version"),
        (["final_mp4_size"], "final.mp4 size (bytes)"),
        (["final_mp4_duration"], "final.mp4 duration (s)"),
        (["mean_volume"], "mean_volume (dB)"),
        (["width"], "width"),
        (["height"], "height"),
        (["has_audio"], "has_audio"),
        (["has_video"], "has_video"),
    ]

    for path, label in fields:
        lines.append(compare_field(baseline, new, path, label))

    # status
    lines.append("\n### Pipeline Status\n")
    lines.append("| Step | Baseline | New | Match? |")
    lines.append("|------|----------|-----|--------|")
    sb = safe_get(baseline, "status", default={})
    sn = safe_get(new, "status", default={})
    for step in ["research", "align", "scene", "match", "bgm", "export",
                 "translate"]:
        b = sb.get(step, "—")
        n = sn.get(step, "—")
        match = "✅" if b == n else "⚠️"
        lines.append(f"| {step} | {b} | {n} | {match} |")

    return "\n".join(lines)


def compare_ep_params(baseline: dict, new: dict) -> str:
    """Compare EP-specific params."""
    lines = [
        "## EP 参数对比\n",
        "| Param | Baseline | New | Delta |",
        "|-------|----------|-----|-------|",
    ]

    params = [
        "hook_templates",
        "set_pieces",
        "render_title_card_sec",
        "bgm_loudnorm",
        "bgm_duck_db",
        "bgm_normalize",
        "audio_target_dbfs",
        "vision_captioner",
        "match_topk",
        "match_topk_reuse_penalty",
        "match_timeline_mode",
    ]

    for p in params:
        b = baseline.get(p)
        n = new.get(p)
        if b is None and n is None:
            continue

        if isinstance(b, list) and isinstance(n, list):
            b_str = f"[{len(b)} items]"
            n_str = f"[{len(n)} items]"
            delta = f"{len(n) - len(b):+d}"
        elif isinstance(b, bool) or isinstance(n, bool):
            b_str = str(b) if b is not None else "—"
            n_str = str(n) if n is not None else "—"
            delta = "" if b == n else "CHANGED"
        elif isinstance(b, (int, float)) and isinstance(n, (int, float)):
            b_str = str(b)
            n_str = str(n)
            delta = f"{n - b:+.4f}"
        else:
            b_str = fmt_val(b)
            n_str = fmt_val(n)
            delta = "" if b == n else "CHANGED"

        lines.append(f"| `{p}` | {b_str} | {n_str} | {delta} |")

    return "\n".join(lines)


def generate_report(
    baseline: dict, new: dict, focus: str, baseline_path: str, new_path: str
) -> str:
    """Generate full comparison report."""
    lines = [
        "# L2+ Hand-Test Comparison Report\n",
        f"- Baseline: `{baseline_path}`",
        f"- New: `{new_path}`",
        f"- Focus: `{focus}`",
        "",
        "---\n",
    ]

    if focus in ("all", "basic"):
        lines.append(compare_basic(baseline, new))
        lines.append("")

    if focus in ("all", "match"):
        lines.append(compare_match_summary(baseline, new))
        lines.append("")

    if focus in ("all", "beat_anchor"):
        lines.append(compare_beat_anchor(baseline, new))
        lines.append("")

    if focus in ("all", "duck_curve"):
        lines.append(compare_duck_curve(baseline, new))
        lines.append("")

    if focus in ("all", "ep_params"):
        lines.append(compare_ep_params(baseline, new))
        lines.append("")

    # Summary verdict
    lines.append("---\n")
    lines.append("## 自动判读\n")

    verdicts = []

    # Check EP2
    beat_anchor_n = safe_get(new, "match_summary", "beat_anchor")
    if beat_anchor_n is True:
        verdicts.append("✅ EP2: beat_anchor=true — beat 时间锚已激活")
    elif beat_anchor_n is False:
        verdicts.append("⚠️ EP2: beat_anchor=false — LLM 未返回 approx_ratio，"
                       "回退到 timeline_mode")
    else:
        verdicts.append("— EP2: beat_anchor 字段不存在（版本 < v0.4.26?）")

    # Check EP4
    hook_n = new.get("hook_templates")
    if hook_n and len(hook_n) > 0:
        verdicts.append(f"✅ EP4: hook_templates 配置了 {len(hook_n)} 条模板")
    else:
        verdicts.append("— EP4: hook_templates 未配置或为空")

    # Check EP5
    title_card_n = new.get("render_title_card_sec")
    if title_card_n and title_card_n > 0:
        verdicts.append(f"✅ EP5: render_title_card_sec={title_card_n}s — "
                       "标题卡已启用")
    else:
        verdicts.append("— EP5: render_title_card_sec=0 — 标题卡未启用")

    # Check EP6
    loudnorm_n = new.get("bgm_loudnorm")
    if loudnorm_n is True:
        verdicts.append("✅ EP6: bgm_loudnorm=true — RMS 响度归一化已启用")
    else:
        verdicts.append("— EP6: bgm_loudnorm=false — 使用峰值归一化")

    for v in verdicts:
        lines.append(f"- {v}")

    lines.append("")
    lines.append("> 以上为 metadata 层自动判读。主观观感（S/E 项）需人工填写"
                 "L2_HANDTEST_PLUS checklist。")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two pipeline run metadata.json files "
                    "for L2+ hand-test verification."
    )
    parser.add_argument(
        "--baseline", required=True,
        help="Path to baseline metadata.json"
    )
    parser.add_argument(
        "--new", required=True,
        help="Path to new metadata.json"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path (default: stdout)"
    )
    parser.add_argument(
        "--focus", default="all",
        choices=["all", "basic", "match", "beat_anchor", "duck_curve",
                 "ep_params"],
        help="Focus area for comparison (default: all)"
    )

    args = parser.parse_args()

    baseline = load_metadata(args.baseline)
    new = load_metadata(args.new)

    report = generate_report(
        baseline, new, args.focus, args.baseline, args.new
    )

    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"Report written to: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
