import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, assetUrl, useAccessKey } from '../services/api'
import { useRun } from '../hooks/useRun'
import Plate from '../components/Plate'
import AgentTimeline from '../components/AgentTimeline'
import MetricsPanel from '../components/MetricsPanel'
import TaskForm from '../components/TaskForm'
import ImageField, { emptyDraft, type ImageDraft } from '../components/ImageField'
import { ErrorNote, Notice, StatusPill, categoryLabel, formatDate } from '../components/Status'
import { TERMINAL, type AgentRun, type Category, type Investigation, type Role, type Seg } from '../types/api'

const CATEGORIES: { id: Category; hint: string }[] = [
  { id: 'buildings', hint: 'Roofs and structures' },
  { id: 'trees', hint: 'Individual trees and canopy' },
  { id: 'vegetation', hint: 'Experimental: broad green cover' },
]

const REPORT_KIND = {
  full: 'Full report',
  needs_review: 'Report marked needs review',
  diagnostic: 'Diagnostic report (analysis not completed)',
} as const

function latestSeg(run: AgentRun | undefined, role: Role, category: Category): Seg | undefined {
  return run?.segmentations
    .filter((s) => s.role === role && s.category === category)
    .sort((a, b) => b.attempt - a.attempt)[0]
}

function MissingImages({ inv }: { inv: Investigation }) {
  const qc = useQueryClient()
  const missing = (['earlier', 'newer'] as Role[]).filter((r) => !inv.images.some((i) => i.role === r))
  const [drafts, setDrafts] = useState<Record<Role, ImageDraft>>({ earlier: emptyDraft, newer: emptyDraft })
  const m = useMutation({
    mutationFn: async () => {
      for (const r of missing) {
        const d = drafts[r]
        await api.uploadImage(inv.id, r, d.file as File, d.date, d.source.trim())
      }
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['inv', inv.id] }),
  })
  const ready = missing.every((r) => drafts[r].file) && !m.isPending
  return (
    <section className="space-y-3">
      <Notice>This investigation still needs {missing.join(' and ')} {missing.length > 1 ? 'images' : 'image'} before it can be analysed.</Notice>
      <div className="grid gap-4 md:grid-cols-2">
        {missing.map((r) => (
          <ImageField key={r} role={r} value={drafts[r]} onChange={(v) => setDrafts({ ...drafts, [r]: v })} />
        ))}
      </div>
      <ErrorNote error={m.error} />
      <button disabled={!ready} onClick={() => m.mutate()} className="rounded-ui bg-contour px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
        {m.isPending ? 'Uploading…' : 'Upload images'}
      </button>
    </section>
  )
}

