# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-05-10 04:00*

## 🎯 Pending

<!-- pending:start -->
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `f088668` · 2026-05-08 17:17 — GA4 tag injection via shared utilities/gtag + new kumori /admin/audience dashboard (where applica...
- `338fabf` · 2026-05-08 16:15 — visitor_logging: 2% bot sample + skip noise paths + 90d TTL + SELECT 1 liveness probe (per db-spe...
- `819c260` · 2026-05-08 14:25 — robots.txt: append AI-training + SEO-scraper bot-block (allow Googlebot/Bingbot/citation engines)
- `c6942d8` · 2026-05-08 12:20 — add visitor_logging middleware → kumori_ops.visitor_log
- `3223134` · 2026-05-07 10:44 — fix /about page 30s statement_timeout: get_control_vs_treatment + get_experiment_raw_data both us...
- `e84385a` · 2026-05-07 10:31 — fix lingering pool exhaustion: get_db_connection now retries with exponential backoff (50ms→400ms...
- `6658ae6` · 2026-05-07 09:50 — fix tokens_in/tokens_out write path in kumori_free_llms (was dead since columns added — 0 of 947 ...
- `b18277c` · 2026-05-07 08:58 — metrics polish + cron-log honesty: drop dead by_tier panel from get_telemetry_summary (FREE_BACKE...
- `a376c82` · 2026-05-07 08:00 — telemetry repointed to kumori aggregate tables (kindness_llm_telemetry was retired in Apr 12 shar...
- `c3ddcec` · 2026-05-05 13:02 — multi-modality validation chain (Phase 9 minimum slice): persist avatar description + backend on ...
- `0a47fb3` · 2026-05-05 10:28 — lifecycle awareness across kindness_social: (1) get_backend_health() now JOINs kumori_llm_endpoin...
- `0e876ac` · 2026-05-05 10:19 — narrative refresh: /about now describes self-curating catalog (lifecycle ladder + scout + multi-m...
- `96f44b2` · 2026-05-05 10:12 — lifecycle dashboard: hide retired column behind toggle (348 retired endpoints was visually noisy)
- `f1ae695` · 2026-05-05 09:48 — lifecycle dashboard upgrade: 5 new sections from kumori_llm_endpoints (Modality Lanes, Investigat...
- `17d6591` · 2026-05-05 09:10 — sync kumori_free_llms: DB-driven BACKENDS with 5min refresh + bandit exploration (10% under-valid...
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
**1 file(s) with uncommitted changes:**
- ` M next_steps.md`

<!-- wip:end -->
