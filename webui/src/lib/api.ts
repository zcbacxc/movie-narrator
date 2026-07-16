import type { FormSubmitData, TaskCreateResponse, TaskStatusResponse } from "@/types"

const API_BASE = ""

export async function createTask(
  data: FormSubmitData,
  video?: File,
  bgm?: File,
): Promise<TaskCreateResponse> {
  const formData = new FormData()

  // Append form fields
  Object.entries(data).forEach(([key, value]) => {
    if (typeof value === "boolean") {
      formData.append(key, value ? "true" : "false")
    } else if (value !== undefined && value !== null) {
      formData.append(key, String(value))
    }
  })

  // Append files
  if (video) formData.append("video", video)
  if (bgm) formData.append("bgm", bgm)

  const res = await fetch(`${API_BASE}/api/tasks`, {
    method: "POST",
    body: formData,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function getTask(taskId: string): Promise<TaskStatusResponse> {
  const res = await fetch(`${API_BASE}/api/tasks/${taskId}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function cancelTask(taskId: string): Promise<void> {
  await fetch(`${API_BASE}/api/tasks/${taskId}`, { method: "DELETE" })
}

export function getArtifactUrl(taskId: string): string {
  return `${API_BASE}/api/artifacts/${taskId}`
}

export function getWsUrl(taskId: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:"
  return `${proto}//${location.host}/ws/task/${taskId}`
}