export default function Workspace() {
  const { id = '' } = useParams()
  const key = useAccessKey()
  const qc = useQueryClient()

  const inv = useQuery({ queryKey: ['inv', id, !!key], queryFn: () => api.investigation(id) })
  const [pickedRun, setPickedRun] = useState<string | null>(null)
  const runId = pickedRun ?? inv.data?.latest_run_id ?? null
  const runQ = useRun(runId)
  const run = runQ.data

  const [cats, setCats] = useState<Category[]>(['buildings'])
  const [layer, setLayer] = useState('original')
  const [taskOpen, setTaskOpen] = useState(false)

  const start = useMutation({
    mutationFn: () => api.startRun(id, cats),
    onSuccess: (r) => {
      setPickedRun(r.id)
      setLayer('original')
      qc.setQueryData(['run', r.id], r)
      qc.invalidateQueries({ queryKey: ['inv', id] })
    },
  })

  const report = useMutation({
    mutationFn: () => api.generateReport(runId as string),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['run', runId] }),
  })

  // When a run reaches a final state, refresh the lists that depend on it and show the first detection layer once.
  const settled = useRef<string | null>(null)
  useEffect(() => {
    if (run && TERMINAL.includes(run.status) && settled.current !== run.id) {
      settled.current = run.id
      qc.invalidateQueries({ queryKey: ['inv', id] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['investigations'] })
      if (run.status !== 'failed' && run.categories[0]) setLayer(`det:${run.categories[0]}`)
    }
  }, [run, id, qc])

  const data = inv.data
  const imgs = useMemo(() => {
    const by = (r: Role) => data?.images.find((i) => i.role === r)
    return { earlier: by('earlier'), newer: by('newer') }
  }, [data])

  if (inv.isLoading) return <p className="text-ink-soft">Loading investigation…</p>
  if (inv.error || !data) {
    return (
      <div className="space-y-3">
        <ErrorNote error={inv.error ?? new Error('Investigation not found.')} />
        <Link to="/" className="text-sm underline">Back to investigations</Link>
      </div>
    )
  }

  const active = !!run && !TERMINAL.includes(run.status)
  const bothImages = !!imgs.earlier && !!imgs.newer
  const canRun = !!key && !data.is_sample && bothImages && !active
  const finished = !!run && (run.status === 'completed' || run.status === 'review_required')

  // Which images to show: the aligned, scaled pair from the run when it exists, else the uploaded originals.
  const baseE = assetUrl(run?.prepared_urls.earlier) ?? assetUrl(imgs.earlier?.url)
  const baseN = assetUrl(run?.prepared_urls.newer) ?? assetUrl(imgs.newer?.url)
  const capE = `Earlier: ${formatDate(imgs.earlier?.capture_date)}${imgs.earlier?.source ? `, ${imgs.earlier.source}` : ''}`
  const capN = `Newer: ${formatDate(imgs.newer?.capture_date)}${imgs.newer?.source ? `, ${imgs.newer.source}` : ''}`

  const layers: { id: string; label: string }[] = [{ id: 'original', label: 'Original images' }]
  for (const c of run?.categories ?? []) {
    if (latestSeg(run, 'earlier', c)?.overlay_url) layers.push({ id: `det:${c}`, label: `${categoryLabel(c)} detected` })
    if (run?.diff_urls[c]) layers.push({ id: `chg:${c}`, label: `${categoryLabel(c)} change` })
  }
  const current = layers.some((l) => l.id === layer) ? layer : 'original'
  const [kind, cat] = current.split(':') as [string, Category | undefined]

  let left = { src: baseE, caption: capE }
  let right = { src: baseN, caption: capN }
  let overlay: string | undefined
  let legend: React.ReactNode
  if (kind === 'det' && cat) {
    left = { src: assetUrl(latestSeg(run, 'earlier', cat)?.overlay_url) ?? baseE, caption: `${capE}, ${cat} highlighted` }
    right = { src: assetUrl(latestSeg(run, 'newer', cat)?.overlay_url) ?? baseN, caption: `${capN}, ${cat} highlighted` }
  } else if (kind === 'chg' && cat) {
    overlay = assetUrl(run?.diff_urls[cat])
    left = { src: baseE, caption: capE }
    legend = (
      <span className="flex gap-3 text-sm">
        <span className="flex items-center gap-1"><i className="inline-block size-3 rounded-sm" style={{ background: 'rgb(34,197,94)' }} />Gained in newer</span>
        <span className="flex items-center gap-1"><i className="inline-block size-3 rounded-sm" style={{ background: 'rgb(239,68,68)' }} />Lost from earlier</span>
        <span className="flex items-center gap-1"><i className="inline-block size-3 rounded-sm" style={{ background: 'rgb(148,163,184)' }} />Detected in both</span>
      </span>
    )
  }

  const interp = run?.interpretation?.items?.[0]
  const v = run?.validation

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/" className="text-sm text-ink-soft underline underline-offset-2">All investigations</Link>
          <h1 className="mt-1 text-2xl font-bold">{data.title}</h1>
          <p className="text-ink-soft">
            {data.location_name}
            {data.latitude != null && data.longitude != null && `, ${data.latitude.toFixed(4)}, ${data.longitude.toFixed(4)}`}
            {data.is_sample && <span className="ml-2 rounded-ui bg-contour/10 px-1.5 py-0.5 text-xs font-medium text-contour">Public sample, read-only</span>}
          </p>
          <p className="mt-1 max-w-prose text-sm text-ink-soft">{data.objective}</p>
        </div>
        <StatusPill status={run && !TERMINAL.includes(run.status) ? run.status : data.status} />
      </div>

      {!bothImages && !data.is_sample && key && <MissingImages inv={data} />}
      {!bothImages && (!key || data.is_sample) && <Notice>This investigation does not have both images yet.</Notice>}

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_25rem]">
        <div className="space-y-4">
          {layers.length > 1 && (
            <div role="group" aria-label="Image layer" className="flex flex-wrap gap-1.5">
              {layers.map((l) => (
                <button
                  key={l.id}
                  aria-pressed={current === l.id}
                  onClick={() => setLayer(l.id)}
                  className={`rounded-ui border px-3 py-1 text-sm font-medium ${current === l.id ? 'border-contour bg-contour text-white' : 'border-rule bg-panel hover:border-ink'}`}
                >
                  {l.label}
                </button>
              ))}
            </div>
          )}
          {bothImages && <Plate left={left} right={right} overlay={overlay} legend={legend} />}
          {run && v && (
            <p className="text-sm text-ink-soft">
              Image pair check: {String(v.verdict ?? 'done')}
              {v.aligned === false && ', alignment could not be confirmed'}
              {v.method ? ` (method: ${v.method})` : ''}.
              {(v.reasons ?? []).length > 0 && ` ${(v.reasons ?? []).join(' ')}`}
            </p>
          )}
        </div>

        <aside className="space-y-8">
          <section aria-labelledby="run-h" className="space-y-3">
            <h2 id="run-h" className="text-lg font-semibold">Analysis</h2>
            {data.is_sample ? (
              <p className="text-sm text-ink-soft">This is a recorded run, shown read-only.</p>
            ) : !key ? (
              <Notice tone="info">Enter the access key to run an analysis.</Notice>
            ) : (
              <form
                className="space-y-3"
                onSubmit={(e) => {
                  e.preventDefault()
                  if (canRun && cats.length) start.mutate()
                }}
              >
                <fieldset disabled={!canRun}>
                  <legend className="text-sm font-medium">What should it look for?</legend>
                  <div className="mt-1 space-y-1">
                    {CATEGORIES.map((c) => (
                      <label key={c.id} className="flex items-start gap-2 text-sm">
                        <input
                          type="checkbox"
                          className="mt-1 accent-contour"
                          checked={cats.includes(c.id)}
                          onChange={(e) => setCats(e.target.checked ? [...cats, c.id] : cats.filter((x) => x !== c.id))}
                        />
                        <span><span className="font-medium">{categoryLabel(c.id)}</span> <span className="text-ink-soft">{c.hint}</span></span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                <ErrorNote error={start.error} />
                <button
                  type="submit"
                  disabled={!canRun || cats.length === 0 || start.isPending}
                  className="rounded-ui bg-contour px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {active ? 'Analysis running…' : run ? 'Run again' : 'Run analysis'}
                </button>
              </form>
            )}
          </section>

          {run && (
            <section aria-labelledby="steps-h" className="space-y-3">
              <h2 id="steps-h" className="text-lg font-semibold">Agent steps</h2>
              <AgentTimeline steps={run.steps} live={active} />
              {run.status === 'failed' && (
                <div className="space-y-1">
                  <ErrorNote error={new Error(run.error ?? 'The analysis failed.')} />
                  <p className="text-sm text-ink-soft">A failed run produces no report, so nothing here can be mistaken for a result.</p>
                </div>
              )}
              {runQ.error && <ErrorNote error={runQ.error} />}
            </section>
          )}

          {run?.status === 'review_required' && (
            <Notice>
              This run finished but needs a person to review it before anyone relies on it. The reasons are listed under each result.
            </Notice>
          )}

          {finished && (
            <section aria-labelledby="res-h" className="space-y-3">
              <h2 id="res-h" className="text-lg font-semibold">Results</h2>
              <MetricsPanel run={run} />
            </section>
          )}

          {run && (finished || run.status === 'failed') && (
            <section aria-labelledby="rep-h" className="space-y-2">
              <h2 id="rep-h" className="text-lg font-semibold">Report</h2>
              {run.report ? (
                <p className="text-sm">
                  <a className="font-medium text-contour underline underline-offset-2" href={assetUrl(run.report.url)} target="_blank" rel="noreferrer">
                    Open the PDF
                  </a>
                  <span className="text-ink-soft">, {REPORT_KIND[run.report.kind]}</span>
                </p>
              ) : run.status === 'failed' ? (
                <p className="text-sm text-ink-soft">No report exists for this run.</p>
              ) : key && !data.is_sample ? (
                <>
                  <button onClick={() => report.mutate()} disabled={report.isPending} className="rounded-ui border border-ink px-3 py-1.5 text-sm font-medium hover:bg-white disabled:opacity-50">
                    {report.isPending ? 'Generating…' : 'Generate report'}
                  </button>
                  <ErrorNote error={report.error} />
                </>
              ) : (
                <p className="text-sm text-ink-soft">No report has been generated.</p>
              )}
            </section>
          )}

          {finished && (
            <section aria-labelledby="task-h" className="space-y-3">
              <h2 id="task-h" className="text-lg font-semibold">Inspection task</h2>
              {run.tasks.map((t) => (
                <div key={t.id} className="rounded-ui border border-rule bg-panel p-3 text-sm">
                  <p className="font-medium">{t.title}</p>
                  <p className="text-ink-soft">{t.reason}</p>
                  <p className="mt-1"><StatusPill status={t.status} /></p>
                </div>
              ))}
              {key && !data.is_sample && !taskOpen && (
                <button onClick={() => setTaskOpen(true)} className="rounded-ui border border-ink px-3 py-1.5 text-sm font-medium hover:bg-white">
                  Approve an inspection task
                </button>
              )}
              {taskOpen && (
                <TaskForm
                  runId={run.id}
                  defaultTitle={`Inspect ${data.location_name}`}
                  defaultReason={interp?.recommended_action ?? 'Review the highlighted change on site.'}
                  onDone={() => setTaskOpen(false)}
                />
              )}
            </section>
          )}
        </aside>
      </div>
    </div>
  )
}
