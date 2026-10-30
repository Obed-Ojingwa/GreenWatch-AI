-- GreenWatch initial Supabase/PostgreSQL schema.
-- Apply once to a new project. Keep byte-for-byte aligned with backend/app/db/schema.sql.
create extension if not exists "pgcrypto";

create table investigations (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  location_name text not null,
  latitude double precision,
  longitude double precision,
  objective text not null,
  notes text,
  is_sample boolean not null default false,
  status text not null default 'draft'
    check (status in ('draft','processing','completed','review_required','failed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table images (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null references investigations(id) on delete cascade,
  role text not null check (role in ('earlier','newer')),
  storage_path text not null,
  capture_date date,
  source text,
  width int not null,
  height int not null,
  sha256 text not null,
  georef_meta jsonb,              -- {crs, pixel_size_m, bounds} or null
  created_at timestamptz not null default now(),
  unique (investigation_id, role)
);

create table analysis_runs (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null references investigations(id) on delete cascade,
  status text not null default 'queued'
    check (status in ('queued','processing','completed','review_required','failed')),
  categories jsonb not null default '["buildings"]',
  model_config jsonb not null default '{}',   -- model IDs, prompts used, retries, QC thresholds
  validation jsonb,                           -- alignment/overlap/pair checks
  metrics jsonb,                              -- per-category ComparisonResult
  findings jsonb,                             -- list of findings, each with evidence_refs + limitations
  interpretation jsonb,                       -- validated structured Muse Spark output
  approval boolean not null default false,    -- user approved inspection-task creation
  error text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now()
);

create table segmentation_results (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references analysis_runs(id) on delete cascade,
  image_id uuid not null references images(id) on delete cascade,
  role text not null check (role in ('earlier','newer')),
  category text not null,
  prompt_used text not null,
  attempt int not null default 1,
  mask_path text,
  overlay_path text,
  pixel_count int,
  coverage double precision,
  qc_status text check (qc_status in ('accepted','retry','flagged')),
  qc_detail jsonb,
  from_cache boolean not null default false,
  created_at timestamptz not null default now()
);

create table agent_steps (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references analysis_runs(id) on delete cascade,
  idx int not null,
  tool text not null,
  input jsonb,
  output jsonb,
  status text not null check (status in ('running','ok','error','skipped')),
  duration_ms int,
  created_at timestamptz not null default now(),
  unique (run_id, idx)
);

create table reports (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null references investigations(id) on delete cascade,
  run_id uuid not null references analysis_runs(id) on delete cascade,
  kind text not null check (kind in ('full','needs_review','diagnostic')),
  storage_path text not null,
  created_at timestamptz not null default now()
);

create table inspection_tasks (
  id uuid primary key default gen_random_uuid(),
  investigation_id uuid not null references investigations(id) on delete cascade,
  run_id uuid references analysis_runs(id) on delete set null,
  title text not null,
  reason text not null,
  priority text not null default 'medium' check (priority in ('low','medium','high')),
  status text not null default 'open' check (status in ('open','in_progress','done')),
  created_at timestamptz not null default now()
);

create index on images (investigation_id);
create index on analysis_runs (investigation_id);
create index on agent_steps (run_id, idx);
create index on segmentation_results (run_id);

-- The backend connects with its server-side database credentials. No Supabase REST
-- policies are created, so the anon/authenticated roles receive no table access.
alter table investigations enable row level security;
alter table images enable row level security;
alter table analysis_runs enable row level security;
alter table segmentation_results enable row level security;
alter table agent_steps enable row level security;
alter table reports enable row level security;
alter table inspection_tasks enable row level security;
