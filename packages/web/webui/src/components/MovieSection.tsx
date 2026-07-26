import { Clapperboard, Palette, Clock, Monitor } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import type { FormSubmitData } from "@/types"

interface MovieSectionProps {
  data: FormSubmitData
  onChange: (field: keyof FormSubmitData, value: string | number | boolean) => void
}

export function MovieSection({ data, onChange }: MovieSectionProps) {
  return (
    <div className="space-y-5">
      {/* Movie name */}
      <div className="space-y-2">
        <Label className="flex items-center gap-1.5 text-slate-200">
          <Clapperboard className="h-4 w-4 text-pink-500" strokeWidth={1.5} />
          电影名称 <span className="text-pink-500">*</span>
        </Label>
        <Input
          value={data.movie}
          onChange={(e) => onChange("movie", e.target.value)}
          placeholder="输入电影名称，如：肖申克的救赎"
          className="h-12 text-base"
        />
      </div>

      {/* Style */}
      <div className="space-y-2">
        <Label className="flex items-center gap-1.5 text-slate-200">
          <Palette className="h-4 w-4 text-pink-500" strokeWidth={1.5} />
          解说风格
        </Label>
        <Input
          value={data.style}
          onChange={(e) => onChange("style", e.target.value)}
          placeholder="热血搞笑"
        />
      </div>

      {/* Duration */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label className="flex items-center gap-1.5 text-slate-200">
            <Clock className="h-4 w-4 text-pink-500" strokeWidth={1.5} />
            目标时长
          </Label>
          <span className="text-sm font-medium text-pink-400">
            {Math.floor(data.duration / 60)}:{String(data.duration % 60).padStart(2, "0")}
          </span>
        </div>
        <Slider
          value={data.duration}
          min={30}
          max={300}
          step={5}
          onValueChange={(v) => onChange("duration", v)}
        />
        <div className="flex justify-between text-xs text-slate-400">
          <span>30s</span>
          <span>300s</span>
        </div>
      </div>

      {/* Format */}
      <div className="space-y-2">
        <Label className="flex items-center gap-1.5 text-slate-200">
          <Monitor className="h-4 w-4 text-pink-500" strokeWidth={1.5} />
          视频比例
        </Label>
        <div className="flex gap-2">
          {(["16:9", "9:16"] as const).map((fmt) => (
            <button
              key={fmt}
              type="button"
              onClick={() => onChange("format", fmt)}
              className={`flex flex-1 items-center justify-center gap-2 rounded-input border px-4 py-2.5 text-sm font-medium transition-colors ${
                data.format === fmt
                  ? "border-pink-500 bg-pink-500/10 text-pink-400"
                  : "border-border bg-surface text-slate-300 hover:bg-slate-700/50"
              }`}
            >
              <span className="font-mono">{fmt}</span>
              {fmt === "16:9" ? "横屏" : "竖屏"}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
