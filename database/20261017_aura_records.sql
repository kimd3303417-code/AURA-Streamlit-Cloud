-- Apply only to the original stock-risk-platform Supabase project.
-- The six tables already exist there; this migration documents and recreates them on a fresh project.
create extension if not exists pgcrypto;

create table if not exists public.aura_users (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

create table if not exists public.aura_assessments (
  analysis_id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.aura_users(user_id) on delete cascade,
  questionnaire_answers jsonb not null check (jsonb_typeof(questionnaire_answers) = 'array' and jsonb_array_length(questionnaire_answers) = 10),
  risk_score numeric not null check (risk_score between 0 and 100),
  risk_category text not null check (length(btrim(risk_category)) > 0),
  suitability_status text not null check (suitability_status in ('not_assessed','suitable','review_required','unsuitable')),
  suitability_reason text,
  methodology_version text,
  created_at timestamptz not null default now(),
  unique (analysis_id, user_id)
);

create table if not exists public.aura_recommendations (
  analysis_id uuid primary key,
  user_id uuid not null,
  stocks_snapshot jsonb not null check (jsonb_typeof(stocks_snapshot) = 'array' and jsonb_array_length(stocks_snapshot) = 15),
  screening_criteria jsonb not null default '{}'::jsonb check (jsonb_typeof(screening_criteria) = 'object'),
  market_data_as_of timestamptz,
  methodology_version text,
  created_at timestamptz not null default now(),
  unique (analysis_id, user_id),
  foreign key (analysis_id, user_id) references public.aura_assessments(analysis_id, user_id) on delete cascade
);

create table if not exists public.aura_portfolios (
  analysis_id uuid primary key,
  user_id uuid not null,
  positions jsonb not null check (jsonb_typeof(positions) = 'array' and jsonb_array_length(positions) between 3 and 15),
  risk_metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(risk_metrics) = 'object'),
  optimization_inputs jsonb not null default '{}'::jsonb check (jsonb_typeof(optimization_inputs) = 'object'),
  market_data_as_of timestamptz,
  methodology_version text,
  created_at timestamptz not null default now(),
  unique (analysis_id, user_id),
  foreign key (analysis_id, user_id) references public.aura_assessments(analysis_id, user_id) on delete cascade
);

create table if not exists public.aura_reports (
  report_id uuid primary key default gen_random_uuid(),
  analysis_id uuid not null unique,
  user_id uuid not null,
  status text not null default 'pending' check (status in ('pending','processing','completed','failed')),
  storage_bucket text not null default 'aura-reports' check (storage_bucket = 'aura-reports'),
  storage_path text,
  error_code text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  failed_at timestamptz,
  unique (analysis_id, user_id),
  foreign key (analysis_id, user_id) references public.aura_portfolios(analysis_id, user_id) on delete cascade,
  check (storage_path is null or (storage_path like user_id::text || '/' || analysis_id::text || '/%' and position('..' in storage_path) = 0)),
  check (status <> 'completed' or (storage_path is not null and completed_at is not null)),
  check (status = 'completed' or completed_at is null),
  check (status <> 'failed' or failed_at is not null)
);

create table if not exists public.aura_activity_logs (
  event_id uuid primary key default gen_random_uuid(),
  analysis_id uuid not null,
  user_id uuid not null,
  event_type text not null check (length(btrim(event_type)) > 0),
  event_data jsonb not null default '{}'::jsonb check (jsonb_typeof(event_data) = 'object'),
  occurred_at timestamptz not null default now(),
  foreign key (analysis_id, user_id) references public.aura_assessments(analysis_id, user_id) on delete cascade
);

create index if not exists aura_assessments_user_created_idx on public.aura_assessments(user_id, created_at desc);
create index if not exists aura_reports_user_created_idx on public.aura_reports(user_id, created_at desc);
create index if not exists aura_activity_logs_user_time_idx on public.aura_activity_logs(user_id, occurred_at desc);

-- Clients can read only their own records. All writes use the JWT-validated Python backend.
do $$ declare t text; begin
  foreach t in array array['aura_users','aura_assessments','aura_recommendations','aura_portfolios','aura_reports','aura_activity_logs'] loop
    execute format('alter table public.%I enable row level security', t);
    if not exists (select 1 from pg_policies where schemaname='public' and tablename=t and policyname='aura_select_own') then
      execute format('create policy aura_select_own on public.%I for select to authenticated using ((select auth.uid()) = user_id)', t);
    end if;
    execute format('grant select on public.%I to authenticated', t);
  end loop;
end $$;

insert into storage.buckets (id, name, public)
values ('aura-reports', 'aura-reports', false)
on conflict (id) do update set public = false;

do $$ begin
  if not exists (select 1 from pg_policies where schemaname='storage' and tablename='objects' and policyname='aura_read_completed_report') then
    create policy aura_read_completed_report on storage.objects for select to authenticated
    using (bucket_id = 'aura-reports' and exists (
      select 1 from public.aura_reports r
      where r.user_id = (select auth.uid()) and r.status = 'completed'
        and r.storage_bucket = storage.objects.bucket_id
        and r.storage_path = storage.objects.name
    ));
  end if;
end $$;
