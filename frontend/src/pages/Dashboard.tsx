import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, useAccessKey } from '../services/api'
import { ErrorNote, StatusPill, formatDate } from '../components/Status'

function Figure({ value, label }: { value: number | string; label: string }) {
  return (
    <div className="border-t-2 border-ink pt-2">
      <div className="num text-3xl font-bold leading-none">{value}</div>
      <div className="mt-1 text-sm text-ink-soft">{label}</div>
    </div>
  )
}

export default function Dashboard() {
  const key = useAccessKey()
  const stats = useQuery({ queryKey: ['stats', !!key], queryFn: api.stats })
  const list = useQuery({ queryKey: ['investigations', !!key], queryFn: api.investigations })
  const statValue = (value: number | undefined) =>
    stats.isLoading ? 'Loading' : value ?? 'Unavailable'

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Investigations</h1>
          <p className="mt-1 max-w-prose text-ink-soft">
            Compare two images of one site, measure what the model detected in each, and record what needs a person on the ground.
          </p>
        </div>
        <Link to="/new" className="rounded-ui bg-contour px-4 py-2 text-sm font-medium text-white hover:bg-contour-deep">
          New investigation
        </Link>
      </div>

      <ErrorNote error={stats.error ?? list.error} />

      <section aria-label="Summary" className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        <Figure value={statValue(stats.data?.investigations)} label="Investigations" />
        <Figure value={statValue(stats.data?.awaiting_review)} label="Awaiting review" />
        <Figure value={statValue(stats.data?.completed)} label="Completed" />
        <Figure value={statValue(stats.data?.open_tasks)} label="Open inspection tasks" />
      </section>

      {!key && (
        <p className="rounded-ui border border-rule bg-panel px-3 py-2 text-sm text-ink-soft">
          Public investigations are read-only. Enter the project access key to create investigations and run analyses.
        </p>
      )}

      <section aria-label="Investigation list">
        {list.isLoading ? (
          <p className="text-ink-soft">Loading investigations…</p>
        ) : list.isError ? (
          <p role="status" className="rounded-ui border border-rule bg-panel p-6 text-center text-sm text-ink-soft">
            Investigations could not be loaded. Resolve the server connection before trying again.
          </p>
        ) : (list.data ?? []).length === 0 ? (
          <div className="rounded-ui border border-dashed border-rule bg-panel p-8 text-center">
            <p className="font-medium">{key ? 'No investigations yet' : 'No public investigations are available'}</p>
            <p className="mt-1 text-sm text-ink-soft">
              {key
                ? 'Create one with earlier and newer images of the same site.'
                : 'A public, read-only investigation will appear here once one has been published.'}
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-rule border-y border-rule">
            {list.data!.map((i) => (
              <li key={i.id}>
                <Link to={`/investigations/${i.id}`} className="grid gap-1 px-1 py-4 hover:bg-white/60 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
                  <span>
                    <span className="font-semibold">{i.title}</span>
                    {i.is_sample && <span className="ml-2 rounded-ui bg-contour/10 px-1.5 py-0.5 text-xs font-medium text-contour">Public sample</span>}
                    <span className="block text-sm text-ink-soft">{i.location_name}, created {formatDate(i.created_at)}</span>
                  </span>
                  <StatusPill status={i.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
