-- Run once on the existing AURA Supabase project after reviewing the target project.
-- Existing rows stay intact. Investment amounts live in the existing JSONB columns.
do $$
declare old_check record;
begin
  for old_check in
    select conname from pg_constraint
    where conrelid = 'public.aura_portfolios'::regclass
      and contype = 'c'
      and conname <> 'aura_portfolios_positions_3_to_15'
      and pg_get_constraintdef(oid) like '%jsonb_array_length(positions)%'
  loop
    execute format('alter table public.aura_portfolios drop constraint %I', old_check.conname);
  end loop;
end $$;

do $$ begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.aura_portfolios'::regclass
      and conname = 'aura_portfolios_positions_3_to_15'
  ) then
    alter table public.aura_portfolios
      add constraint aura_portfolios_positions_3_to_15
      check (jsonb_typeof(positions) = 'array'
             and jsonb_array_length(positions) between 3 and 15);
  end if;
end $$;
