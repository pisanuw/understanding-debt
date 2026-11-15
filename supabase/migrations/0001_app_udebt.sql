-- udebt on Supabase (Phase 4a): schema app_udebt. Idempotent: every statement may run again.
-- Never edit this file once applied; add a new migration instead (HOSTING.md, platform §5).
--
-- Two kinds of table:
--   shared, keyed by repository and commit: repositories, analyses. A sample repository's
--     analysis (user_id null) is readable by every signed-in person and written by no app
--     code: the platform's admin workflow loads it (`udebt worker sql`). A person's own
--     analysis is written with their token and readable by them only.
--   a person's own rows: my_repositories, records (quiz evidence), quiz_sessions. Each has
--     `user_id uuid not null default auth.uid() references auth.users on delete cascade`,
--     own-row policies, and grants to `authenticated` only; nothing to `anon`.
-- Apps never hold the service-role key: every write here is made with the person's token.
-- Metadata only: questions, verdicts, levels, confidences; never answer text. Code appears
-- only as walkthrough excerpts inside `analyses.specs`, for public repositories.
--
-- Design, not implemented yet (ROADMAP "Permission model"): teams sharing one repository.
--   app_udebt.teams (id, repo_id, name); app_udebt.team_members (team_id, user_id, role
--   check (role in ('owner', 'lead', 'member', 'viewer')), dev_key). A function
--   app_udebt.sees(team_id, user_id) returns true for an owner or lead, for a member looking
--   at themselves or at anyone when the team's members_see_individuals is set, and false for a
--   viewer; records gain a policy `using (user_id = auth.uid() or exists (select 1 from
--   app_udebt.team_members m where m.user_id = records.user_id and app_udebt.sees(m.team_id,
--   auth.uid())))`, and a security-definer function serves team means to every role without
--   naming anyone. Owners assign roles and visibility and opt the repository into soft
--   blocking of pull requests; leads acknowledge soft blocks. Until then, access is own-row.

create schema if not exists app_udebt;

create table if not exists app_udebt.repositories (
    id bigint generated always as identity primary key,
    url text not null unique,                 -- public URL, never credentials
    name text not null,
    license text,
    is_sample boolean not null default false,
    added_by uuid default auth.uid() references auth.users on delete set null,
    created_at timestamptz not null default now()
);

create table if not exists app_udebt.analyses (
    id bigint generated always as identity primary key,
    repo_id bigint not null references app_udebt.repositories on delete cascade,
    sha text not null,
    user_id uuid default auth.uid() references auth.users on delete cascade,  -- null: shared
    contract integer not null,                -- udebt/contract.py CONTRACT
    manifest jsonb not null,                  -- the worker's analysis.json
    export jsonb,                             -- contract `import`; null when kept in Storage
    map jsonb,                                -- contract.sanitize_map: no walkthroughs, no answers
    specs jsonb,                              -- units, orientations, tour, walkthroughs
    storage_path text,                        -- large artefacts in the app's private bucket
    created_at timestamptz not null default now(),
    unique nulls not distinct (repo_id, sha, user_id)
);

create table if not exists app_udebt.my_repositories (
    user_id uuid not null default auth.uid() references auth.users on delete cascade,
    repo_id bigint not null references app_udebt.repositories on delete cascade,
    added_at timestamptz not null default now(),
    primary key (user_id, repo_id)
);

create table if not exists app_udebt.records (
    id bigint generated always as identity primary key,
    user_id uuid not null default auth.uid() references auth.users on delete cascade,
    repo_id bigint not null references app_udebt.repositories on delete cascade,
    sha text,                                 -- the analysis the question came from
    unit_id text not null,
    ts timestamptz not null default now(),
    level smallint check (level between 1 and 5),
    verdict text not null check (verdict in ('correct', 'partial', 'wrong', 'unknown')),
    confidence smallint check (confidence between 1 and 3),
    type text not null default 'quiz',
    source text not null default 'quiz',
    question text,
    revealed boolean not null default false,
    forced boolean not null default false,
    data jsonb not null default '{}'::jsonb   -- variants, review state, right/missing/misconception
);
create index if not exists records_by_unit on app_udebt.records (user_id, repo_id, unit_id, ts);

create table if not exists app_udebt.quiz_sessions (
    id bigint generated always as identity primary key,
    user_id uuid not null default auth.uid() references auth.users on delete cascade,
    repo_id bigint not null references app_udebt.repositories on delete cascade,
    sha text,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    questions jsonb not null default '[]'::jsonb  -- as served: never reference answers
);

alter table app_udebt.repositories enable row level security;
alter table app_udebt.analyses enable row level security;
alter table app_udebt.my_repositories enable row level security;
alter table app_udebt.records enable row level security;
alter table app_udebt.quiz_sessions enable row level security;

drop policy if exists repositories_read on app_udebt.repositories;
create policy repositories_read on app_udebt.repositories for select to authenticated
    using (true);
drop policy if exists repositories_add on app_udebt.repositories;
create policy repositories_add on app_udebt.repositories for insert to authenticated
    with check (added_by = (select auth.uid()) and not is_sample);

drop policy if exists analyses_read on app_udebt.analyses;
create policy analyses_read on app_udebt.analyses for select to authenticated
    using (user_id is null or user_id = (select auth.uid()));
drop policy if exists analyses_own on app_udebt.analyses;
create policy analyses_own on app_udebt.analyses for insert to authenticated
    with check (user_id = (select auth.uid()));
drop policy if exists analyses_own_update on app_udebt.analyses;
create policy analyses_own_update on app_udebt.analyses for update to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
drop policy if exists analyses_own_delete on app_udebt.analyses;
create policy analyses_own_delete on app_udebt.analyses for delete to authenticated
    using (user_id = (select auth.uid()));

drop policy if exists my_repositories_own on app_udebt.my_repositories;
create policy my_repositories_own on app_udebt.my_repositories for all to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
drop policy if exists records_own on app_udebt.records;
create policy records_own on app_udebt.records for all to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
drop policy if exists quiz_sessions_own on app_udebt.quiz_sessions;
create policy quiz_sessions_own on app_udebt.quiz_sessions for all to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

grant usage on schema app_udebt to authenticated;
grant select, insert on app_udebt.repositories to authenticated;
grant select, insert, update, delete on app_udebt.analyses, app_udebt.my_repositories,
    app_udebt.records, app_udebt.quiz_sessions to authenticated;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        revoke all on schema app_udebt from anon;
        revoke all on all tables in schema app_udebt from anon;
    end if;
end $$;
