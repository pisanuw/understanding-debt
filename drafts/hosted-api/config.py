"""Settings from the environment (`.env.example` lists them with placeholders)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    database_url: str = "sqlite:///udebt-hosted.db"
    jwt_secret: str = ""
    dev_auth: bool = False  # POST /auth/dev mints a session for any login: local only
    app_origins: list[str] = field(default_factory=list)  # the SPA's origins, for CORS
    app_url: str = ""  # where /auth/github/callback sends the browser back
    api_url: str = ""  # this API's public URL (the OAuth callback is under it)
    github_client_id: str = ""
    github_client_secret: str = ""
    github_webhook_secret: str = ""
    github_app_id: str = ""
    github_app_private_key: str = ""  # PEM; for check runs and installation clone tokens
    work_dir: str = ""  # where the worker clones (a temp dir per job)
    pg_rls: bool = True  # Postgres reads run as `authenticated` with the caller's claims
    session_hours: int = 12
    ack_label: str = "udebt-acknowledged"

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ.get
        return cls(
            database_url=e("DATABASE_URL", cls.database_url),
            jwt_secret=e("UDEBT_JWT_SECRET", ""),
            dev_auth=_flag("UDEBT_DEV_AUTH"),
            app_origins=[o.strip() for o in e("UDEBT_APP_ORIGINS", "").split(",") if o.strip()],
            app_url=e("UDEBT_APP_URL", ""),
            api_url=e("UDEBT_API_URL", ""),
            github_client_id=e("GITHUB_CLIENT_ID", ""),
            github_client_secret=e("GITHUB_CLIENT_SECRET", ""),
            github_webhook_secret=e("GITHUB_WEBHOOK_SECRET", ""),
            github_app_id=e("GITHUB_APP_ID", ""),
            github_app_private_key=e("GITHUB_APP_PRIVATE_KEY", "").replace("\\n", "\n"),
            work_dir=e("UDEBT_WORK_DIR", ""),
            pg_rls=_flag("UDEBT_PG_RLS", True),
            session_hours=int(e("UDEBT_SESSION_HOURS", "12")),
            ack_label=e("UDEBT_ACK_LABEL", "udebt-acknowledged"),
        )

    def check(self) -> None:
        if len(self.jwt_secret) < 16:
            raise SystemExit("UDEBT_JWT_SECRET must be set (at least 16 characters).")
