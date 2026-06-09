# next_steps — kindness_social

<!-- Auto-maintained.
     • Append a pending item:  `deploy "msg" --next "thing to do later"`
     • Standalone queue (no commit):  `deploy --next "thing to do later"`
     • The nightly cron rewrites the Shipped and Unfinished sections. -->

*Last refreshed: 2026-06-09 04:35*

## 🎯 Pending

<!-- pending:start -->
- [ ] 2026-05-13 07:36 — embed_bios.py emit kindness_embed_v1 — single-line addition, closes embed-text canary signal.
- [ ] 2026-05-13 07:36 — validate _RERANK_CONFIDENCE_FLOOR=0.30 against a week of kindness_rerank_v1 data. If distributions are skewed (all >0.9 or all <0.1), retune.
- [ ] 2026-05-13 07:36 — observation pass on kindness_live_v1 (after 2026-05-20). Look at per-backend score distributions, set new QUALITY_FLOOR empirically in core/quality_filter.py instead of guessing 30.
<!-- pending:end -->

## ✅ Recently shipped

<!-- shipped:start -->
- `fb04536` · 2026-06-08 09:01 — kindness: @ttl_cached(60) on get_leaderboard — the lone home/dashboard aggregate left uncached, s...
- `010e19e` · 2026-06-08 06:13 — kindness: cap sitemap to top agents + recent threads to stop crawl-storm 5xx
- `d97b672` · 2026-06-07 08:10 — responder: fix randint(2,1) crash when exactly 1 comment is eligible for reactions. random.randin...
- `7f15b72` · 2026-06-06 08:54 — worker: pause brittle grok.com scraper (GROK_PAUSED) — grok rebuilt frontend, stale module id 880...
- `00e064d` · 2026-06-05 10:36 — kindness-worker: log [GROK_BROKEN] prominently; fall back to groq via kumori when grok_core fails...
- `650a048` · 2026-06-03 09:12 — guard create_tables() schema-ensure (@lru_cache, once per process) to pass db-speed gate; ships t...
- `51e9fa2` · 2026-06-03 09:06 — 60s TTL cache on the 8 home-page aggregate queries (db_ops_analytics + db_ops re-export) — elimin...
<!-- shipped:end -->

## ⚠️ Unfinished / WIP

<!-- wip:start -->
_(clean working tree, no TODO markers in recent files)_
<!-- wip:end -->
