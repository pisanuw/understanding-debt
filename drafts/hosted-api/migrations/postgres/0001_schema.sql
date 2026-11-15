-- udebt hosted schema (Phase 4a), Postgres / Supabase. Metadata only: shas, paths, unit
-- descriptions, scores, question text; never source code or answer text.
-- Applied in order by `udebt-hosted migrate` (or the Supabase SQL editor / `supabase db push`).

create table if not exists users (
    id uuid primary key,                -- Supabase auth.users.id, or minted by udebt's own OAuth
    github_id bigint unique,
    login text unique not null,
    email text,
    name text,
    created_at timestamptz not null default now()
);

create table if not exists api_tokens (
    id text primary key,
    user_id uuid not null references users(id) on delete cascade,
    name text not null,
    token_hash text unique not null,    -- sha256 of the token; the token is shown once
    created_at timestamptz not null default now(),
    last_used timestamptz
);

create table if not exists orgs (
    id text primary key,
    slug text unique not null,
    name text not null,
    github_installation bigint,         -- the GitHub App installation, for clones and checks
    created_at timestamptz not null default now()
);

create table if not exists memberships (
    org_id text not null references orgs(id) on delete cascade,
    user_id uuid not null references users(id) on delete cascade,
    role text not null check (role in ('owner', 'lead', 'member', 'viewer')),
    dev_key text,                       -- the developer key (primary email) in udebt's data
    created_at timestamptz not null default now(),
    primary key (org_id, user_id)
);

create table if not exists repos (
    id text primary key,
    org_id text not null references orgs(id) on delete cascade,
    name text not null,
    github text,                        -- owner/name on GitHub, for webhooks
    clone_url text,
    default_branch text not null default 'main',
    members_see_individuals boolean not null default false,
    soft_block boolean not null default false,
    head text,
    imported_at timestamptz,
    settings jsonb not null default '{}'::jsonb,   -- the export's settings and fingerprint
    created_at timestamptz not null default now(),
    unique (org_id, name)
);

create table if not exists maps (
    repo_id text primary key references repos(id) on delete cascade,
    data jsonb not null,                -- contract.sanitize_map: no walkthroughs, no questions
    generated_at text,
    updated_at timestamptz not null default now()
);

create table if not exists units (
    repo_id text not null references repos(id) on delete cascade,
    unit_id text not null,
    parent_id text,
    name text not null,
    kind text,
    scope text not null default 'architectural',
    importance double precision,
    ai_share double precision,
    data jsonb not null default '{}'::jsonb,  -- per-developer you/understanding/depth/basis
    primary key (repo_id, unit_id)
);

create table if not exists snapshot_points (
    repo_id text not null references repos(id) on delete cascade,
    t text not null,
    sha text not null,
    commits integer not null,
    mass jsonb not null,
    units jsonb not null,
    primary key (repo_id, t)
);

create table if not exists snapshots (
    repo_id text not null references repos(id) on delete cascade,
    t text not null,
    dev_key text not null,
    idx double precision,
    und jsonb not null default '{}'::jsonb,
    debt jsonb not null default '{}'::jsonb,
    depth jsonb not null default '{}'::jsonb,
    quizzed jsonb not null default '[]'::jsonb,
    primary key (repo_id, t, dev_key)
);

create table if not exists evidence (
    repo_id text not null references repos(id) on delete cascade,
    key text not null,                  -- sha1(dev, ts, unit, question)[:20]: idempotent pushes
    dev_key text not null,
    ts text not null,
    unit_id text not null,
    level integer,
    forced boolean not null default false,
    type text not null default 'quiz',
    verdict text not null check (verdict in ('correct', 'partial', 'wrong', 'unknown')),
    confidence integer,
    evidence double precision,
    head text,
    question text,
    origin text not null default 'push',  -- push (from a repository) or hosted (POST /answer)
    created_at timestamptz not null default now(),
    primary key (repo_id, key)
);

create table if not exists risk_events (
    repo_id text not null references repos(id) on delete cascade,
    key text not null,                  -- sha:unit:dev
    sha text not null,
    unit_id text not null,
    dev_key text not null,
    level text not null,
    mass double precision not null,
    understanding double precision not null,
    data jsonb not null default '{}'::jsonb,
    primary key (repo_id, key)
);

create table if not exists contributors (
    repo_id text not null references repos(id) on delete cascade,
    key text not null,
    dev_key text,
    data jsonb not null,
    primary key (repo_id, key)
);

create table if not exists heavy_commits (
    repo_id text not null references repos(id) on delete cascade,
    sha text not null,
    data jsonb not null,
    primary key (repo_id, sha)
);

create table if not exists responsibility (
    repo_id text not null references repos(id) on delete cascade,
    dev_key text not null,
    units jsonb not null,
    primary key (repo_id, dev_key)
);

create table if not exists jobs (
    id text primary key,
    repo_id text not null references repos(id) on delete cascade,
    kind text not null check (kind in ('analyze', 'check')),
    payload jsonb not null default '{}'::jsonb,
    status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
    attempts integer not null default 0,
    error text,
    result jsonb,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz
);
create index if not exists jobs_queued on jobs (status, created_at);

create table if not exists acks (
    repo_id text not null references repos(id) on delete cascade,
    pr integer not null,
    head_sha text not null,
    user_id uuid references users(id) on delete set null,
    login text,
    via text not null,                  -- label, comment or api
    created_at timestamptz not null default now(),
    primary key (repo_id, pr, head_sha)
);

create table if not exists checks (
    repo_id text not null references repos(id) on delete cascade,
    pr integer not null,
    head_sha text not null,
    conclusion text not null,
    level text not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    primary key (repo_id, pr, head_sha)
);

create table if not exists org_secrets (
    org_id text primary key references orgs(id) on delete cascade,
    api_key_ciphertext text,            -- the organisation's model API key, encrypted (Phase 4b)
    updated_at timestamptz not null default now()
);
