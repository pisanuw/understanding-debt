-- udebt on Supabase, Phase 4b (the HintWise app): what 0001_app_udebt.sql lacks. Idempotent:
-- every statement may run again. Never edit this file once applied; add a new migration.
--
-- 1. Shared analyses (owner, 2026-11-15): the app takes public GitHub repositories only, and an
--    analysis of one is shared with everyone signed in. A person's worker run writes it with
--    their token (user_id = them, so only they may change or delete it); everyone reads it.
--    When their account is deleted the analysis stays, unowned (user_id set null) like a
--    sample, because others rely on it; several unowned analyses of one commit may then
--    coexist (the unique key no longer treats nulls as equal).
-- 2. Repositories are public GitHub URLs: https://github.com/<owner>/<name>.
-- 3. jobs: a person's analysis runs on the Render worker, written with their token, so the
--    page can follow a run (own rows only).
-- 4. The app's row for the HintWise LLM gateway (caps are the owner's, set by the Plabs
--    session, never here).
--
-- The shared tables need SHARED_TABLES entries in claude-built-apps platform/supabase-admin.mjs
-- before check-schema passes: app_udebt.repositories and app_udebt.analyses (supabase/README.md).

-- 1. analyses: read by everyone signed in; written and changed by their owner only
drop policy if exists analyses_read on app_udebt.analyses;
create policy analyses_read on app_udebt.analyses for select to authenticated using (true);

alter table app_udebt.analyses drop constraint if exists analyses_user_id_fkey;
alter table app_udebt.analyses add constraint analyses_user_id_fkey
    foreign key (user_id) references auth.users (id) on delete set null;

alter table app_udebt.analyses drop constraint if exists analyses_repo_id_sha_user_id_key;
create unique index if not exists analyses_by_owner
    on app_udebt.analyses (repo_id, sha, user_id);

-- 2. public GitHub repositories only (rows from before this file are not re-checked)
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'repositories_github_url'
                   and conrelid = 'app_udebt.repositories'::regclass) then
        alter table app_udebt.repositories add constraint repositories_github_url check (
            url ~ '^https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$'
        ) not valid;
    end if;
end $$;

-- 3. a person's analysis jobs
create table if not exists app_udebt.jobs (
    id bigint generated always as identity primary key,
    user_id uuid not null default auth.uid() references auth.users on delete cascade,
    url text not null,
    sha text,
    status text not null default 'running'
        check (status in ('queued', 'running', 'done', 'failed')),
    message text,
    calls integer,
    created_at timestamptz not null default now(),
    finished_at timestamptz
);
create index if not exists jobs_by_user on app_udebt.jobs (user_id, created_at desc);
alter table app_udebt.jobs enable row level security;
drop policy if exists jobs_own on app_udebt.jobs;
create policy jobs_own on app_udebt.jobs for all to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
grant select, insert, update, delete on app_udebt.jobs to authenticated;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        revoke all on all tables in schema app_udebt from anon;
    end if;
end $$;

-- 4. the gateway row (on the platform's project; skipped where the platform schema is absent)
do $$
begin
    if to_regclass('platform.llm_apps') is not null then
        insert into platform.llm_apps (slug) values ('udebt') on conflict do nothing;
    end if;
end $$;
