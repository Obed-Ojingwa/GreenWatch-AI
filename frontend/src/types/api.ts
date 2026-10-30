// Mirrors backend/app/schemas/__init__.py. Keep in sync when the API changes.

export type Category = 'buildings' | 'trees' | 'vegetation'
export type Role = 'earlier' | 'newer'
export type RunStatus = 'queued' | 'processing' | 'completed' | 'review_required' | 'failed'
export type Direction = 'increase' | 'decrease' | 'unchanged' | 'undetermined'

export interface ImageOut {
  id: string
  role: Role
  capture_date: string | null
  source: string | null
  width: number
  height: number
  georeferenced: boolean
  url: string
}

export interface Investigation {
  id: string
  title: string
  location_name: string
  latitude: number | null
  longitude: number | null
  objective: string
  notes: string | null
  is_sample: boolean
  status: string
  created_at: string
  images: ImageOut[]
  latest_run_id: string | null
}

export interface Step {
  idx: number
  tool: string
  status: string
  input: unknown
  output: unknown
  duration_ms: number | null
}

export interface Seg {
  id: string
  role: Role
  category: Category
  prompt_used: string
  attempt: number
  pixel_count: number | null
  coverage: number | null
  qc_status: 'accepted' | 'retry' | 'flagged' | null
  qc_detail: unknown
  from_cache: boolean
  overlay_url: string | null
  mask_url: string | null
}

export interface Report {
  id: string
  kind: 'full' | 'needs_review' | 'diagnostic'
  url: string
  created_at: string
}

export interface Task {
  id: string
  investigation_id: string
  run_id: string | null
  investigation_title: string | null
  title: string
  reason: string
  priority: 'low' | 'medium' | 'high'
  status: 'open' | 'in_progress' | 'done'
  created_at: string
}

export interface CategoryMetrics {
  category: Category
  old_pixels: number
  new_pixels: number
  difference_pixels: number
  change_percent: number | null
  change_direction: Direction
  change_direction_raw?: Direction
  coverage_old: number
  coverage_new: number
  valid_pixels: number
  real_world_area_available: boolean
  alignment_verified: boolean
  qc: { earlier: string | null; newer: string | null }
  prompts: { earlier: string; newer: string }
  prompt_mismatch?: boolean
  area_m2?: { earlier: number; newer: number; difference: number }
}

export interface Finding {
  category: Category
  change_direction: Direction
  metric_type: string
  old_value: number
  new_value: number
  change_value: number
  change_percent: number | null
  evidence_refs: string[]
  limitations: string[]
  review_status: string
}

export interface InterpretationItem {
  category?: Category
  summary: string
  change_direction: Direction
  evidence_ids: string[]
  limitations: string[]
  recommended_action: string
  requires_human_review: boolean
  source?: string
}

export interface PairValidation {
  aligned?: boolean
  method?: string
  verdict?: string
  reasons?: string[]
  warnings?: string[]
  [key: string]: unknown
}

export interface AgentRun {
  id: string
  investigation_id: string
  status: RunStatus
  categories: Category[]
  error: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
  validation: PairValidation | null
  metrics: Record<string, CategoryMetrics> | null
  findings: Finding[] | null
  interpretation: { items?: InterpretationItem[]; [key: string]: unknown } | null
  steps: Step[]
  segmentations: Seg[]
  prepared_urls: Partial<Record<Role, string>>
  diff_urls: Record<string, string>
  report: Report | null
  tasks: Task[]
  model_info: unknown
}

export interface Stats {
  investigations: number
  awaiting_review: number
  completed: number
  open_tasks: number
}

export interface InvestigationInput {
  title: string
  location_name: string
  latitude?: number | null
  longitude?: number | null
  objective: string
  notes?: string | null
}

export const TERMINAL: RunStatus[] = ['completed', 'review_required', 'failed']
