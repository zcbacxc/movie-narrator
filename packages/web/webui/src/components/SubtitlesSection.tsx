import { Captions, Languages } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import type { FormSubmitData } from "@/types"

interface SubtitlesSectionProps {
  data: FormSubmitData
  onChange: (field: keyof FormSubmitData, value: string | number | boolean) => void
}

export function SubtitlesSection({ data, onChange }: SubtitlesSectionProps) {
  return (
    <Accordion type="single" collapsible>
      <AccordionItem value="subtitles" className="border-b-0">
        <AccordionTrigger>字幕设置</AccordionTrigger>
        <AccordionContent className="space-y-5">
          {/* Subtitle language */}
          <div className="space-y-2">
            <Label className="flex items-center gap-1.5 text-slate-200">
              <Languages className="h-4 w-4 text-pink-500" strokeWidth={1.5} />
              字幕语言代码
            </Label>
            <Input
              value={data.subtitle_lang}
              onChange={(e) => onChange("subtitle_lang", e.target.value)}
              placeholder="如 zh、en（留空则不启用多语言字幕）"
            />
            <p className="text-xs text-slate-400">
              设置后启用翻译字幕功能，将自动翻译字幕到指定语言
            </p>
          </div>

          {/* Subtitle mode */}
          <div className="space-y-2">
            <Label className="flex items-center gap-1.5 text-slate-200">
              <Captions className="h-4 w-4 text-pink-500" strokeWidth={1.5} />
              字幕模式
            </Label>
            <div className="flex gap-2">
              {([
                { value: "original", label: "仅原文" },
                { value: "translated", label: "仅译文" },
                { value: "bilingual", label: "双语" },
              ] as const).map((mode) => (
                <button
                  key={mode.value}
                  type="button"
                  onClick={() => onChange("subtitle_mode", mode.value)}
                  className={`flex-1 rounded-input border px-3 py-2 text-sm font-medium transition-colors ${
                    data.subtitle_mode === mode.value
                      ? "border-pink-500 bg-pink-500/10 text-pink-400"
                      : "border-border bg-surface text-slate-300 hover:bg-slate-700/50"
                  }`}
                >
                  {mode.label}
                </button>
              ))}
            </div>
          </div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  )
}
