-- Read-only checks. Run in the SQL Editor for the intended AURA Supabase project.
select current_database() as database_name;

select name, to_regclass('public.' || name) is not null as table_exists
from (values ('aura_users'), ('aura_assessments'), ('aura_recommendations'),
             ('aura_portfolios'), ('aura_reports'), ('aura_activity_logs')) as t(name);

select c.relname as table_name, c.relrowsecurity as rls_enabled
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname like 'aura_%'
order by c.relname;

select tablename, policyname, roles, cmd, qual
from pg_policies
where (schemaname = 'public' and tablename like 'aura_%')
   or (schemaname = 'storage' and tablename = 'objects'
       and policyname = 'aura_read_completed_report')
order by schemaname, tablename, policyname;

select id, public as publicly_readable
from storage.buckets
where id = 'aura-reports';

select conname, pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid = to_regclass('public.aura_portfolios')
  and contype = 'c'
order by conname;

-- Required columns for the app and report worker. Missing columns need a
-- schema migration before customers can use the deployed app.
with required(table_name, column_name) as (
  values
    ('aura_users', 'user_id'),
    ('aura_assessments', 'analysis_id'), ('aura_assessments', 'user_id'),
    ('aura_assessments', 'questionnaire_answers'), ('aura_assessments', 'risk_score'),
    ('aura_assessments', 'risk_category'), ('aura_assessments', 'suitability_status'),
    ('aura_assessments', 'created_at'),
    ('aura_recommendations', 'analysis_id'), ('aura_recommendations', 'user_id'),
    ('aura_recommendations', 'stocks_snapshot'), ('aura_recommendations', 'screening_criteria'),
    ('aura_portfolios', 'analysis_id'), ('aura_portfolios', 'user_id'),
    ('aura_portfolios', 'positions'), ('aura_portfolios', 'risk_metrics'),
    ('aura_portfolios', 'optimization_inputs'),
    ('aura_reports', 'report_id'), ('aura_reports', 'analysis_id'),
    ('aura_reports', 'user_id'), ('aura_reports', 'status'),
    ('aura_reports', 'storage_bucket'), ('aura_reports', 'storage_path'),
    ('aura_reports', 'created_at'), ('aura_reports', 'started_at'),
    ('aura_reports', 'completed_at'), ('aura_reports', 'failed_at'),
    ('aura_reports', 'error_code'),
    ('aura_activity_logs', 'analysis_id'), ('aura_activity_logs', 'user_id'),
    ('aura_activity_logs', 'event_type'), ('aura_activity_logs', 'event_data')
)
select r.table_name, r.column_name, c.column_name is not null as column_exists
from required r
left join information_schema.columns c
  on c.table_schema = 'public' and c.table_name = r.table_name
 and c.column_name = r.column_name
order by r.table_name, r.column_name;
