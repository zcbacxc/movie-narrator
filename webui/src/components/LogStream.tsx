import { useEffect, useRef } from "react"
import { Terminal } from "lucide-react"

interface LogStreamProps {
  logText: string
}

export function LogStream({ logText }: LogStreamProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const preRef = useRef<HTMLPreElement>(null)

  // Auto-scroll to bottom when log updates
  useEffect(() => {
    if (preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight
    }
  }, [logText])

  return (
    <div ref={containerRef} className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <Terminal className="h-4 w-4 text-pink-500" strokeWidth={1.5} />
        <span className="text-sm font-medium text-slate-200">实时日志</span>
      </div>
      <pre
        ref={preRef}
        className="flex-1 overflow-auto bg-background p-4 font-mono text-xs leading-relaxed text-slate-300"
        style={{ minHeight: "200px", maxHeight: "400px" }}
      >
        {logText || (
          <span className="text-slate-500">等待日志输出...</span>
        )}
      </pre>
    </div>
  )
}
