import { Mic } from "lucide-react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import type { FormSubmitData } from "@/types"

interface VoiceSectionProps {
  data: FormSubmitData
  onChange: (field: keyof FormSubmitData, value: string | number | boolean) => void
}

export function VoiceSection({ data, onChange }: VoiceSectionProps) {
  return (
    <div className="space-y-2">
      <Label className="flex items-center gap-1.5 text-slate-200">
        <Mic className="h-4 w-4 text-pink-500" strokeWidth={1.5} />
        语音名称
      </Label>
      <Input
        value={data.voice}
        onChange={(e) => onChange("voice", e.target.value)}
        placeholder="zh-CN-YunxiNeural（留空使用默认语音）"
      />
      <p className="text-xs text-slate-400">
        使用 Edge TTS 语音名称，如 zh-CN-YunxiNeural、zh-CN-XiaoxiaoNeural 等
      </p>
    </div>
  )
}
