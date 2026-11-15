-- udebt hosted schema (Phase 4a), SQLite: the Postgres schema with portable types, for local
-- runs and tests. No row-level security here: permissions are enforced in code (permissions.py).
-- Metadata only: shas, paths, unit
-- descriptions, scores, question text; never source code or answer text.
-- Applied in order by `udebt-hosted migrate` (or the Supabase SQL editor / `supabase db push`).

create table if not exists users (
    id text primary key,                -- Supabase auth.users.id, or minted by udebt's own OAuth
    github_id integer unique,
    login text unique not null,
    email text,
    name text,
    created_at text not null default CURRENT_TIMESTAMP
);

create table if not exists api_tokens (
    id text primary key,
    user_id text not null references users(id) on delete cascade,
    name text not null,
    token_hash text unique not null,    -- sha256 of the token; the token is shown once
    created_at text not null default CURRENT_TIMESTAMP,
    last_used text
);

create table if not exists orgs (
    id text primary key,
    slug text unique not null,
    name text not null,
    github_installation integer,         -- the GitHub App installation, for clones and checks
    created_at text not null default CURRENT_TIMESTAMP
);

create table if not exists memberships (
    org_id text not null references orgs(id) on delete cascade,
    user_id text not null references users(id) on delete cascade,
    role text not null check (role in ('owner', 'lead', 'member', 'viewer')),
    dev_key text,                       -- the developer key (primary email) in udebt's data
    created_at text not null default CURRENT_TIMESTAMP,
    primary key (org_id, user_id)
);

create table if not exists repos (
    id text primary key,
    org_id text not null references orgs(id) on delete cascade,
    name text not null,
    github text,                        -- owner/name on GitHub, for webhooks
    clone_url text,
    default_branch text not null default 'main',
    members_see_individuals integer not null default 0,
    soft_block integer not null default 0,
    head text,
    imported_at text,
    settings text not null default '{}',   -- the export's settings and fingerprint
    created_at text not null default CURRENT_TIMESTAMP,
    unique (org_id, name)
);

create table if not exists maps (
    repo_id text primary key references repos(id) on delete cascade,
    data text not null,                -- contract.sanitize_map: no walkthroughs, no questions
    generated_at text,
    updated_at text not null default CURRENT_TIMESTAMP
);

create table if not exists units (
    repo_id text not null references repos(id) on delete cascade,
    unit_id text not null,
    parent_id text,
    name text not null,
    kind text,
    scope text not null default 'architectural',
    importance real,
    ai_share real,
    data text not null default '{}',  -- per-developer you/understanding/depth/basis
    primary key (repo_id, unit_id)
);

create table if not exists snapshot_points (
    repo_id text not null references repos(id) on delete cascade,
    t text not null,
    sha text not null,
    commits integer not null,
    mass text not null,
    units text not null,
    primary key (repo_id, t)
);

create table if not exists snapshots (
    repo_id text not null references repos(id) on delete cascade,
    t text not null,
    dev_key text not null,
    idx real,
    und text not null default '{}',
    debt text not null default '{}',
    depth text not null default '{}',
    quizzed text not null default '[]',
    primary key (repo_id, t, dev_key)
);

create table if not exists evidence (
    repo_id text not null references repos(id) on delete cascade,
    key text not null,                  -- sha1(dev, ts, unit, question)[:20]: idempotent pushes
    dev_key text not null,
    ts text not null,
    unit_id text not null,
    level integer,
    forced integer not null default 0,
    type text not null default 'quiz',
    verdict text not null check (verdict in ('correct', 'partial', 'wrong', 'unknown')),
    confidence integer,
    evidence real,
    head text,
    question text,
    origin text not null default 'push',  -- push (from a repository) or hosted (POST /answer)
    created_at text not null default CURRENT_TIMESTAMP,
    primary key (repo_id, key)
);

create table if not exists risk_events (
    repo_id text not null references repos(id) on delete cascade,
    key text not null,                  -- sha:unit:dev
    sha text not null,
    unit_id text not null,
    dev_key text not null,
    level text not null,
    mass real not null,
    understanding real not null,
    data text not null default '{}',
    primary key (repo_id, key)
);

create table if not exists contributors (
    repo_id text not null references repos(id) on delete cascade,
    key text not null,
    dev_key text,
    data text not null,
    primary key (repo_id, key)
);

create table if not exists heavy_commits (
    repo_id text not null references repos(id) on delete cascade,
    sha text not null,
    data text not null,
    primary key (repo_id, sha)
);

create table if not exists responsibility (
    repo_id text not null references repos(id) on delete cascade,
    dev_key text not null,
    units text not null,
    primary key (repo_id, dev_key)
);

create table if not exists jobs (
    id text primary key,
    repo_id text not null references repos(id) on delete cascade,
    kind text not null check (kind in ('analyze', 'check')),
    payload text not null default '{}',
    status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
    attempts integer not null default 0,
    error text,
    result text,
    created_at text not null default CURRENT_TIMESTAMP,
    started_at text,
    finished_at text
);
create index if not exists jobs_queued on jobs (status, created_at);

create table if not exists acks (
    repo_id text not null references repos(id) on delete cascade,
    pr integer not null,
    head_sha text not null,
    user_id text references users(id) on delete set null,
    login text,
    via text not null,                  -- label, comment or api
    created_at text not null default CURRENT_TIMESTAMP,
    primary key (repo_id, pr, head_sha)
);

create table if not exists checks (
    repo_id text not null references repos(id) on delete cascade,
    pr integer not null,
    head_sha text not null,
    conclusion text not null,
    level text not null,
    payload text not null,
    created_at text not null default CURRENT_TIMESTAMP,
    primary key (repo_id, pr, head_sha)
);

create table if not exists org_secrets (
    org_id text primary key references orgs(id) on delete cascade,
    api_key_ciphertext text,            -- the organisation's model API key, encrypted (Phase 4b)
    updated_at text not null default CURRENT_TIMESTAMP
);
