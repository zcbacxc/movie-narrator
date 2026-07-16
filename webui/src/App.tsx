import {
  createContext,
  useState,
  useCallback,
} from "react"
import { Header } from "@/components/Header"
import { Footer } from "@/components/Footer"
import { CreatePanel } from "@/components/CreatePanel"
import { MonitorPanel } from "@/components/MonitorPanel"
import { useWebSocket } from "@/hooks/useWebSocket"
import { createTask, cancelTask as cancelTaskApi } from "@/lib/api"
import type { FormSubmitData, TaskStatus, WsMessage } from "@/types"

type AppStatus = "idle" | TaskStatus

export interface TaskContextValue {
  status: AppStatus
  taskId: string | null
  currentStep: string
  logText: string
  artifacts: string[]
  error: string | null
  videoPath: string | null
  connected: boolean
  startTask: (data: FormSubmitData, video?: File, bgm?: File) => Promise<void>
  resetTask: () => void
  cancelTask: () => void
}

export const TaskContext = createContext<TaskContextValue | null>(null)

export function App() {
  const [status, setStatus] = useState<AppStatus>("idle")
  const [taskId, setTaskId] = useState<string | null>(null)
  const [currentStep, setCurrentStep] = useState("")
  const [logText, setLogText] = useState("")
  const [artifacts, setArtifacts] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [videoPath, setVideoPath] = useState<string | null>(null)

  // Stable onMessage callback — only depends on stable setState functions
  const onMessage = useCallback((msg: WsMessage) => {
    if (msg.type === "progress") {
      if (msg.step) setCurrentStep(msg.step)
      if (msg.log !== undefined) setLogText(msg.log)
    } else if (msg.type === "terminal") {
      if (msg.status) setStatus(msg.status)
      if (msg.artifacts) setArtifacts(msg.artifacts)
      if (msg.error !== undefined) setError(msg.error)
      if (msg.video_path !== undefined) setVideoPath(msg.video_path)
    }
  }, [])

  // Only keep WebSocket active while the task is running.
  // When the task reaches a terminal state, pass null so the hook
  // tears down the connection and stops initiating new ones.
  const wsTaskId = status === "running" ? taskId : null
  const { connected, sendCancel } = useWebSocket(wsTaskId, onMessage)

  // Only surface the connected indicator while the task is running;
  // prevents flicker from residual reconnection attempts after completion.
  const displayConnected = status === "running" && connected

  const startTask = useCallback(
    async (data: FormSubmitData, video?: File, bgm?: File) => {
      const res = await createTask(data, video, bgm)
      setTaskId(res.task_id)
      setStatus("running")
      setCurrentStep("")
      setLogText("")
      setArtifacts([])
      setError(null)
      setVideoPath(null)
    },
    [],
  )

  const resetTask = useCallback(() => {
    setStatus("idle")
    setTaskId(null)
    setCurrentStep("")
    setLogText("")
    setArtifacts([])
    setError(null)
    setVideoPath(null)
  }, [])

  const handleCancelTask = useCallback(() => {
    if (taskId) {
      sendCancel()
      cancelTaskApi(taskId).catch(() => {})
    }
  }, [taskId, sendCancel])

  const contextValue: TaskContextValue = {
    status,
    taskId,
    currentStep,
    logText,
    artifacts,
    error,
    videoPath,
    connected: displayConnected,
    startTask,
    resetTask,
    cancelTask: handleCancelTask,
  }

  return (
    <TaskContext.Provider value={contextValue}>
      <div className="flex min-h-screen flex-col bg-background">
        <Header />
        <main className="flex-1">
          {status === "idle" ? <CreatePanel /> : <MonitorPanel />}
        </main>
        <Footer />
      </div>
    </TaskContext.Provider>
  )
}

export default App
