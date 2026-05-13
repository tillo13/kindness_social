# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-05-13 04:00*

## 🎯 Pending

<!-- pending:start -->
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `42a26f3` · 2026-05-12 16:04 — quality-aware backend filter for new agent assignment — first kindness consumer of the kumori fre...
- `ee65f34` · 2026-05-12 14:13 — cron_admin_routes:1366 — dict_rows=True → dict_cursor=True. Same kwarg-mismatch caught in embed_b...
- `48d4a68` · 2026-05-12 13:57 — embed_bios.py: db_cursor kwarg fix — kindness's utilities/postgres_utils.db_cursor takes dict_cur...
- `f118712` · 2026-05-12 13:50 — agent bio embeddings via kumori free-LLM pool — first real consumer of the embed_text runtime shi...
- `e7df7cf` · 2026-05-12 10:59 — avatar: drop static-file fallback. GCS is the single source of truth. -193 seed files, simplified...
- `31cd906` · 2026-05-12 10:47 — backfill_avatars: upload-or-generate logic (uploaded the 45 seed-only agents to GCS)
- `9d5b610` · 2026-05-12 10:41 — dev: migrate backfill_avatars.py to kumori_api_client (post-migration)
- `8827c25` · 2026-05-12 10:29 — kindness: migrate off vendored kumori_free_llm → HTTP via kumori_api_client. Drops 9 shared_files...
- `67e2299` · 2026-05-10 20:05 — kindness: fix admin_system_status NameError on FALLBACK_ORDER — HTTP migration left a broken mult...
- `97b67de` · 2026-05-10 19:29 — kindness: pick up updated kumori_api_client llm_chat_eval(prompt, system) signature fix from kumo...
- `391f7fe` · 2026-05-10 17:39 — kindness_social HTTP migration: App Engine main process now calls kumori.ai/api/v1/llm/* via util...
- `f088668` · 2026-05-08 17:17 — GA4 tag injection via shared utilities/gtag + new kumori /admin/audience dashboard (where applica...
- `338fabf` · 2026-05-08 16:15 — visitor_logging: 2% bot sample + skip noise paths + 90d TTL + SELECT 1 liveness probe (per db-spe...
- `819c260` · 2026-05-08 14:25 — robots.txt: append AI-training + SEO-scraper bot-block (allow Googlebot/Bingbot/citation engines)
- `c6942d8` · 2026-05-08 12:20 — add visitor_logging middleware → kumori_ops.visitor_log
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
**2 file(s) with uncommitted changes:**
- ` M utilities/kumori_api_client/__init__.py`
- ` M utilities/kumori_api_client/client.py`

<!-- wip:end -->
