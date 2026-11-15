"""The hosted service (Phase 4a): an API over the export contract, with the permission model
enforced in SQL (Postgres row-level security, as on Supabase) and in code (permissions.py,
which is all SQLite has), a GitHub webhook and check-run builder, and a job runner that
analyses a transient clone and keeps only metadata.

Optional: `pip install udebt[hosted]` (Starlette, uvicorn, PyJWT, psycopg). The core CLI
never imports this package, and stays stdlib only. Entry point: `udebt-hosted` (cli.py).
"""
