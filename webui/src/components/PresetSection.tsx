import { Wand2 } from "lucide-react"
import { Label } from "@/components/ui/label"
import { NARRATION_PRESETS } from "@/types"
import type { FormSubmitData } from "@/types"

interface PresetSectionProps {
  data: FormSubmitData
  onChange: (field: keyof FormSubmitData, value: string | number | boolean) => void
}

export function PresetSection({ data, onChange }: PresetSectionProps) {
  return (
    <div className="space-y-2">
      <Label className="flex items-center gap-1.5 text-slate-200">
        <Wand2 className="h-4 w-4 text-pink-500" strokeWidth={1.5} />
        解说预设
      </Label>
      <div className="grid grid-cols-2 gap-2">
        {NARRATION_PRESETS.map((preset) => (
          <button
            key={preset.value}
            type="button"
            onClick={() => onChange("narration_preset", preset.value)}
            className={`flex flex-col items-start gap-0.5 rounded-input border px-3 py-2.5 text-left text-sm transition-colors ${
              data.narration_preset === preset.value
                ? "border-pink-500 bg-pink-500/10 text-pink-400"
                : "border-border bg-surface text-slate-300 hover:bg-slate-700/50"
            }`}
          >
            <span className="font-medium">{preset.label}</span>
            <span className="text-xs text-slate-400">{preset.description}</span>
          </button>
        ))}
      </div>
      <p className="text-xs text-slate-400">
        预设自动调整句数、切镜节奏和 BGM 闪避参数。留空使用默认值。
      </p>
    </div>
  )
}
