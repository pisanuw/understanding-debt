-- Row-level security implementing udebt's permission model (ROADMAP "Permission model").
--   owner   sees everything; assigns roles, sets visibility, opts the repo into soft blocks
--   lead    sees every developer; acknowledges soft blocks
--   member  sees their own rows, team aggregates, and everyone's when the owner opens it up
--           (repos.members_see_individuals)
--   viewer  sees aggregates only
-- The caller is `udebt_uid()`, the `sub` of the request's JWT claims, read exactly as
-- Supabase's auth.uid() reads it, so the same policies hold for the Supabase API and for the
-- udebt API server, which sets the claims per transaction (db.py). The service role (and
-- the table owner) bypasses RLS; the API server writes through it after checking in code.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;   -- Supabase has it; plain Postgres gets it here
    end if;
    begin
        execute format('grant authenticated to %I', current_user);  -- so the API can SET ROLE
    exception when others then null;
    end;
end $$;

create or replace function udebt_uid() returns uuid
language sql stable as $$
    select nullif(coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::json
                  ->> 'sub', '')::uuid
$$;

create or replace function udebt_org_role(p_org text) returns text
language sql stable security definer set search_path = public as $$
    select role from memberships where org_id = p_org and user_id = udebt_uid()
$$;

create or replace function udebt_repo_role(p_repo text) returns text
language sql stable security definer set search_path = public as $$
    select m.role from memberships m join repos r on r.org_id = m.org_id
    where r.id = p_repo and m.user_id = udebt_uid()
$$;

create or replace function udebt_my_dev(p_repo text) returns text
language sql stable security definer set search_path = public as $$
    select lower(m.dev_key) from memberships m join repos r on r.org_id = m.org_id
    where r.id = p_repo and m.user_id = udebt_uid()
$$;

-- may the caller see this developer's individual rows in this repository?
create or replace function udebt_sees_dev(p_repo text, p_dev text) returns boolean
language sql stable security definer set search_path = public as $$
    select coalesce((
        select case
            when m.role in ('owner', 'lead') then true
            when m.role = 'member' then
                (m.dev_key is not null and lower(m.dev_key) = lower(p_dev))
                or r.members_see_individuals
            else false
        end
        from memberships m join repos r on r.org_id = m.org_id
        where r.id = p_repo and m.user_id = udebt_uid()
    ), false)
$$;

