import { Film } from "lucide-react"

export function Header() {
  return (
    <header className="flex items-center justify-between border-b border-border bg-surface/50 px-6 py-4 backdrop-blur-sm">
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-pink-500/10">
          <Film className="h-5 w-5 text-pink-500" strokeWidth={1.5} />
        </div>
        <div className="flex items-baseline gap-2">
          <h1 className="text-lg font-semibold text-slate-50">Movie Narrator</h1>
          <span className="rounded-full border border-border bg-surface px-2 py-0.5 text-xs font-medium text-slate-400">
            v0.1.0
          </span>
        </div>
      </div>
    </header>
  )
}
