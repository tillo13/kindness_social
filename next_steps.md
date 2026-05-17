# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-05-17 04:00*

## 🎯 Pending

<!-- pending:start -->
- [ ] 2026-05-13 07:36 — embed_bios.py emit kindness_embed_v1 — single-line addition, closes embed-text canary signal.
- [ ] 2026-05-13 07:36 — validate _RERANK_CONFIDENCE_FLOOR=0.30 against a week of kindness_rerank_v1 data. If distributions are skewed (all >0.9 or all <0.1), retune.
- [ ] 2026-05-13 07:36 — observation pass on kindness_live_v1 (after 2026-05-20). Look at per-backend score distributions, set new QUALITY_FLOOR empirically in core/quality_filter.py instead of guessing 30.
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `fcef402` · 2026-05-13 07:19 — canary expansion: rerank in responder + weekly avatar-diversity cron.
- `a055c45` · 2026-05-13 06:24 — README: generalize tech-stack citations — no specific provider names.
- `8e75f63` · 2026-05-13 06:04 — agent_factory: weighted backend selection — bias toward under-represented.
- `ba78651` · 2026-05-13 05:45 — llm_registry_remote: hourly TTL refresh — new kumori backends auto-flow in
- `c3ebcda` · 2026-05-13 05:44 — llm_registry_remote: hourly TTL refresh — new kumori backends auto-flow in.
- `395a37a` · 2026-05-13 05:25 — responder: emit kindness_live_v1 quality samples to kumori on every reply. Persona-alignment comp...
- `42a26f3` · 2026-05-12 16:04 — quality-aware backend filter for new agent assignment — first kindness consumer of the kumori fre...
- `ee65f34` · 2026-05-12 14:13 — cron_admin_routes:1366 — dict_rows=True → dict_cursor=True. Same kwarg-mismatch caught in embed_b...
- `48d4a68` · 2026-05-12 13:57 — embed_bios.py: db_cursor kwarg fix — kindness's utilities/postgres_utils.db_cursor takes dict_cur...
- `f118712` · 2026-05-12 13:50 — agent bio embeddings via kumori free-LLM pool — first real consumer of the embed_text runtime shi...
- `e7df7cf` · 2026-05-12 10:59 — avatar: drop static-file fallback. GCS is the single source of truth. -193 seed files, simplified...
- `31cd906` · 2026-05-12 10:47 — backfill_avatars: upload-or-generate logic (uploaded the 45 seed-only agents to GCS)
- `9d5b610` · 2026-05-12 10:41 — dev: migrate backfill_avatars.py to kumori_api_client (post-migration)
- `8827c25` · 2026-05-12 10:29 — kindness: migrate off vendored kumori_free_llm → HTTP via kumori_api_client. Drops 9 shared_files...
- `67e2299` · 2026-05-10 20:05 — kindness: fix admin_system_status NameError on FALLBACK_ORDER — HTTP migration left a broken mult...
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
**1 file(s) with uncommitted changes:**
- ` M next_steps.md`

<!-- wip:end -->
