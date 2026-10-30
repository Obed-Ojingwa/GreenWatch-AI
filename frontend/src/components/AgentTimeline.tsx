import { useState } from 'react'
import type { Step } from '../types/api'

const TOOL_LABEL: Record<string, string> = {
  get_investigation: 'Read the investigation',
  validate_image_pair: 'Check the image pair',
  segment_image: 'Segment an image',
  check_mask_quality: 'Quality-check a mask',
  compare_masks: 'Compare the masks',
  calculate_change_metrics: 'Calculate change metrics',
  save_analysis_results: 'Save the results',
  submit_interpretation: 'Record the interpretation',
  generate_report: 'Generate the report',
  create_inspection_task: 'Create an inspection task',
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' ? (v as Record<string, unknown>) : {}
}

/** One plain-language line per step, built only from values the backend recorded. */
export function summarize(step: Step): string {
  const o = obj(step.output)
  const i = obj(step.input)
  if (typeof o.error === 'string') return o.error
  switch (step.tool) {
    case 'validate_image_pair':
      return `${o.verdict ?? 'checked'}${o.aligned === false ? ', alignment not confirmed' : ''}${o.method ? ` (${o.method})` : ''}`
    case 'segment_image': {
      const px = typeof o.pixel_count === 'number' ? `${o.pixel_count.toLocaleString()} px` : 'done'
      const cache = o.from_cache ? ', from cache' : ''
      return `${i.category ?? ''} ${i.role ?? ''}: ${px}${cache}${o.prompt ? `, prompt "${o.prompt}"` : ''}`.trim()
    }
    case 'check_mask_quality': {
      const cov = typeof o.coverage === 'number' ? `, ${(o.coverage * 100).toFixed(2)}% covered` : ''
      return `${o.status ?? 'checked'}${cov}`
    }
    case 'compare_masks':
    case 'calculate_change_metrics':
      return `${i.category ?? ''}: ${o.change_direction ?? 'done'}`.trim()
    case 'generate_report':
      return `${o.kind ?? 'report'} created`
    default:
      return step.status === 'ok' ? 'Done' : step.status
  }
}

function Marker({ status }: { status: string }) {
  const cls =
    status === 'ok' ? 'bg-gain border-gain'
    : status === 'error' ? 'bg-loss border-loss'
    : 'bg-panel border-contour pulse-dot'
  return <span aria-hidden className={`absolute -left-[7px] top-1.5 size-3 rounded-full border-2 ${cls}`} />
}

export default function AgentTimeline({ steps, live }: { steps: Step[]; live: boolean }) {
  const [open, setOpen] = useState<number | null>(null)
  if (steps.length === 0) {
    return <p className="text-sm text-ink-soft">{live ? 'Waiting for the agent to start…' : 'No agent steps were recorded.'}</p>
  }
  return (
    <ol className="ml-2 border-l-2 border-rule pl-5" aria-label="Agent steps" aria-live="polite">
      {steps.map((s) => {
        const retry = s.tool === 'check_mask_quality' && obj(s.output).status === 'retry'
        return (
          <li key={s.idx} className="relative pb-4 last:pb-0">
            <Marker status={s.status} />
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-medium">{TOOL_LABEL[s.tool] ?? s.tool}</span>
              {retry && <span className="rounded-ui bg-warn-wash px-1.5 text-xs font-medium text-warn">Retrying with another prompt</span>}
              {s.duration_ms != null && <span className="num text-xs text-ink-soft">{(s.duration_ms / 1000).toFixed(1)} s</span>}
            </div>
            <p className={`text-sm ${s.status === 'error' ? 'text-loss' : 'text-ink-soft'}`}>{summarize(s)}</p>
            <button
              className="mt-0.5 text-xs text-contour underline underline-offset-2"
              aria-expanded={open === s.idx}
              onClick={() => setOpen(open === s.idx ? null : s.idx)}
            >
              {open === s.idx ? 'Hide details' : 'Show details'}
            </button>
            {open === s.idx && (
              <pre className="mt-1 max-h-60 overflow-auto rounded-ui border border-rule bg-panel p-2 text-xs leading-snug">
                {JSON.stringify({ input: s.input, output: s.output }, null, 2)}
              </pre>
            )}
          </li>
        )
      })}
    </ol>
  )
}
