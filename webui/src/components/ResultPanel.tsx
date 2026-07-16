import {
  CheckCircle2,
  XCircle,
  Ban,
  Download,
  PlusCircle,
  PlayCircle,
  FileArchive,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { useTask } from "@/hooks/useTask"
import { getArtifactUrl } from "@/lib/api"

export function ResultPanel() {
  const { taskId, status, artifacts, error, resetTask } = useTask()

  if (!taskId) return null

  const artifactUrl = getArtifactUrl(taskId)
  const hasArtifacts = artifacts && artifacts.length > 0

  // Done
  if (status === "done") {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-green-400">
          <CheckCircle2 className="h-5 w-5" strokeWidth={1.5} />
          <span className="text-lg font-semibold">任务完成</span>
        </div>

        {/* Video player */}
        <div className="overflow-hidden rounded-card border border-border bg-black">
          <video
            controls
            className="mx-auto max-h-[480px] w-full"
            src={artifactUrl}
          >
            您的浏览器不支持视频播放。
          </video>
        </div>

        {/* Artifacts download */}
        {hasArtifacts && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-slate-200">下载制品</h3>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" asChild>
                <a href={artifactUrl} download>
                  <Download className="mr-1.5 h-4 w-4" strokeWidth={1.5} />
                  下载全部制品 ({artifacts!.length} 个文件)
                </a>
              </Button>
              {artifacts!.map((path, idx) => (
                <Button key={idx} variant="ghost" size="sm" asChild>
                  <a href={artifactUrl} download>
                    <FileArchive className="mr-1.5 h-4 w-4" strokeWidth={1.5} />
                    {path.split(/[\\/]/).pop() || `制品 ${idx + 1}`}
                  </a>
                </Button>
              ))}
            </div>
          </div>
        )}

        <Button variant="default" size="lg" className="w-full" onClick={resetTask}>
          <PlusCircle className="mr-2 h-4 w-4" strokeWidth={1.5} />
          新建任务
        </Button>
      </div>
    )
  }

  // Failed
  if (status === "failed") {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-red-400">
          <XCircle className="h-5 w-5" strokeWidth={1.5} />
          <span className="text-lg font-semibold">任务失败</span>
        </div>

        {error && (
          <div className="rounded-card border border-red-500/30 bg-red-500/10 p-4">
            <p className="text-xs leading-relaxed text-red-300">
              <span className="font-semibold">错误信息：</span>
            </p>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap font-mono text-xs text-red-300">
              {error}
            </pre>
          </div>
        )}

        {/* Partial artifacts */}
        {hasArtifacts && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-slate-200">部分制品下载</h3>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" asChild>
                <a href={artifactUrl} download>
                  <Download className="mr-1.5 h-4 w-4" strokeWidth={1.5} />
                  下载制品 ({artifacts!.length} 个文件)
                </a>
              </Button>
            </div>
          </div>
        )}

        <Button variant="default" size="lg" className="w-full" onClick={resetTask}>
          <PlusCircle className="mr-2 h-4 w-4" strokeWidth={1.5} />
          新建任务
        </Button>
      </div>
    )
  }

  // Cancelled
  if (status === "cancelled") {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2 text-slate-400">
          <Ban className="h-5 w-5" strokeWidth={1.5} />
          <span className="text-lg font-semibold">任务已取消</span>
        </div>

        <p className="text-sm text-slate-400">
          任务已取消执行。您可以查看下方日志了解执行进度。
        </p>

        {/* Partial artifacts */}
        {hasArtifacts && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-slate-200">部分制品下载</h3>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" asChild>
                <a href={artifactUrl} download>
                  <Download className="mr-1.5 h-4 w-4" strokeWidth={1.5} />
                  下载制品 ({artifacts!.length} 个文件)
                </a>
              </Button>
            </div>
          </div>
        )}

        <Button variant="default" size="lg" className="w-full" onClick={resetTask}>
          <PlusCircle className="mr-2 h-4 w-4" strokeWidth={1.5} />
          新建任务
        </Button>
      </div>
    )
  }

  // Running — show a placeholder (MonitorPanel handles the running state)
  return (
    <div className="flex items-center justify-center py-8 text-slate-400">
      <PlayCircle className="mr-2 h-5 w-5 animate-pulse" strokeWidth={1.5} />
      <span>任务执行中...</span>
    </div>
  )
}
