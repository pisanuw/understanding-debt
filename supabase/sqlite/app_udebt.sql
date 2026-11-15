-- The app_udebt schema for SQLite, for tests and local runs: attach a database as `app_udebt`
-- first (`attach database 'x.db' as app_udebt`). The same tables and constraints as
-- ../migrations/0001_app_udebt.sql and 0002_hosted.sql together, in portable types: ids are
-- integers, uuids and timestamps text, JSON text, booleans integers. SQLite has no auth.users and no row-level
-- security: own-row access is the Postgres policies' job, and the tests check those against a
-- real Postgres (tests/test_schema.py, UDEBT_TEST_PG).

create table if not exists app_udebt.repositories (
    id integer primary key autoincrement,
    url text not null unique
        check (url glob 'https://github.com/?*/?*' and url not glob 'https://github.com/*/*/*'),
    name text not null,
    license text,
    is_sample integer not null default 0,
    added_by text,
    created_at text not null default CURRENT_TIMESTAMP
);

create table if not exists app_udebt.analyses (
    id integer primary key autoincrement,
    repo_id integer not null references repositories (id) on delete cascade,
    sha text not null,
    user_id text,
    contract integer not null,
    manifest text not null,
    export text,
    map text,
    specs text,
    storage_path text,
    created_at text not null default CURRENT_TIMESTAMP
);
drop index if exists app_udebt.analyses_key;
create unique index if not exists app_udebt.analyses_by_owner on analyses (repo_id, sha, user_id);

create table if not exists app_udebt.my_repositories (
    user_id text not null,
    repo_id integer not null references repositories (id) on delete cascade,
    added_at text not null default CURRENT_TIMESTAMP,
    primary key (user_id, repo_id)
);

create table if not exists app_udebt.records (
    id integer primary key autoincrement,
    user_id text not null,
    repo_id integer not null references repositories (id) on delete cascade,
    sha text,
    unit_id text not null,
    ts text not null default CURRENT_TIMESTAMP,
    level integer check (level between 1 and 5),
    verdict text not null check (verdict in ('correct', 'partial', 'wrong', 'unknown')),
    confidence integer check (confidence between 1 and 3),
    type text not null default 'quiz',
    source text not null default 'quiz',
    question text,
    revealed integer not null default 0,
    forced integer not null default 0,
    data text not null default '{}'
);
create index if not exists app_udebt.records_by_unit on records (user_id, repo_id, unit_id, ts);

create table if not exists app_udebt.quiz_sessions (
    id integer primary key autoincrement,
    user_id text not null,
    repo_id integer not null references repositories (id) on delete cascade,
    sha text,
    started_at text not null default CURRENT_TIMESTAMP,
    finished_at text,
    questions text not null default '[]'
);

create table if not exists app_udebt.jobs (
    id integer primary key autoincrement,
    user_id text not null,
    url text not null,
    sha text,
    status text not null default 'running'
        check (status in ('queued', 'running', 'done', 'failed')),
    message text,
    calls integer,
    created_at text not null default CURRENT_TIMESTAMP,
    finished_at text
);
