# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-05-05 04:10*

## 🎯 Pending

<!-- pending:start -->
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `fd73c2e` · 2026-05-04 18:00 — sync runtime: bulletproof canary + DB-driven shared_pool caps + probe accounting fixes from kumor...
- `5901cb4` · 2026-05-04 15:53 — sync kumori_free_llms: cluster-wide cap enforcement (DB-backed counter, shared_pool checks, probe...
- `f329a3b` · 2026-05-04 14:06 — audit dual-write to kumori_llm_endpoints: catalog audit cron now mirrors status transitions + new...
- `cf230bb` · 2026-05-04 12:52 — catalog audit gate: new backends from providers without canonical_status=active land in status=pe...
- `736d2f2` · 2026-05-04 11:10 — catalog audit: filter openrouter to :free model_ids only. /v1/models returns both free and paid S...
- `0d85174` · 2026-05-04 08:39 — wire avatar_generator to kumori image stats: persist real-traffic gen attempts to kumori_image_da...
- `ea3cb40` · 2026-05-01 13:35 — sync kumori_free_llms.py from canonical infra — picks up circuit breaker (cooldown_until / consec...
- `3f7cd6c` · 2026-04-29 23:18 — fix: db_cursor accepts commit kwarg so kumori_free_llms gating loads provider_limits
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
_(clean working tree, no TODO markers in recent files)_
<!-- wip:end -->
