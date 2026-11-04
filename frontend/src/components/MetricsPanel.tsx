import type { AgentRun, CategoryMetrics, Direction } from '../types/api'
import { categoryLabel } from './Status'

const DIRECTION: Record<Direction, string> = {
  increase: 'More detected pixels in the newer image',
  decrease: 'Fewer detected pixels in the newer image',
  unchanged: 'No difference in detected pixels',
  undetermined: 'Direction cannot be determined',
}

const n = (v: number) => v.toLocaleString()
const signed = (v: number) => (v > 0 ? `+${n(v)}` : n(v))
const pct = (v: number | null) => (v === null ? 'Not defined' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)

function Row({ label, a, b, c }: { label: string; a: string; b: string; c: string }) {
  return (
    <tr className="border-t border-rule">
      <th scope="row" className="py-2 pr-3 text-left font-medium">{label}</th>
      <td className="num py-2 pr-3 text-right">{a}</td>
      <td className="num py-2 pr-3 text-right">{b}</td>
      <td className="num py-2 text-right font-medium">{c}</td>
    </tr>
  )
}

function CategoryBlock({ m, run }: { m: CategoryMetrics; run: AgentRun }) {
  const finding = run.findings?.find((f) => f.category === m.category)
  const interp = run.interpretation?.items?.find((i) => i.category === m.category)
  const tone =
    m.change_direction === 'increase' ? 'text-gain' : m.change_direction === 'decrease' ? 'text-loss' : 'text-ink-soft'
  return (
    <section className="border-t border-ink pt-4 first:border-t-0 first:pt-0">
      <h3 className="text-base font-semibold">{categoryLabel(m.category)}</h3>
      <p className={`mt-0.5 text-sm font-medium ${tone}`}>{DIRECTION[m.change_direction]}</p>
      <table className="mt-2 w-full text-sm">
        <caption className="sr-only">Detected {m.category} pixels in the earlier and newer images</caption>
        <thead>
          <tr className="text-ink-soft">
            <th scope="col" className="pb-1 text-left font-normal"><span className="sr-only">Measure</span></th>
            <th scope="col" className="pb-1 pr-3 text-right font-normal">Earlier</th>
            <th scope="col" className="pb-1 pr-3 text-right font-normal">Newer</th>
            <th scope="col" className="pb-1 text-right font-normal">Change</th>
          </tr>
        </thead>
        <tbody>
          <Row label="Detected pixels" a={n(m.old_pixels)} b={n(m.new_pixels)} c={signed(m.difference_pixels)} />
          <Row label="Share of compared area" a={`${(m.coverage_old * 100).toFixed(2)}%`} b={`${(m.coverage_new * 100).toFixed(2)}%`} c={pct(m.change_percent)} />
          {m.area_m2 && (
            <Row label="Estimated area (m²)" a={n(Math.round(m.area_m2.earlier))} b={n(Math.round(m.area_m2.newer))} c={signed(Math.round(m.area_m2.difference))} />
          )}
        </tbody>
      </table>
      {!m.real_world_area_available && (
        <p className="mt-2 text-sm text-ink-soft">
          Measured in detected pixels. Real-world area is not shown because these images have no usable georeference.
        </p>
      )}
      {interp && (
        <div className="mt-3 text-sm">
          <p>{interp.summary}</p>
          <p className="mt-1 text-ink-soft">
            <span className="font-medium text-ink">Suggested next step:</span> {interp.recommended_action}
          </p>
          <p className="mt-1 text-xs text-ink-soft">
            Written by {interp.source === 'gemini' ? 'Gemini' : interp.source === 'muse_spark' ? 'Muse Spark' : 'the backend template'}. All numbers above come from backend measurement.
          </p>
        </div>
      )}
      {finding && finding.limitations.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer font-medium">Limitations ({finding.limitations.length})</summary>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-ink-soft">
            {finding.limitations.map((l) => <li key={l}>{l}</li>)}
          </ul>
        </details>
      )}
    </section>
  )
}

export default function MetricsPanel({ run }: { run: AgentRun }) {
  const entries = Object.values(run.metrics ?? {})
  if (entries.length === 0) return <p className="text-sm text-ink-soft">No measurements were produced for this run.</p>
  return <div className="space-y-5">{entries.map((m) => <CategoryBlock key={m.category} m={m} run={run} />)}</div>
}
