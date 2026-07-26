import { X, Wifi, WifiOff } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { ProgressTimeline } from "@/components/ProgressTimeline"
import { LogStream } from "@/components/LogStream"
import { ResultPanel } from "@/components/ResultPanel"
import { useTask } from "@/hooks/useTask"

export function MonitorPanel() {
  const { taskId, status, currentStep, logText, connected, cancelTask } = useTask()

  const isRunning = status === "running"

  return (
    <div className="mx-auto max-w-4xl space-y-4 px-4 py-8">
      {/* Header card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CardTitle>任务监控</CardTitle>
              {taskId && (
                <span className="rounded-full border border-border bg-surface px-2 py-0.5 font-mono text-xs text-slate-400">
                  {taskId}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3">
              {/* Connection indicator */}
              <div className="flex items-center gap-1.5 text-xs">
                {connected ? (
                  <>
                    <Wifi className="h-3.5 w-3.5 text-green-400" strokeWidth={1.5} />
                    <span className="text-green-400">已连接</span>
                  </>
                ) : (
                  <>
                    <WifiOff className="h-3.5 w-3.5 text-slate-400" strokeWidth={1.5} />
                    <span className="text-slate-400">未连接</span>
                  </>
                )}
              </div>

              {/* Cancel button */}
              {isRunning && (
                <Button variant="destructive" size="sm" onClick={cancelTask}>
                  <X className="mr-1.5 h-4 w-4" strokeWidth={1.5} />
                  取消任务
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Progress timeline */}
          <ProgressTimeline currentStep={currentStep} status={status} />

          <Separator />

          {/* Log stream */}
          <div className="overflow-hidden rounded-card border border-border">
            <LogStream logText={logText} />
          </div>

          <Separator />

          {/* Result panel */}
          <ResultPanel />
        </CardContent>
      </Card>
    </div>
  )
}
