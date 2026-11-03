import { useSyncExternalStore } from 'react'
import type {
  AgentRun, Category, Investigation, InvestigationInput, Report, Role, Stats, Task,
} from '../types/api'

const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim()
const BASE = (configuredBase || (import.meta.env.DEV ? 'http://localhost:8000' : '')).replace(/\/$/, '')
const KEY_STORAGE = 'greenwatch.accessKey'

// The access key lives in sessionStorage when available (cleared when the tab closes) and is never
// baked into the bundle. Reads and writes are wrapped because storage can throw in private windows.
let memoryKey = ''
const listeners = new Set<() => void>()

export function getAccessKey(): string {
  try {
    return sessionStorage.getItem(KEY_STORAGE) ?? memoryKey
  } catch {
    return memoryKey
  }
}

export function setAccessKey(key: string): void {
  memoryKey = key
  try {
    if (key) sessionStorage.setItem(KEY_STORAGE, key)
    else sessionStorage.removeItem(KEY_STORAGE)
  } catch {
    /* storage unavailable: keep the in-memory copy */
  }
  listeners.forEach((l) => l())
}

function subscribe(l: () => void) {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function useAccessKey(): string {
  return useSyncExternalStore(subscribe, getAccessKey, () => '')
}

/** Signed URLs from the local storage backend are relative to the API; Supabase ones are absolute. */
export function assetUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined
  return url.startsWith('/') ? BASE + url : url
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function messageFrom(body: unknown, fallback: string): string {
  if (body && typeof body === 'object') {
    const b = body as { detail?: unknown; error?: unknown }
    if (typeof b.detail === 'string') return b.detail
    if (Array.isArray(b.detail)) {
      return b.detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String((d as { msg: unknown }).msg) : String(d)))
        .join('; ')
    }
    if (typeof b.error === 'string') return b.error
  }
  return fallback
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (!BASE) {
    throw new ApiError(0, 'The backend URL is not configured. Set VITE_API_BASE_URL in the frontend deployment environment.')
  }
  const headers: Record<string, string> = {}
  const key = getAccessKey()
  if (key) headers['X-Demo-Key'] = key
  let payload: BodyInit | undefined
  if (body instanceof FormData) payload = body
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  let res: Response
  try {
    res = await fetch(BASE + path, { method, headers, body: payload })
  } catch {
    throw new ApiError(0, 'Cannot reach the GreenWatch server. Check your connection and try again.')
  }
  if (!res.ok) {
    let parsed: unknown = null
    try {
      parsed = await res.json()
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, messageFrom(parsed, `Request failed (${res.status}).`))
  }
  return (await res.json()) as T
}

export const api = {
  stats: () => request<Stats>('GET', '/api/stats'),
  investigations: () => request<Investigation[]>('GET', '/api/investigations'),
  investigation: (id: string) => request<Investigation>('GET', `/api/investigations/${id}`),
  createInvestigation: (b: InvestigationInput) => request<Investigation>('POST', '/api/investigations', b),
  uploadImage: (id: string, role: Role, file: File, captureDate: string, source: string) => {
    const f = new FormData()
    f.append('role', role)
    if (captureDate) f.append('capture_date', captureDate)
    if (source) f.append('source', source)
    f.append('file', file)
    return request<Investigation>('POST', `/api/investigations/${id}/images`, f)
  },
  startRun: (id: string, categories: Category[]) =>
    request<AgentRun>('POST', `/api/investigations/${id}/agent-runs`, { categories }),
  run: (id: string) => request<AgentRun>('GET', `/api/agent-runs/${id}`),
  approveTask: (runId: string, b: { title: string; reason: string; priority: Task['priority'] }) =>
    request<Task>('POST', `/api/agent-runs/${runId}/approve-task`, b),
  generateReport: (runId: string) => request<Report>('POST', `/api/agent-runs/${runId}/report`),
  tasks: () => request<Task[]>('GET', '/api/tasks'),
  setTaskStatus: (id: string, status: Task['status']) => request<Task>('PATCH', `/api/tasks/${id}`, { status }),
}
