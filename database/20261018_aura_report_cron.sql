-- Run in the original AURA Supabase project after deploying aura-report-worker.
-- Before running, create two Supabase Vault secrets:
--   aura_worker_token   = the same random token as Edge Function secret AURA_WORKER_TOKEN
--   aura_publishable_key = this project's publishable API key
-- No secret values belong in this SQL file or in GitHub.

create extension if not exists pg_cron;
create extension if not exists pg_net;

select cron.schedule(
  'aura-report-worker',
  '* * * * *',
  $job$
    select net.http_post(
      url := 'https://mlcqlxuiajwcorswwtdf.supabase.co/functions/v1/aura-report-worker',
      headers := jsonb_build_object(
        'Content-Type', 'application/json',
        'apikey', (select decrypted_secret from vault.decrypted_secrets
                   where name = 'aura_publishable_key'),
        'x-aura-worker-token', (select decrypted_secret from vault.decrypted_secrets
                                where name = 'aura_worker_token')
      ),
      body := '{}'::jsonb,
      timeout_milliseconds := 120000
    ) as request_id;
  $job$
);
