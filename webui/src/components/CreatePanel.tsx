import { useState } from "react"
import { Sparkles, Loader2 } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { MovieSection } from "@/components/MovieSection"
import { VoiceSection } from "@/components/VoiceSection"
import { AssetsSection } from "@/components/AssetsSection"
import { SubtitlesSection } from "@/components/SubtitlesSection"
import { AdvancedSection } from "@/components/AdvancedSection"
import { useTask } from "@/hooks/useTask"
import type { FormSubmitData } from "@/types"

const DEFAULT_FORM: FormSubmitData = {
  movie: "",
  style: "热血搞笑",
  duration: 60,
  voice: "",
  format: "16:9",
  library_dir: "",
  research: false,
  no_bgm: false,
  no_clips: false,
  strict: false,
  subtitle_lang: "",
  subtitle_mode: "original",
  translate_provider: "",
}

export function CreatePanel() {
  const { startTask } = useTask()
  const [formData, setFormData] = useState<FormSubmitData>(DEFAULT_FORM)
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [bgmFile, setBgmFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const handleChange = (field: keyof FormSubmitData, value: string | number | boolean | undefined) => {
    setFormData((prev) => ({ ...prev, [field]: value }))
  }

  const handleSubmit = async () => {
    if (!formData.movie.trim()) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      await startTask(formData, videoFile ?? undefined, bgmFile ?? undefined)
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "创建任务失败")
    } finally {
      setSubmitting(false)
    }
  }

  const canSubmit = formData.movie.trim().length > 0 && !submitting

  return (
    <div className="mx-auto max-w-2xl px-4 py-8">
      <Card>
        <CardHeader>
          <CardTitle>创建解说任务</CardTitle>
          <CardDescription>
            填写电影信息和解说参数，一键生成电影解说视频
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Movie section */}
          <MovieSection data={formData} onChange={handleChange} />

          <Separator />

          {/* Voice section */}
          <VoiceSection data={formData} onChange={handleChange} />

          <Separator />

          {/* Assets section (accordion) */}
          <AssetsSection
            data={formData}
            videoFile={videoFile}
            bgmFile={bgmFile}
            onChange={handleChange}
            onVideoChange={setVideoFile}
            onBgmChange={setBgmFile}
          />

          <Separator />

          {/* Subtitles section (accordion) */}
          <SubtitlesSection data={formData} onChange={handleChange} />

          <Separator />

          {/* Advanced section (accordion) */}
          <AdvancedSection data={formData} onChange={handleChange} />

          {/* Error message */}
          {submitError && (
            <div className="rounded-input border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-400">
              {submitError}
            </div>
          )}

          {/* Submit button */}
          <Button
            size="lg"
            className="w-full text-base"
            disabled={!canSubmit}
            onClick={handleSubmit}
          >
            {submitting ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" strokeWidth={1.5} />
                正在创建任务...
              </>
            ) : (
              <>
                <Sparkles className="mr-2 h-4 w-4" strokeWidth={1.5} />
                生成解说视频
              </>
            )}
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
