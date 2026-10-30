import type { ReactNode } from 'react'

const LABELS: Record<string, { label: string; tone: 'idle' | 'busy' | 'good' | 'warn' | 'bad' }> = {
  draft: { label: 'Draft', tone: 'idle' },
  queued: { label: 'Queued', tone: 'busy' },
  processing: { label: 'Running', tone: 'busy' },
  completed: { label: 'Completed', tone: 'good' },
  review_required: { label: 'Needs review', tone: 'warn' },
  failed: { label: 'Failed', tone: 'bad' },
  open: { label: 'Open', tone: 'warn' },
  in_progress: { label: 'In progress', tone: 'busy' },
  done: { label: 'Done', tone: 'good' },
}

const DOT = { idle: 'bg-rule', busy: 'bg-contour pulse-dot', good: 'bg-gain', warn: 'bg-warn', bad: 'bg-loss' }

export function StatusPill({ status }: { status: string }) {
  const s = LABELS[status] ?? { label: status, tone: 'idle' as const }
  return (
    <span className="inline-flex items-center gap-1.5 text-sm font-medium">
      <span className={`inline-block size-2 rounded-full ${DOT[s.tone]}`} aria-hidden />
      {s.label}
    </span>
  )
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null
  const message = error instanceof Error ? error.message : String(error)
  return (
    <p role="alert" className="rounded-ui border border-loss/40 bg-loss-wash px-3 py-2 text-sm text-loss">
      {message}
    </p>
  )
}

export function Notice({ tone = 'warn', children }: { tone?: 'warn' | 'info'; children: ReactNode }) {
  const cls = tone === 'warn' ? 'border-warn/40 bg-warn-wash text-warn' : 'border-rule bg-panel text-ink-soft'
  return <div className={`rounded-ui border px-3 py-2 text-sm ${cls}`}>{children}</div>
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return 'date not recorded'
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

export const categoryLabel = (c: string) => c.charAt(0).toUpperCase() + c.slice(1)
