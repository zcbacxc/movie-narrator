import { useEffect, useRef, useCallback, useState } from "react"
import type { WsMessage } from "@/types"
import { getWsUrl } from "@/lib/api"

export function useWebSocket(taskId: string | null, onMessage: (msg: WsMessage) => void) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectCount = useRef(0)
  const [connected, setConnected] = useState(false)

  const connect = useCallback(() => {
    if (!taskId) return
    const ws = new WebSocket(getWsUrl(taskId))
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      reconnectCount.current = 0
      ws.send(JSON.stringify({ action: "subscribe" }))
    }

    ws.onmessage = (e) => {
      try {
        const msg: WsMessage = JSON.parse(e.data)
        onMessage(msg)
      } catch {}
    }

    ws.onclose = () => {
      setConnected(false)
      if (reconnectCount.current < 3) {
        reconnectCount.current++
        setTimeout(connect, 2000)
      }
    }

    ws.onerror = () => ws.close()
  }, [taskId, onMessage])

  useEffect(() => {
    connect()
    return () => {
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [connect])

  const sendCancel = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ action: "cancel" }))
  }, [])

  return { connected, sendCancel }
}
