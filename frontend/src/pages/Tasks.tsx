import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, useAccessKey } from '../services/api'
import { ErrorNote, StatusPill, formatDate } from '../components/Status'
import type { Task } from '../types/api'

export default function Tasks() {
  const key = useAccessKey()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['tasks', !!key], queryFn: api.tasks })
  const set = useMutation({
    mutationFn: (v: { id: string; status: Task['status'] }) => api.setTaskStatus(v.id, v.status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
  })
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Inspection tasks</h1>
        <p className="mt-1 text-ink-soft">Follow-ups created from findings after a person approved them.</p>
      </div>
      <ErrorNote error={q.error ?? set.error} />
      {q.isLoading ? (
        <p className="text-ink-soft">Loading tasks…</p>
      ) : (q.data ?? []).length === 0 ? (
        <div className="rounded-ui border border-dashed border-rule bg-panel p-8 text-center">
          <p className="font-medium">No inspection tasks yet</p>
          <p className="mt-1 text-sm text-ink-soft">Open a finished investigation and approve a task from its findings.</p>
        </div>
      ) : (
        <ul className="divide-y divide-rule border-y border-rule">
          {q.data!.map((t) => (
            <li key={t.id} className="grid gap-2 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
              <div>
                <p className="font-semibold">{t.title}</p>
                <p className="text-sm text-ink-soft">{t.reason}</p>
                <p className="mt-1 text-sm text-ink-soft">
                  <Link className="underline underline-offset-2" to={`/investigations/${t.investigation_id}`}>
                    {t.investigation_title ?? 'Investigation'}
                  </Link>
                  , {t.priority} priority, created {formatDate(t.created_at)}
                </p>
              </div>
              <div className="flex items-center gap-3">
                <StatusPill status={t.status} />
                {key && (
                  <select
                    aria-label={`Status of ${t.title}`}
                    value={t.status}
                    onChange={(e) => set.mutate({ id: t.id, status: e.target.value as Task['status'] })}
                    className="rounded-ui border border-rule bg-white px-2 py-1 text-sm"
                  >
                    <option value="open">Open</option>
                    <option value="in_progress">In progress</option>
                    <option value="done">Done</option>
                  </select>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
