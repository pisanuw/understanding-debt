-- udebt's gateway settings for the HintWise key. Run with the Supabase admin workflow:
-- `sql` with this file, after `migrate udebt` (0002 registers the row with the platform's
-- defaults; `on conflict do nothing` there leaves these numbers alone afterwards).
--   default_model: the cheaper of the gateway's two models (owner, 15 Nov 2026: "1 yes" to
--   the recommendation in HOSTING.md "Measured on the samples"). A two-question quiz sitting
--   is then about $0.035 and a five-question one about $0.08, estimated from the digitalme
--   fixture and the three samples' excerpt sizes, not yet measured on the gateway.
--   the app as a whole: $2 a day (the platform default, stated so it survives a default change);
--   one person across HintWise, through udebt: $0.25 a day (the platform default), about seven
--   short sittings on this model.
--   max_tokens stays at the platform default (4096): quiz answers run a few hundred to 1,500
--   output tokens. Analyses never go through the gateway (HOSTING.md decision 3).
-- Idempotent: re-running sets the same numbers. Revisit once `usage` shows real sittings.
insert into platform.llm_apps (slug, default_model, cap_day_usd, cap_user_day_usd)
values ('udebt', 'claude-sonnet-5-5', 2, 0.25)
on conflict (slug) do update set
  default_model = excluded.default_model,
  cap_day_usd = excluded.cap_day_usd,
  cap_user_day_usd = excluded.cap_user_day_usd;
select slug, enabled, default_model, allowed_models, cap_day_usd, cap_week_usd, cap_month_usd,
       cap_user_day_usd, max_tokens
  from platform.llm_apps where slug = 'udebt';