-- team aggregates any member (viewers included) may read: the mean index per point and the
-- mean understanding per unit, over every developer, without naming anyone
create or replace function udebt_team_series(p_repo text)
returns table (t text, mean_index double precision, devs integer, und jsonb)
language sql stable security definer set search_path = public as $$
    with s as (
        select * from snapshots where repo_id = p_repo and udebt_repo_role(p_repo) is not null
    ),
    i as (
        select s.t, avg(s.idx) as mean_index, count(*)::integer as devs from s group by s.t
    ),
    u as (
        select s.t, e.key as unit, avg((e.value #>> '{}')::double precision) as v
        from s, jsonb_each(s.und) e
        where jsonb_typeof(e.value) = 'number'
        group by s.t, e.key
    )
    select i.t, i.mean_index, i.devs,
           coalesce((select jsonb_object_agg(u.unit, round(u.v::numeric, 3))
                     from u where u.t = i.t), '{}'::jsonb)
    from i order by i.t
$$;

alter table users enable row level security;
alter table api_tokens enable row level security;
alter table orgs enable row level security;
alter table memberships enable row level security;
alter table repos enable row level security;
alter table maps enable row level security;
alter table units enable row level security;
alter table snapshot_points enable row level security;
alter table snapshots enable row level security;
alter table evidence enable row level security;
alter table risk_events enable row level security;
alter table contributors enable row level security;
alter table heavy_commits enable row level security;
alter table responsibility enable row level security;
alter table jobs enable row level security;
alter table acks enable row level security;
alter table checks enable row level security;
alter table org_secrets enable row level security;   -- no policy: the service role only

drop policy if exists users_read on users;
create policy users_read on users for select to authenticated using (
    id = udebt_uid() or exists (
        select 1 from memberships a join memberships b on a.org_id = b.org_id
        where a.user_id = udebt_uid() and b.user_id = users.id)
);
drop policy if exists tokens_own on api_tokens;
create policy tokens_own on api_tokens for select to authenticated using (user_id = udebt_uid());

drop policy if exists orgs_read on orgs;
create policy orgs_read on orgs for select to authenticated using (udebt_org_role(id) is not null);

drop policy if exists memberships_read on memberships;
create policy memberships_read on memberships for select to authenticated
    using (udebt_org_role(org_id) is not null);
drop policy if exists memberships_owner on memberships;
create policy memberships_owner on memberships for all to authenticated
    using (udebt_org_role(org_id) = 'owner') with check (udebt_org_role(org_id) = 'owner');

drop policy if exists repos_read on repos;
create policy repos_read on repos for select to authenticated
    using (udebt_repo_role(id) is not null);
drop policy if exists repos_owner on repos;
create policy repos_owner on repos for update to authenticated
    using (udebt_repo_role(id) = 'owner') with check (udebt_repo_role(id) = 'owner');

-- shared, unnamed state: every role
drop policy if exists maps_read on maps;
create policy maps_read on maps for select to authenticated
    using (udebt_repo_role(repo_id) is not null);
drop policy if exists units_read on units;
create policy units_read on units for select to authenticated
    using (udebt_repo_role(repo_id) is not null);
drop policy if exists points_read on snapshot_points;
create policy points_read on snapshot_points for select to authenticated
    using (udebt_repo_role(repo_id) is not null);
drop policy if exists heavy_read on heavy_commits;
create policy heavy_read on heavy_commits for select to authenticated
    using (udebt_repo_role(repo_id) is not null);

-- one developer's rows: whoever may see that developer
drop policy if exists snapshots_read on snapshots;
create policy snapshots_read on snapshots for select to authenticated
    using (udebt_sees_dev(repo_id, dev_key));
drop policy if exists evidence_read on evidence;
create policy evidence_read on evidence for select to authenticated
    using (udebt_sees_dev(repo_id, dev_key));
drop policy if exists evidence_own_insert on evidence;
create policy evidence_own_insert on evidence for insert to authenticated with check (
    udebt_repo_role(repo_id) in ('owner', 'lead')
    or (udebt_repo_role(repo_id) = 'member' and udebt_my_dev(repo_id) = lower(dev_key))
);
drop policy if exists risk_read on risk_events;
create policy risk_read on risk_events for select to authenticated
    using (udebt_sees_dev(repo_id, dev_key));
drop policy if exists responsibility_read on responsibility;
create policy responsibility_read on responsibility for select to authenticated
    using (udebt_sees_dev(repo_id, dev_key));
-- a contributor with no developer key (an agent, a bot) is visible where individuals are
drop policy if exists contributors_read on contributors;
create policy contributors_read on contributors for select to authenticated
    using (udebt_sees_dev(repo_id, coalesce(dev_key, '*')));

-- operations: owners and leads
drop policy if exists jobs_read on jobs;
create policy jobs_read on jobs for select to authenticated
    using (udebt_repo_role(repo_id) in ('owner', 'lead'));
drop policy if exists acks_read on acks;
create policy acks_read on acks for select to authenticated
    using (udebt_repo_role(repo_id) in ('owner', 'lead'));
drop policy if exists checks_read on checks;
create policy checks_read on checks for select to authenticated
    using (udebt_repo_role(repo_id) in ('owner', 'lead'));

grant usage on schema public to authenticated;
grant select on users, api_tokens, orgs, memberships, repos, maps, units, snapshot_points,
    snapshots, evidence, risk_events, contributors, heavy_commits, responsibility, jobs, acks,
    checks to authenticated;
grant insert on evidence to authenticated;
grant insert, update, delete on memberships to authenticated;
grant update on repos to authenticated;
grant execute on function udebt_uid(), udebt_org_role(text), udebt_repo_role(text),
    udebt_my_dev(text), udebt_sees_dev(text, text), udebt_team_series(text) to authenticated;
