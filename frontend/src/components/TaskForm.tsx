import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../services/api'
import type { Task } from '../types/api'
import { ErrorNote } from './Status'

export default function TaskForm({
  runId, defaultTitle, defaultReason, onDone,
}: { runId: string; defaultTitle: string; defaultReason: string; onDone: () => void }) {
  const [title, setTitle] = useState(defaultTitle)
  const [reason, setReason] = useState(defaultReason)
  const [priority, setPriority] = useState<Task['priority']>('medium')
  const qc = useQueryClient()
  const m = useMutation({
    mutationFn: () => api.approveTask(runId, { title: title.trim(), reason: reason.trim(), priority }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['run', runId] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      onDone()
    },
  })
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault()
        m.mutate()
      }}
    >
      <p className="text-sm text-ink-soft">A task is only created after you approve it here. The agent cannot create one on its own.</p>
      <div>
        <label htmlFor="task-title" className="block text-sm font-medium">Task title</label>
        <input id="task-title" value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={200}
          className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
      </div>
      <div>
        <label htmlFor="task-reason" className="block text-sm font-medium">Reason for inspection</label>
        <textarea id="task-reason" value={reason} onChange={(e) => setReason(e.target.value)} required maxLength={800} rows={3}
          className="mt-1 w-full rounded-ui border border-rule bg-white px-3 py-2" />
      </div>
      <div>
        <label htmlFor="task-priority" className="block text-sm font-medium">Priority</label>
        <select id="task-priority" value={priority} onChange={(e) => setPriority(e.target.value as Task['priority'])}
          className="mt-1 rounded-ui border border-rule bg-white px-3 py-2">
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
      </div>
      <ErrorNote error={m.error} />
      <button type="submit" disabled={m.isPending || !title.trim() || !reason.trim()}
        className="rounded-ui bg-contour px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
        {m.isPending ? 'Creating…' : 'Create inspection task'}
      </button>
    </form>
  )
}
