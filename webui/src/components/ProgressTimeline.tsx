import type { ReactNode } from "react"
import { Check, X, Loader2, Minus } from "lucide-react"
import { Progress } from "@/components/ui/progress"
import { PIPELINE_STEPS } from "@/types"
import { cn } from "@/lib/utils"

const STEP_LABELS: Record<string, string> = {
  resolve_video: "解析视频",
  prepare_assets: "准备素材",
  research_plot: "剧情研究",
  generate_script: "生成文案",
  export_script_md: "导出文案",
  generate_voice: "语音合成",
  align_audio: "音频对齐",
  detect_scenes: "场景检测",
  match_clips: "片段匹配",
  mix_bgm: "混音配乐",
  translate_subtitles: "翻译字幕",
  generate_subtitle: "生成字幕",
  render_video: "渲染输出",
  validate_deliverable: "成片质检",
  export_clips: "导出片段",
}

type StepState = "pending" | "active" | "done" | "failed" | "skipped"

function getStepStates(
  currentStep: string,
  status: string
): StepState[] {
  const currentIdx = PIPELINE_STEPS.indexOf(currentStep as (typeof PIPELINE_STEPS)[number])

  return PIPELINE_STEPS.map((_step, idx) => {
    // Step not found in list — all pending
    if (currentIdx === -1) {
      if (status === "done") return "done"
      return "pending"
    }

    if (status === "done") return "done"

    if (idx < currentIdx) return "done"

    if (idx === currentIdx) {
      if (status === "failed") return "failed"
      if (status === "cancelled") return "skipped"
      return "active"
    }

    // idx > currentIdx
    if (status === "failed" || status === "cancelled") return "skipped"
    return "pending"
  })
}

function getProgressPercent(states: StepState[]): number {
  const done = states.filter((s) => s === "done").length
  return Math.round((done / states.length) * 100)
}

const STATE_STYLES: Record<StepState, { dot: string; label: string; icon: ReactNode }> = {
  pending: {
    dot: "border-slate-600 bg-slate-700",
    label: "text-slate-500",
    icon: <Minus className="h-3 w-3 text-slate-500" strokeWidth={2} />,
  },
  active: {
    dot: "border-pink-500 bg-pink-500",
    label: "text-pink-400",
    icon: <Loader2 className="h-3 w-3 text-white animate-spin" strokeWidth={2} />,
  },
  done: {
    dot: "border-green-500 bg-green-500",
    label: "text-green-400",
    icon: <Check className="h-3 w-3 text-white" strokeWidth={2.5} />,
  },
  failed: {
    dot: "border-red-500 bg-red-500",
    label: "text-red-400",
    icon: <X className="h-3 w-3 text-white" strokeWidth={2.5} />,
  },
  skipped: {
    dot: "border-slate-600 bg-slate-700",
    label: "text-slate-500",
    icon: <span className="text-[10px] leading-none text-slate-500">...</span>,
  },
}

interface ProgressTimelineProps {
  currentStep: string
  status: string
}

export function ProgressTimeline({ currentStep, status }: ProgressTimelineProps) {
  const states = getStepStates(currentStep, status)
  const percent = getProgressPercent(states)

  return (
    <div className="space-y-4">
      {/* Progress bar */}
      <div className="space-y-1.5">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium text-slate-200">流水线进度</span>
          <span className="font-mono text-pink-400">{percent}%</span>
        </div>
        <Progress value={percent} />
      </div>

      {/* Step dots */}
      <div className="flex flex-wrap gap-3">
        {PIPELINE_STEPS.map((step, idx) => {
          const state = states[idx]
          const style = STATE_STYLES[state]
          const isActive = state === "active"

          return (
            <div
              key={step}
              className="flex flex-col items-center gap-1.5"
              style={{ minWidth: "72px" }}
            >
              <div
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-full border-2 transition-all",
                  style.dot,
                  isActive && "animate-step-pulse ring-4 ring-pink-500/20"
                )}
              >
                {style.icon}
              </div>
              <span
                className={cn(
                  "text-center text-xs font-medium leading-tight",
                  style.label
                )}
              >
                {STEP_LABELS[step] ?? step}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
